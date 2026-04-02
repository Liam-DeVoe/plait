from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from server import db, git
from server.models import (
    Cell,
    CellStatus,
    CIStatus,
    Session,
    SessionRole,
    Sortie,
    SyncStatus,
)
from server.sessions import daemon_sortie_cmd, spawn_session, tend_cmd

logger = logging.getLogger(__name__)

# Will be set by the API server to broadcast updates
notify_callback: asyncio.Queue | None = None

POLL_INTERVAL = 300  # seconds (5 minutes)

# Per-cell locks to prevent concurrent process_cell execution (e.g. daemon
# daemon run overlapping with an API-triggered sync).
_cell_locks: dict[str, asyncio.Lock] = {}

# In-flight daemon actions, keyed by (cell_id, trigger). Prevents the daemon
# from spawning duplicate sessions for the same trigger on the same cell.
# Tracked in memory so user-side actions (e.g. resuming a session) don't
# interfere with daemon concurrency control.
_in_flight: set[tuple[str, str]] = set()


async def notify(event: str, data: dict) -> None:
    if notify_callback is not None:
        await notify_callback.put({"event": event, "data": data})


async def _push_if_published(cell: Cell) -> bool | None:
    """Push to origin if the branch has been published (exists on remote).

    Returns True if pushed successfully, False if push failed,
    or None if the branch is local-only (no push attempted).
    """
    if not await git.has_remote_branch(cell.worktree_path, cell.branch):
        return None
    ok, _ = await git.push(cell.worktree_path, cell.branch)
    return ok


async def process_cell(cell: Cell) -> dict | None:
    """Process a single cell: check sync status, CI status, and fix issues.

    Returns a result dict for daemon run tracking, or None if the cell
    was already being processed.
    """
    lock = _cell_locks.setdefault(cell.id, asyncio.Lock())
    if lock.locked():
        logger.info(f"Cell {cell.id} already being processed, skipping")
        return None
    async with lock:
        return await _process_cell(cell)


async def _process_cell(cell: Cell) -> dict:
    reasons: list[str] = []
    decision = "idle"
    outcome = None

    try:
        await git.assert_not_detached(cell.worktree_path)

        # Fetch latest from origin
        await git.fetch_origin(cell.repo)

        # --- Attempt automatic merge if behind main ---
        has_conflicts = False
        behind = await git.is_behind_main(cell.worktree_path)
        if not behind and cell.sync_status != SyncStatus.current:
            await db.update_cell(cell.id, sync_status=SyncStatus.current)
            await notify("cell_updated", {"id": cell.id, "sync_status": "current"})
        if behind:
            reasons.append("behind_main")
            logger.info(
                f"Cell {cell.id} ({cell.repo}:{cell.branch}) is behind main, merging"
            )
            await db.update_cell(cell.id, sync_status=SyncStatus.syncing)
            await notify("cell_updated", {"id": cell.id, "sync_status": "syncing"})

            success, _output = await git.merge_from_main(cell.worktree_path)

            if success:
                # Clean merge — push only if branch is already on remote
                push_result = await _push_if_published(cell)
                if push_result is False:
                    await db.update_cell(cell.id, sync_status=SyncStatus.failed)
                    await notify(
                        "cell_updated", {"id": cell.id, "sync_status": "failed"}
                    )
                    logger.error(f"Cell {cell.id} push failed after merge")
                else:
                    await db.update_cell(cell.id, sync_status=SyncStatus.current)
                    await notify(
                        "cell_updated", {"id": cell.id, "sync_status": "current"}
                    )
                    logger.info(f"Cell {cell.id} merged successfully")
            else:
                has_conflicts = True
                reasons.append("conflicts")
                await db.update_cell(cell.id, sync_status=SyncStatus.conflict)
                await notify("cell_updated", {"id": cell.id, "sync_status": "conflict"})

        # --- Poll PR state ---
        needs_tend = has_conflicts or behind
        if cell.pr_number:
            ci = await git.get_ci_status(cell.repo, cell.pr_number)
            ci_status = CIStatus(ci)
            if ci_status != cell.ci_status:
                await db.update_cell(cell.id, ci_status=ci_status)
                await notify("cell_updated", {"id": cell.id, "ci_status": ci})
            if ci_status == CIStatus.failing:
                if "ci_failing" not in reasons:
                    reasons.append("ci_failing")
                needs_tend = True

            comment_count = await git.get_pr_comment_count(cell.repo, cell.pr_number)
            if comment_count != cell.pr_comment_count:
                await db.update_cell(cell.id, pr_comment_count=comment_count)
                reasons.append("new_comments")
                needs_tend = True

            reaction_count = await git.get_pr_reaction_count(cell.repo, cell.pr_number)
            if reaction_count != cell.pr_reaction_count:
                await db.update_cell(cell.id, pr_reaction_count=reaction_count)
                reasons.append("new_reactions")
                needs_tend = True

        # --- Spawn a tend session if anything changed ---
        key = (cell.id, "tend")
        if needs_tend and key not in _in_flight:
            decision = "tended"
            logger.info(f"Cell {cell.id} needs fixing, invoking Claude")
            _in_flight.add(key)
            try:
                session = Session(
                    cell_id=cell.id,
                    role=SessionRole.daemon,
                    trigger="tend",
                )
                await db.create_session(session)

                cmd, cwd = tend_cmd(session.id, cell)
                task = spawn_session(session.id, cmd, cwd)
                exit_code = await task
                ok = exit_code == 0
                outcome = "succeeded" if ok else "failed"

                if ok:
                    push_result = await _push_if_published(cell)
                    sync = (
                        SyncStatus.current
                        if push_result is not False
                        else SyncStatus.failed
                    )
                else:
                    sync = SyncStatus.failed

                await db.update_cell(cell.id, sync_status=sync)
                await notify("cell_updated", {"id": cell.id, "sync_status": sync.value})

                await db.update_session(session.id, succeeded=1 if ok else 0)
                if not ok:
                    logger.error(f"Claude failed to fix issues for cell {cell.id}")
            finally:
                _in_flight.discard(key)
        elif needs_tend:
            decision = "skipped"

        # --- Detect PR for cells that have been pushed but have no PR yet ---
        if not cell.pr_number and await git.has_remote_branch(
            cell.worktree_path, cell.branch
        ):
            pr_info = await git.find_pr_for_branch(cell.repo, cell.branch)
            if pr_info:
                await db.update_cell(
                    cell.id,
                    pr_number=pr_info["number"],
                    pr_url=pr_info["url"],
                )
                await notify("cell_updated", {"id": cell.id})

    except Exception:
        decision = "error"
        logger.exception(f"Error processing cell {cell.id}")

    return {
        "cell_id": cell.id,
        "repo": cell.repo,
        "branch": cell.branch,
        "decision": decision,
        "reasons": reasons,
        "outcome": outcome,
    }


async def daemon_loop() -> None:
    """Main daemon loop. Runs forever, processing all active cells periodically."""
    logger.info("Daemon started")
    while True:
        try:
            cells = await db.list_cells(status=CellStatus.active)
            logger.info(f"Daemon run: processing {len(cells)} active cells")

            started_at = datetime.now(timezone.utc).isoformat()
            results = []
            for cell in cells:
                result = await process_cell(cell)
                if result is not None:
                    results.append(result)

            ended_at = datetime.now(timezone.utc).isoformat()
            run_id = str(uuid.uuid4())
            await db.create_daemon_run(run_id, started_at, ended_at, results)

        except Exception:
            logger.exception("Daemon loop error")

        await asyncio.sleep(POLL_INTERVAL)


async def spawn_sortie_session(sortie: Sortie) -> None:
    """Spawn the single orchestrator Claude session for a sortie."""
    try:
        repo_worktrees = await git.create_sortie_worktrees(sortie.id)
    except RuntimeError:
        logger.exception(f"Failed to create sortie worktrees for {sortie.id}")
        return

    exploration_dir = str(git.WORKTREE_ROOT / f"sortie-{sortie.id}")

    session = Session(
        sortie_id=sortie.id,
        role=SessionRole.daemon,
        trigger="sortie",
    )
    await db.create_session(session)
    await db.update_sortie(sortie.id, session_id=session.id)
    await notify("sortie_updated", {"id": sortie.id})

    cmd, cwd = daemon_sortie_cmd(session.id, sortie, exploration_dir, repo_worktrees)
    task = spawn_session(session.id, cmd, cwd)
    exit_code = await task
    ok = exit_code == 0

    await db.update_session(session.id, succeeded=1 if ok else 0)

    # Clean up exploration worktrees (cell worktrees persist)
    try:
        await git.remove_sortie_worktrees(sortie.id)
    except Exception:
        logger.exception(f"Failed to clean up sortie worktrees for {sortie.id}")

    await notify("sortie_updated", {"id": sortie.id})
