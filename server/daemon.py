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
    SyncStatus,
)
from server.sessions import spawn_session, tend_cmd

logger = logging.getLogger(__name__)

# Will be set by the API server to broadcast updates
notify_callback: asyncio.Queue | None = None

POLL_INTERVAL = 300  # seconds (5 minutes)
SESSION_IDLE_TIMEOUT = (
    300  # seconds — kill interactive sessions after 5 min of no output
)
PR_ACTIVITY_COOLDOWN = 300  # seconds — wait for PR activity to settle

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


async def _derive_sync_status(cell: Cell) -> None:
    """Derive sync status from remote refs and update DB if changed."""
    behind = await git.is_behind_main(cell.worktree_path, cell.branch)
    sync = SyncStatus.behind if behind else SyncStatus.current
    if sync != cell.sync_status:
        await db.update_cell(cell.id, sync_status=sync)
        await notify("cell_updated", {"id": cell.id, "sync_status": sync.value})
        cell.sync_status = sync


async def tend_cell(cell: Cell) -> bool:
    """Unconditionally spawn a tend session for a cell. Returns True on success."""
    session = Session(
        cell_id=cell.id,
        role=SessionRole.daemon,
        trigger="tend",
    )
    await db.create_session(session)
    await notify("cell_updated", {"id": cell.id})

    cmd, cwd, prompt = tend_cmd(session.id, cell)
    task = spawn_session(
        session.id, cmd, cwd, initial_input=prompt, idle_timeout=SESSION_IDLE_TIMEOUT
    )
    exit_code = await task
    ok = exit_code == 0

    if ok:
        await _push_if_published(cell)

    await db.update_session(session.id, succeeded=1 if ok else 0)
    await notify("cell_updated", {"id": cell.id})
    if not ok:
        logger.error(f"Claude failed to fix issues for cell {cell.id}")
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

        # --- Derive sync status from remote refs ---
        await _derive_sync_status(cell)

        # --- Attempt automatic merge if behind main ---
        has_conflicts = False
        if cell.sync_status == SyncStatus.behind:
            reasons.append("behind_main")
            logger.info(
                f"Cell {cell.id} ({cell.repo}:{cell.branch}) is behind main, merging"
            )

            success, _output = await git.merge_from_main(cell.worktree_path)

            if success:
                push_result = await _push_if_published(cell)
                if push_result is not False:
                    logger.info(f"Cell {cell.id} merged successfully")
                else:
                    logger.error(f"Cell {cell.id} push failed after merge")
            else:
                has_conflicts = True
                reasons.append("conflicts")

        # --- Auto-archive if PR merged/closed ---
        if cell.pr_number:
            pr_state = await git.get_pr_state(cell.repo, cell.pr_number)
            if pr_state in ("MERGED", "CLOSED"):
                logger.info(
                    f"Cell {cell.id} PR #{cell.pr_number} is {pr_state}, archiving"
                )
                try:
                    await git.remove_worktree(cell.repo, cell.worktree_path)
                except Exception:
                    logger.warning(f"Failed to remove worktree for cell {cell.id}")
                await db.update_cell(
                    cell.id,
                    status=CellStatus.archived,
                    archived_at=datetime.now(timezone.utc).isoformat(),
                )
                await notify("cell_updated", {"id": cell.id, "status": "archived"})
                return {
                    "cell_id": cell.id,
                    "repo": cell.repo,
                    "branch": cell.branch,
                    "decision": "archived",
                    "reasons": [f"pr_{pr_state.lower()}"],
                    "outcome": None,
                }

        # --- Poll PR state ---
        needs_tend = has_conflicts
        if cell.pr_number:
            ci = await git.get_ci_status(cell.repo, cell.pr_number)
            ci_status = CIStatus(ci)
            if ci_status != cell.ci_status:
                await db.update_cell(cell.id, ci_status=ci_status)
                await notify("cell_updated", {"id": cell.id, "ci_status": ci})
            if ci_status == CIStatus.failing and not cell.ci_failure_expected:
                ci_detail = f"ci: {cell.ci_status.value}\u2192{ci_status.value}"
                if ci_detail not in reasons:
                    reasons.append(ci_detail)
                needs_tend = True

            comment_count = await git.get_pr_comment_count(cell.repo, cell.pr_number)
            reaction_count = await git.get_pr_reaction_count(cell.repo, cell.pr_number)

            pr_activity_changed = (
                comment_count != cell.pr_comment_count
                or reaction_count != cell.pr_reaction_count
            )

            if pr_activity_changed:
                # Check cooldown: if the latest PR comment is too recent,
                # defer so the reviewer can finish their review.
                latest = await git.get_pr_latest_comment_time(cell.repo, cell.pr_number)
                elapsed = (
                    (datetime.now(timezone.utc) - latest).total_seconds()
                    if latest
                    else None
                )
                if elapsed is not None and elapsed < PR_ACTIVITY_COOLDOWN:
                    logger.info(
                        f"Cell {cell.id}: PR activity is {elapsed:.0f}s old, "
                        f"deferring until {PR_ACTIVITY_COOLDOWN}s cooldown"
                    )
                else:
                    if comment_count != cell.pr_comment_count:
                        reasons.append(
                            f"comments: {cell.pr_comment_count}\u2192{comment_count}"
                        )
                        await db.update_cell(cell.id, pr_comment_count=comment_count)
                    if reaction_count != cell.pr_reaction_count:
                        reasons.append(
                            f"reactions: {cell.pr_reaction_count}\u2192{reaction_count}"
                        )
                        await db.update_cell(cell.id, pr_reaction_count=reaction_count)
                    needs_tend = True

        # --- Spawn a tend session if anything changed ---
        key = (cell.id, "tend")
        if needs_tend and key not in _in_flight:
            # Clear the ci_failure_expected flag so the tend session
            # re-evaluates CI with fresh eyes (it was triggered by a
            # non-CI reason since CI alone can't trigger when the flag
            # is set).
            if cell.ci_failure_expected:
                await db.update_cell(cell.id, ci_failure_expected=False)
                cell.ci_failure_expected = False
            decision = "tended"
            logger.info(f"Cell {cell.id} needs fixing, invoking Claude")
            _in_flight.add(key)
            try:
                ok = await tend_cell(cell)
                outcome = "succeeded" if ok else "failed"
            except Exception:
                outcome = "failed"
                logger.exception(f"Tend failed for cell {cell.id}")
            finally:
                _in_flight.discard(key)
        elif needs_tend:
            decision = "skipped"

        # --- Re-derive sync status after merge/push/tend ---
        await _derive_sync_status(cell)

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


async def run_once() -> None:
    """Process all active cells once and record the results."""
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


async def daemon_loop() -> None:
    """Main daemon loop. Runs forever, processing all active cells periodically."""
    logger.info("Daemon started")
    while True:
        try:
            await run_once()
        except Exception:
            logger.exception("Daemon loop error")

        await asyncio.sleep(POLL_INTERVAL)
