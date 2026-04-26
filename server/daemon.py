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

# Backoff tiers for PR polling.
# (max_idle_seconds, poll_interval_seconds)
# Git operations (fetch, merge) always run; only API calls are deferred.
BACKOFF_TIERS = [
    (30 * 60, 5 * 60),  # < 30 min since activity: poll every 5 min
    (6 * 3600, 10 * 60),  # 30 min - 6 hrs: every 10 min
    (float("inf"), 15 * 60),  # 6+ hrs: every 15 min
]

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
    behind = await git.is_behind_main(cell.repo, cell.worktree_path, cell.branch)
    sync = SyncStatus.behind if behind else SyncStatus.current
    if sync != cell.sync_status:
        await db.update_cell(cell.id, sync_status=sync)
        await notify("cell_updated", {"id": cell.id, "sync_status": sync.value})
        cell.sync_status = sync


async def tend_cell(cell: Cell, has_conflict: bool = False) -> bool:
    """Unconditionally spawn a tend session for a cell. Returns True on success.

    `has_conflict` tells Claude (via the prompt) whether to perform a
    merge from main and resolve conflicts, or to leave merge-from-main
    alone. The daemon is authoritative about this — Claude never has to
    figure it out.
    """
    session = Session(
        cell_id=cell.id,
        role=SessionRole.daemon,
        trigger="tend",
    )
    await db.create_session(session)
    await notify("cell_updated", {"id": cell.id})

    cmd, cwd, prompt = await tend_cmd(session.id, cell, has_conflict)
    task = spawn_session(
        session.id, cmd, cwd, initial_input=prompt, idle_timeout=SESSION_IDLE_TIMEOUT
    )
    exit_code = await task
    ok = exit_code == 0

    if ok:
        await _push_if_published(cell)

    await db.update_session(session.id, succeeded=1 if ok else 0)
    await notify("cell_updated", {"id": cell.id})
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


# Tracks when each cell was last polled for PR data, keyed by cell ID.
# In-memory only — resets on server restart (which is fine, we just poll
# once immediately on restart).
_last_polled: dict[str, datetime] = {}


def _should_defer(cell: Cell) -> bool:
    """Check if PR polling should be deferred for this cell."""
    if not cell.last_activity_at:
        return False
    try:
        last_activity = datetime.fromisoformat(cell.last_activity_at)
    except (ValueError, TypeError):
        return False

    now = datetime.now(timezone.utc)
    idle_seconds = (now - last_activity).total_seconds()

    # Determine the appropriate polling interval for this cell's idle time
    interval = POLL_INTERVAL
    for max_idle, tier_interval in BACKOFF_TIERS:
        if idle_seconds < max_idle:
            interval = tier_interval
            break

    # If we're in the base tier, never defer
    if interval <= POLL_INTERVAL:
        return False

    # Check if enough time has passed since last poll
    last = _last_polled.get(cell.id)
    if last is None:
        return False  # never polled — don't defer first run
    return (now - last).total_seconds() < interval


async def _mark_activity(cell: Cell) -> None:
    """Update last_activity_at to now."""
    now = datetime.now(timezone.utc).isoformat()
    await db.update_cell(cell.id, last_activity_at=now)
    cell.last_activity_at = now


async def _process_cell(cell: Cell) -> dict:
    reasons: list[str] = []
    warnings: list[str] = []
    decision = "ok"
    outcome = None

    try:
        await git.assert_not_detached(cell.worktree_path)

        # Fetch latest from origin
        await git.fetch_origin(cell.repo)

        # --- Derive sync status from remote refs ---
        await _derive_sync_status(cell)

        # --- Detect (but don't perform) a merge if behind main ---
        # We deliberately don't create the merge commit ourselves: clean
        # merges produce noisy "Merge remote-tracking branch ..." commits
        # on the PR for no real benefit. Conflicts still need a tend
        # session — Claude does the actual merge there.
        #
        # Conflicts in transient release-marker files are ignored: another
        # PR was merged but the release job hasn't yet cut a release that
        # deletes the file from main. Acting on these conflicts is wasted
        # work — they resolve themselves once the release job runs.
        has_conflicts = False
        if cell.sync_status == SyncStatus.behind:
            reasons.append("behind_main")
            conflicts = await git.check_merge_conflicts(
                cell.repo,
                cell.worktree_path,
                ignore=frozenset({"RELEASE.md", "RELEASE.rst"}),
            )
            if conflicts:
                logger.info(
                    f"Cell {cell.id} ({cell.repo}:{cell.branch}) "
                    f"has merge conflicts with main: {', '.join(conflicts)}"
                )
                has_conflicts = True
                reasons.append(f"conflicts: {', '.join(conflicts)}")

        needs_tend = has_conflicts

        if cell.pr_number:
            # --- Backoff check: skip expensive API calls for idle cells ---
            if _should_defer(cell):
                decision = "deferred"
                return {
                    "cell_id": cell.id,
                    "repo": cell.repo,
                    "branch": cell.branch,
                    "decision": decision,
                    "reasons": reasons,
                    "warnings": warnings,
                    "outcome": outcome,
                }

            # Record that we're polling this cell now
            _last_polled[cell.id] = datetime.now(timezone.utc)

            # --- Fetch all PR data via pure REST (no GraphQL) ---
            pr_data = await git.get_pr_data(cell.repo, cell.pr_number)

            if pr_data is None:
                warnings.append("api_ratelimited")
                # Skip all PR comparisons — don't act on missing data
            else:
                # --- Auto-archive if PR merged/closed ---
                if pr_data.state in ("MERGED", "CLOSED"):
                    logger.info(
                        f"Cell {cell.id} PR #{cell.pr_number} is "
                        f"{pr_data.state}, archiving"
                    )
                    try:
                        await git.remove_worktree(cell.repo, cell.worktree_path)
                    except Exception:
                        logger.warning(f"Failed to remove worktree for cell {cell.id}")
                    await db.update_cell(
                        cell.id,
                        status=CellStatus.archived,
                        archived_at=datetime.now(timezone.utc).isoformat(),
                        archive_reason=pr_data.state.lower(),
                    )
                    await notify("cell_updated", {"id": cell.id, "status": "archived"})
                    return {
                        "cell_id": cell.id,
                        "repo": cell.repo,
                        "branch": cell.branch,
                        "decision": "archived",
                        "reasons": [f"pr_{pr_data.state.lower()}"],
                        "warnings": warnings,
                        "outcome": None,
                    }

                # --- Clear stale ci_failure_expected when HEAD moves ---
                if (
                    cell.ci_failure_expected_sha
                    and pr_data.head_sha != cell.ci_failure_expected_sha
                ):
                    await db.update_cell(cell.id, ci_failure_expected_sha=None)
                    cell.ci_failure_expected_sha = None

                # --- CI status (REST, using head SHA from PR data) ---
                ci = await git.get_ci_status_rest(cell.repo, pr_data.head_sha)
                ci_status = CIStatus(ci)
                if ci_status != cell.ci_status:
                    await db.update_cell(cell.id, ci_status=ci_status)
                    await notify("cell_updated", {"id": cell.id, "ci_status": ci})
                if ci_status == CIStatus.failing and not cell.ci_failure_expected_sha:
                    ci_detail = f"ci: {cell.ci_status.value}\u2192{ci_status.value}"
                    if ci_detail not in reasons:
                        reasons.append(ci_detail)
                    needs_tend = True

                # --- Comment / reaction comparison ---
                pr_activity_changed = (
                    pr_data.comment_count != cell.pr_comment_count
                    or pr_data.reaction_count != cell.pr_reaction_count
                )

                if pr_activity_changed:
                    elapsed = (
                        (
                            datetime.now(timezone.utc) - pr_data.latest_comment_time
                        ).total_seconds()
                        if pr_data.latest_comment_time
                        else None
                    )
                    if elapsed is not None and elapsed < PR_ACTIVITY_COOLDOWN:
                        logger.info(
                            f"Cell {cell.id}: PR activity is {elapsed:.0f}s "
                            f"old, deferring until "
                            f"{PR_ACTIVITY_COOLDOWN}s cooldown"
                        )
                    else:
                        if pr_data.comment_count != cell.pr_comment_count:
                            reasons.append(
                                f"comments: {cell.pr_comment_count}"
                                f"\u2192{pr_data.comment_count}"
                            )
                            await db.update_cell(
                                cell.id,
                                pr_comment_count=pr_data.comment_count,
                            )
                        if pr_data.reaction_count != cell.pr_reaction_count:
                            reasons.append(
                                f"reactions: {cell.pr_reaction_count}"
                                f"\u2192{pr_data.reaction_count}"
                            )
                            await db.update_cell(
                                cell.id,
                                pr_reaction_count=pr_data.reaction_count,
                            )
                        needs_tend = True

        # --- Spawn a tend session if anything changed ---
        key = (cell.id, "tend")
        if needs_tend and key not in _in_flight:
            # Clear the ci_failure_expected flag so the tend session
            # re-evaluates CI with fresh eyes (it was triggered by a
            # non-CI reason since CI alone can't trigger when the flag
            # is set).
            if cell.ci_failure_expected_sha:
                await db.update_cell(cell.id, ci_failure_expected_sha=None)
                cell.ci_failure_expected_sha = None
            decision = "tended"
            logger.info(f"Cell {cell.id} needs fixing, invoking Claude")
            _in_flight.add(key)
            try:
                ok = await tend_cell(cell, has_conflict=has_conflicts)
                outcome = "succeeded" if ok else "failed"
            except Exception:
                outcome = "failed"
                logger.exception(f"Tend failed for cell {cell.id}")
            finally:
                _in_flight.discard(key)
        elif needs_tend:
            decision = "skipped"

        # Mark activity if anything happened
        if decision not in ("ok", "deferred"):
            await _mark_activity(cell)

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
        "warnings": warnings,
        "outcome": outcome,
    }


async def run_once() -> None:
    """Process all active cells once and record the results."""
    cells = await db.list_cells(status=CellStatus.open)
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
