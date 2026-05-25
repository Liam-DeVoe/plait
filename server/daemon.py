from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from server import config, db, git
from server.models import (
    CIStatus,
    MergeableState,
    Session,
    SessionRole,
    SyncStatus,
    Worktop,
    WorktopStatus,
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

# Conflicts in these paths are treated as transient — another PR was merged
# but the release job hasn't yet cut a release that deletes the file from
# main. Acting on them is wasted work; they resolve themselves once the
# release job runs.
IGNORED_CONFLICT_PATHS = frozenset({"RELEASE.md", "RELEASE.rst"})

# Backoff tiers for PR polling.
# (max_idle_seconds, poll_interval_seconds)
# Git operations (fetch, merge) always run; only API calls are deferred.
BACKOFF_TIERS = [
    (30 * 60, 5 * 60),  # < 30 min since activity: poll every 5 min
    (6 * 3600, 10 * 60),  # 30 min - 6 hrs: every 10 min
    (float("inf"), 15 * 60),  # 6+ hrs: every 15 min
]

# Per-worktop locks to prevent concurrent process_worktop execution (e.g. daemon
# daemon run overlapping with an API-triggered sync).
_worktop_locks: dict[str, asyncio.Lock] = {}

# In-flight daemon actions, keyed by (worktop_id, trigger). Prevents the daemon
# from spawning duplicate sessions for the same trigger on the same worktop.
# Tracked in memory so user-side actions (e.g. resuming a session) don't
# interfere with daemon concurrency control.
_in_flight: set[tuple[str, str]] = set()


async def notify(event: str, data: dict) -> None:
    if notify_callback is not None:
        await notify_callback.put({"event": event, "data": data})


async def _push_if_published(worktop: Worktop) -> bool | None:
    """Push to origin if the branch has been published (exists on remote).

    Returns True if pushed successfully, False if push failed,
    or None if the branch is local-only (no push attempted).
    """
    if not await git.has_remote_branch(worktop.worktree_path, worktop.branch):
        return None
    ok, _ = await git.push(worktop.worktree_path, worktop.branch)
    return ok


async def _derive_sync_status(worktop: Worktop) -> None:
    """Derive sync status from remote refs and update DB if changed."""
    behind = await git.is_behind_main(
        worktop.repo, worktop.worktree_path, worktop.branch
    )
    sync = SyncStatus.behind if behind else SyncStatus.current
    if sync is not worktop.sync_status:
        await db.update_worktop(worktop.id, sync_status=sync)
        await notify("worktop_updated", {"id": worktop.id, "sync_status": sync.value})
        worktop.sync_status = sync


async def tend_worktop(worktop: Worktop, has_conflict: bool = False) -> bool:
    """Unconditionally spawn a tend session for a worktop. Returns True on success.

    `has_conflict` tells Claude (via the prompt) whether to perform a
    merge from main and resolve conflicts, or to leave merge-from-main
    alone. The daemon is authoritative about this — Claude never has to
    figure it out.
    """
    session = Session(
        worktop_id=worktop.id,
        role=SessionRole.daemon,
        trigger="tend",
    )
    await db.create_session(session)
    await notify("worktop_updated", {"id": worktop.id})

    cmd, cwd, prompt = await tend_cmd(session.id, worktop, has_conflict)
    task = spawn_session(
        session.id, cmd, cwd, initial_input=prompt, idle_timeout=SESSION_IDLE_TIMEOUT
    )
    exit_code = await task
    ok = exit_code == 0

    if ok:
        await _push_if_published(worktop)

    await db.update_session(session.id, succeeded=1 if ok else 0)
    await notify("worktop_updated", {"id": worktop.id})
    return ok


async def process_worktop(worktop: Worktop) -> dict | None:
    """Process a single worktop: check sync status, CI status, and fix issues.

    Returns a result dict for daemon run tracking, or None if the worktop
    was already being processed.
    """
    lock = _worktop_locks.setdefault(worktop.id, asyncio.Lock())
    if lock.locked():
        logger.info(f"Worktop {worktop.id} already being processed, skipping")
        return None
    async with lock:
        return await _process_worktop(worktop)


# Tracks when each worktop was last polled for PR data, keyed by worktop ID.
# In-memory only — resets on server restart (which is fine, we just poll
# once immediately on restart).
_last_polled: dict[str, datetime] = {}


def _should_defer(worktop: Worktop) -> bool:
    """Check if PR polling should be deferred for this worktop."""
    if not worktop.last_activity_at:
        return False
    try:
        last_activity = datetime.fromisoformat(worktop.last_activity_at)
    except (ValueError, TypeError):
        return False

    now = datetime.now(timezone.utc)
    idle_seconds = (now - last_activity).total_seconds()

    # Determine the appropriate polling interval for this worktop's idle time
    interval = POLL_INTERVAL
    for max_idle, tier_interval in BACKOFF_TIERS:
        if idle_seconds < max_idle:
            interval = tier_interval
            break

    # If we're in the base tier, never defer
    if interval <= POLL_INTERVAL:
        return False

    # Check if enough time has passed since last poll
    last = _last_polled.get(worktop.id)
    if last is None:
        return False  # never polled — don't defer first run
    return (now - last).total_seconds() < interval


async def _mark_activity(worktop: Worktop) -> None:
    """Update last_activity_at to now."""
    now = datetime.now(timezone.utc).isoformat()
    await db.update_worktop(worktop.id, last_activity_at=now)
    worktop.last_activity_at = now


async def _archive_worktop(worktop: Worktop, reason: str) -> None:
    """Archive a worktop: remove its worktree, mark it archived in DB, notify."""
    try:
        await git.remove_worktree(worktop.repo, worktop.worktree_path)
    except Exception:
        logger.warning(f"Failed to remove worktree for worktop {worktop.id}")
    await db.update_worktop(
        worktop.id,
        status=WorktopStatus.archived,
        archived_at=datetime.now(timezone.utc).isoformat(),
        archive_reason=reason,
    )
    await notify("worktop_updated", {"id": worktop.id, "status": "archived"})


async def _process_worktop(worktop: Worktop) -> dict:
    reasons: list[str] = []
    warnings: list[str] = []
    decision = "ok"
    outcome = None

    try:
        # --- Local-only repos: detect merge into local main and archive ---
        # Done first so a worktree that Claude has switched to main doesn't
        # trip up the assert-not-detached / behind-main checks below.
        if config.is_local(worktop.repo) and await git.is_merged_into_main(
            worktop.repo, worktop.branch
        ):
            logger.info(
                f"Worktop {worktop.id} branch {worktop.branch} merged into main, archiving"
            )
            await _archive_worktop(worktop, reason="merged")
            await git.delete_branch(worktop.repo, worktop.branch)
            return {
                "worktop_id": worktop.id,
                "repo": worktop.repo,
                "branch": worktop.branch,
                "decision": "archived",
                "reasons": ["local_merged"],
                "warnings": warnings,
                "outcome": None,
            }

        await git.assert_not_detached(worktop.worktree_path)

        # Fetch latest from the upstream (where main is authoritative).
        await git.fetch_upstream(worktop.repo)

        # --- Derive sync status from remote refs ---
        await _derive_sync_status(worktop)

        # We deliberately don't auto-create the merge commit when a branch
        # is behind but mergeable: clean merges produce noisy "Merge
        # remote-tracking branch ..." commits on the PR for no benefit.
        # Tend sessions only fire when there's something Claude actually
        # needs to do (conflict, CI failure, reviewer feedback, etc.).
        has_conflicts = False
        needs_tend = False
        if worktop.sync_status is SyncStatus.behind:
            reasons.append("behind_main")

        # --- Conflict detection: local fallback ---
        # When there's no GitHub authority to consult (local-only repo,
        # or remote repo with no PR yet) we use the local merge-tree check.
        # Worktops with a PR defer to GitHub's `mergeable_state` below.
        if worktop.sync_status is SyncStatus.behind and (
            config.is_local(worktop.repo) or not worktop.pr_number
        ):
            conflicts = await git.check_merge_conflicts(
                worktop.repo, worktop.worktree_path
            )
            actionable = [c for c in conflicts if c not in IGNORED_CONFLICT_PATHS]
            if actionable:
                logger.info(
                    f"Worktop {worktop.id} ({worktop.repo}:{worktop.branch}) "
                    f"has merge conflicts with main: {', '.join(actionable)}"
                )
                has_conflicts = True
                needs_tend = True
                reasons.append(f"conflicts: {', '.join(actionable)}")

        if worktop.pr_number:
            # --- Backoff check: skip expensive API calls for idle worktops ---
            if _should_defer(worktop):
                decision = "deferred"
                return {
                    "worktop_id": worktop.id,
                    "repo": worktop.repo,
                    "branch": worktop.branch,
                    "decision": decision,
                    "reasons": reasons,
                    "warnings": warnings,
                    "outcome": outcome,
                }

            # Record that we're polling this worktop now
            _last_polled[worktop.id] = datetime.now(timezone.utc)

            # --- Fetch all PR data via pure REST (no GraphQL) ---
            pr_data = await git.get_pr_data(worktop.repo, worktop.pr_number)

            if pr_data is None:
                warnings.append("api_ratelimited")
                # Skip all PR comparisons — don't act on missing data
            else:
                # --- Auto-archive if PR merged/closed ---
                if pr_data.state in ("MERGED", "CLOSED"):
                    logger.info(
                        f"Worktop {worktop.id} PR #{worktop.pr_number} is "
                        f"{pr_data.state}, archiving"
                    )
                    await _archive_worktop(worktop, reason=pr_data.state.lower())
                    return {
                        "worktop_id": worktop.id,
                        "repo": worktop.repo,
                        "branch": worktop.branch,
                        "decision": "archived",
                        "reasons": [f"pr_{pr_data.state.lower()}"],
                        "warnings": warnings,
                        "outcome": None,
                    }

                # --- Clear stale ci_failure_expected when HEAD moves ---
                if (
                    worktop.ci_failure_expected_sha
                    and pr_data.head_sha != worktop.ci_failure_expected_sha
                ):
                    await db.update_worktop(worktop.id, ci_failure_expected_sha=None)
                    worktop.ci_failure_expected_sha = None

                # --- CI status (REST, using head SHA from PR data) ---
                ci = await git.get_ci_status_rest(worktop.repo, pr_data.head_sha)
                ci_status = CIStatus(ci)
                if ci_status is not worktop.ci_status:
                    await db.update_worktop(worktop.id, ci_status=ci_status)
                    await notify("worktop_updated", {"id": worktop.id, "ci_status": ci})
                if (
                    ci_status is CIStatus.failing
                    and not worktop.ci_failure_expected_sha
                ):
                    ci_detail = f"ci: {worktop.ci_status.value}\u2192{ci_status.value}"
                    if ci_detail not in reasons:
                        reasons.append(ci_detail)
                    needs_tend = True

                # --- Comment / reaction comparison ---
                pr_activity_changed = (
                    pr_data.comment_count != worktop.pr_comment_count
                    or pr_data.reaction_count != worktop.pr_reaction_count
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
                            f"Worktop {worktop.id}: PR activity is {elapsed:.0f}s "
                            f"old, deferring until "
                            f"{PR_ACTIVITY_COOLDOWN}s cooldown"
                        )
                    else:
                        if pr_data.comment_count != worktop.pr_comment_count:
                            reasons.append(
                                f"comments: {worktop.pr_comment_count}"
                                f"\u2192{pr_data.comment_count}"
                            )
                            await db.update_worktop(
                                worktop.id,
                                pr_comment_count=pr_data.comment_count,
                            )
                        if pr_data.reaction_count != worktop.pr_reaction_count:
                            reasons.append(
                                f"reactions: {worktop.pr_reaction_count}"
                                f"\u2192{pr_data.reaction_count}"
                            )
                            await db.update_worktop(
                                worktop.id,
                                pr_reaction_count=pr_data.reaction_count,
                            )
                        needs_tend = True

                # --- Conflict signal from GitHub ---
                # GitHub is authoritative for what blocks the merge button.
                # We use it instead of our local merge-tree check because
                # git's default rename detection can resolve "conflicts"
                # that GitHub still blocks on (e.g. modify/delete after a
                # rename). Only `dirty` triggers a tend; `behind`
                # (strict-mode out of date) is left alone so the user can
                # one-click "Update branch" when they next review the PR.
                if pr_data.mergeable_state is MergeableState.dirty:
                    # The local check is only consulted to apply the
                    # ignore filter: if every local conflict is in the
                    # ignore set (e.g. RELEASE.md), the release job will
                    # resolve it and tending is wasted work. In every
                    # other case — including when the local check returns
                    # no paths at all — we trust GitHub.
                    conflicts = await git.check_merge_conflicts(
                        worktop.repo, worktop.worktree_path
                    )
                    actionable = [
                        c for c in conflicts if c not in IGNORED_CONFLICT_PATHS
                    ]
                    all_ignored = conflicts and not actionable
                    if not all_ignored:
                        has_conflicts = True
                        needs_tend = True
                        detail = ", ".join(actionable) if actionable else "unknown"
                        reasons.append(f"conflicts: {detail}")

        # --- Spawn a tend session if anything changed ---
        key = (worktop.id, "tend")
        if needs_tend and key not in _in_flight:
            # Clear the ci_failure_expected flag so the tend session
            # re-evaluates CI with fresh eyes (it was triggered by a
            # non-CI reason since CI alone can't trigger when the flag
            # is set).
            if worktop.ci_failure_expected_sha:
                await db.update_worktop(worktop.id, ci_failure_expected_sha=None)
                worktop.ci_failure_expected_sha = None
            decision = "tended"
            logger.info(f"Worktop {worktop.id} needs fixing, invoking Claude")
            _in_flight.add(key)
            try:
                ok = await tend_worktop(worktop, has_conflict=has_conflicts)
                outcome = "succeeded" if ok else "failed"
            except Exception:
                outcome = "failed"
                logger.exception(f"Tend failed for worktop {worktop.id}")
            finally:
                _in_flight.discard(key)
        elif needs_tend:
            decision = "skipped"

        # Mark activity if anything happened
        if decision not in ("ok", "deferred"):
            await _mark_activity(worktop)

        # --- Re-derive sync status after merge/push/tend ---
        await _derive_sync_status(worktop)

        # --- Detect PR for worktops that have been pushed but have no PR yet ---
        if (
            not config.is_local(worktop.repo)
            and not worktop.pr_number
            and await git.has_remote_branch(worktop.worktree_path, worktop.branch)
        ):
            pr_info = await git.find_pr_for_branch(worktop.repo, worktop.branch)
            if pr_info:
                await db.update_worktop(
                    worktop.id,
                    pr_number=pr_info["number"],
                    pr_url=pr_info["url"],
                )
                await notify("worktop_updated", {"id": worktop.id})

    except Exception:
        decision = "error"
        logger.exception(f"Error processing worktop {worktop.id}")

    return {
        "worktop_id": worktop.id,
        "repo": worktop.repo,
        "branch": worktop.branch,
        "decision": decision,
        "reasons": reasons,
        "warnings": warnings,
        "outcome": outcome,
    }


async def run_once() -> None:
    """Process all active worktops once and record the results."""
    worktops = await db.list_worktops(status=WorktopStatus.open)
    logger.info(f"Daemon run: processing {len(worktops)} active worktops")

    started_at = datetime.now(timezone.utc).isoformat()
    results = []
    for worktop in worktops:
        result = await process_worktop(worktop)
        if result is not None:
            results.append(result)

    ended_at = datetime.now(timezone.utc).isoformat()
    run_id = str(uuid.uuid4())
    await db.create_daemon_run(run_id, started_at, ended_at, results)


async def daemon_loop() -> None:
    """Main daemon loop. Runs forever, processing all active worktops periodically."""
    logger.info("Daemon started")
    while True:
        try:
            await run_once()
        except Exception:
            logger.exception("Daemon loop error")

        await asyncio.sleep(POLL_INTERVAL)
