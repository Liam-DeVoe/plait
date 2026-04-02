from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from server import claude, db, git
from server.models import (
    Cell,
    CellStatus,
    CIStatus,
    Session,
    SessionRole,
    SessionStatus,
    Sortie,
    SyncStatus,
)

logger = logging.getLogger(__name__)

# Will be set by the API server to broadcast updates
notify_callback: asyncio.Queue | None = None

POLL_INTERVAL = 300  # seconds (5 minutes)
MAX_DAEMON_ATTEMPTS = 3


async def notify(event: str, data: dict) -> None:
    if notify_callback is not None:
        await notify_callback.put({"event": event, "data": data})


def _make_output_callback(session_id: str, cell_id: str):
    """Create a throttled callback for streaming Claude output to DB + WebSocket."""
    last_update = 0.0

    async def callback(transcript: str) -> None:
        nonlocal last_update
        now = time.monotonic()
        if now - last_update < 1.0:
            return
        last_update = now
        await db.update_session(session_id, transcript=transcript)
        await notify("session_output", {"session_id": session_id, "cell_id": cell_id})

    return callback


async def _should_attempt(cell_id: str, trigger: str, *, force: bool = False) -> bool:
    """Check if we should attempt a daemon action on this cell.

    Always blocks if a session with the same trigger is already running.
    Blocks after MAX_DAEMON_ATTEMPTS failures unless force=True (manual trigger).
    """
    sessions = await db.list_sessions(cell_id)
    trigger_sessions = [s for s in sessions if s.trigger == trigger]

    # Never start if one is already running
    if any(s.status == SessionStatus.running for s in trigger_sessions):
        return False

    # Skip retry limit if forced (manual trigger)
    if force:
        return True

    failed = sum(1 for s in trigger_sessions if s.status == SessionStatus.failed)
    return failed < MAX_DAEMON_ATTEMPTS


async def process_cell(cell: Cell, *, force: bool = False) -> None:
    """Process a single cell: check sync status, CI status."""
    try:
        # Fetch latest from origin
        await git.fetch_origin(cell.repo)

        # Check if behind main
        if await git.is_behind_main(cell.worktree_path):
            logger.info(
                f"Cell {cell.id} ({cell.repo}:{cell.branch}) is behind main, merging"
            )
            await db.update_cell(cell.id, sync_status=SyncStatus.syncing)
            await notify("cell_updated", {"id": cell.id, "sync_status": "syncing"})

            success, output = await git.merge_from_main(cell.worktree_path)

            if success:
                # Clean merge, push
                push_ok, push_out = await git.push(cell.worktree_path, cell.branch)
                if push_ok:
                    await db.update_cell(cell.id, sync_status=SyncStatus.current)
                    await notify(
                        "cell_updated", {"id": cell.id, "sync_status": "current"}
                    )
                    logger.info(f"Cell {cell.id} merged and pushed successfully")
                else:
                    await db.update_cell(cell.id, sync_status=SyncStatus.failed)
                    await notify(
                        "cell_updated", {"id": cell.id, "sync_status": "failed"}
                    )
                    logger.error(f"Cell {cell.id} push failed: {push_out}")
            else:
                # Conflicts — use Claude to resolve
                await db.update_cell(cell.id, sync_status=SyncStatus.conflict)
                await notify("cell_updated", {"id": cell.id, "sync_status": "conflict"})

                if not await _should_attempt(cell.id, "merge", force=force):
                    logger.info(
                        f"Cell {cell.id}: skipping merge (limit reached or in progress)"
                    )
                    return

                logger.info(f"Cell {cell.id} has conflicts, invoking Claude")

                # Create a daemon session record
                session = Session(
                    cell_id=cell.id,
                    role=SessionRole.daemon,
                    trigger="merge",
                    status=SessionStatus.running,
                )
                await db.create_session(session)

                on_output = _make_output_callback(session.id, cell.id)
                claude_ok, claude_out = await claude.resolve_conflicts(
                    cell.worktree_path, cell.branch, on_output=on_output
                )

                if claude_ok:
                    push_ok, push_out = await git.push(cell.worktree_path, cell.branch)
                    if push_ok:
                        await db.update_cell(cell.id, sync_status=SyncStatus.current)
                        await notify(
                            "cell_updated", {"id": cell.id, "sync_status": "current"}
                        )
                    else:
                        await db.update_cell(cell.id, sync_status=SyncStatus.failed)
                        await notify(
                            "cell_updated", {"id": cell.id, "sync_status": "failed"}
                        )

                    await db.update_session(
                        session.id,
                        status=SessionStatus.completed.value,
                        transcript=claude_out,
                        ended_at=datetime.now(timezone.utc).isoformat(),
                    )
                else:
                    await db.update_cell(cell.id, sync_status=SyncStatus.failed)
                    await notify(
                        "cell_updated", {"id": cell.id, "sync_status": "failed"}
                    )
                    await db.update_session(
                        session.id,
                        status=SessionStatus.failed.value,
                        transcript=claude_out,
                        ended_at=datetime.now(timezone.utc).isoformat(),
                    )
                    logger.error(
                        f"Claude failed to resolve conflicts for cell {cell.id}"
                    )

        # Check CI status if cell has a PR
        if cell.pr_number:
            ci = await git.get_ci_status(cell.repo, cell.pr_number)
            ci_status = CIStatus(ci)
            if ci_status != cell.ci_status:
                await db.update_cell(cell.id, ci_status=ci_status)
                await notify("cell_updated", {"id": cell.id, "ci_status": ci})

                # If CI just started failing, try to fix
                if ci_status == CIStatus.failing:
                    await _fix_ci(cell, force=force)

    except Exception:
        logger.exception(f"Error processing cell {cell.id}")


async def daemon_loop() -> None:
    """Main daemon loop. Runs forever, processing all active cells periodically."""
    logger.info("Daemon started")
    while True:
        try:
            cells = await db.list_cells(status=CellStatus.active)
            logger.info(f"Daemon tick: processing {len(cells)} active cells")

            for cell in cells:
                await process_cell(cell)

        except Exception:
            logger.exception("Daemon loop error")

        await asyncio.sleep(POLL_INTERVAL)


async def _fix_ci(cell: Cell, *, force: bool = False) -> None:
    """Spawn Claude to diagnose and fix a CI failure."""
    if not await _should_attempt(cell.id, "ci_fix", force=force):
        logger.info(f"Cell {cell.id}: skipping CI fix (limit reached or in progress)")
        return

    logger.info(f"Cell {cell.id} CI is failing, invoking Claude to fix")

    ci_logs = await git.get_ci_failure_logs(cell.repo, cell.branch)

    session = Session(
        cell_id=cell.id,
        role=SessionRole.daemon,
        trigger="ci_fix",
        status=SessionStatus.running,
    )
    await db.create_session(session)

    on_output = _make_output_callback(session.id, cell.id)
    ok, output = await claude.fix_ci(
        cell.worktree_path, cell.branch, ci_logs, on_output=on_output
    )
    ended_at = datetime.now(timezone.utc).isoformat()

    if ok:
        push_ok, _ = await git.push(cell.worktree_path, cell.branch)
        await db.update_session(
            session.id,
            status=SessionStatus.completed.value,
            transcript=output,
            ended_at=ended_at,
        )
        if push_ok:
            logger.info(f"Claude fixed CI for cell {cell.id}, pushed")
        await notify("cell_updated", {"id": cell.id})
    else:
        await db.update_session(
            session.id,
            status=SessionStatus.failed.value,
            transcript=output,
            ended_at=ended_at,
        )
        logger.error(f"Claude failed to fix CI for cell {cell.id}")


async def spawn_sortie_cell(sortie: Sortie, repo: str) -> None:
    """Create a cell for a sortie repo and run Claude with the sortie prompt."""
    branch = f"sortie/{sortie.id[:8]}"

    cell = Cell(
        sortie_id=sortie.id,
        repo=repo,
        branch=branch,
        worktree_path="",
    )

    try:
        cell.worktree_path = await git.create_worktree(repo, branch, cell.id)
    except RuntimeError:
        logger.exception(f"Failed to create worktree for sortie cell {repo}")
        return

    await db.create_cell(cell)
    await notify("cell_updated", {"id": cell.id, "status": "active"})

    session = Session(
        cell_id=cell.id,
        role=SessionRole.daemon,
        trigger="sortie",
        status=SessionStatus.running,
    )
    await db.create_session(session)

    on_output = _make_output_callback(session.id, cell.id)
    ok, output = await claude.run_claude_headless(
        sortie.prompt, cwd=cell.worktree_path, on_output=on_output
    )
    ended_at = datetime.now(timezone.utc).isoformat()

    if ok:
        # Push and create PR
        push_ok, _ = await git.push(cell.worktree_path, branch)
        if push_ok:
            try:
                pr_info = await git.create_pr(
                    cell.worktree_path,
                    repo,
                    title=sortie.prompt[:60],
                    body=sortie.prompt,
                )
                await db.update_cell(
                    cell.id,
                    pr_number=pr_info["number"],
                    pr_url=pr_info["url"],
                )
            except RuntimeError:
                logger.exception(f"Failed to create PR for sortie cell {cell.id}")

        await db.update_session(
            session.id,
            status=SessionStatus.completed.value,
            transcript=output,
            ended_at=ended_at,
        )
    else:
        await db.update_session(
            session.id,
            status=SessionStatus.failed.value,
            transcript=output,
            ended_at=ended_at,
        )

    await notify("cell_updated", {"id": cell.id})


async def run_user_session(cell: Cell, session: Session, prompt: str) -> None:
    """Run a user-initiated Claude session in a cell's worktree."""
    on_output = _make_output_callback(session.id, cell.id)
    ok, output = await claude.run_claude_headless(
        prompt, cwd=cell.worktree_path, on_output=on_output
    )
    ended_at = datetime.now(timezone.utc).isoformat()

    await db.update_session(
        session.id,
        status=SessionStatus.completed.value if ok else SessionStatus.failed.value,
        transcript=output,
        ended_at=ended_at,
    )
    await notify("cell_updated", {"id": cell.id})
