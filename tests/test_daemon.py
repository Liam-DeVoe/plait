from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server import daemon, db, git
from server.daemon import process_worktop
from server.models import (
    CIStatus,
    Session,
    SessionRole,
    SyncStatus,
    Worktop,
    WorktopStatus,
)


def _mock_pr_data(
    mock_gh,
    *,
    pr_number: int = 1,
    state: str = "open",
    head_sha: str = "abc123",
    review_comments: list | None = None,
    issue_comments: list | None = None,
    reviews: list | None = None,
    check_runs: list | None = None,
    mergeable_state: str = "clean",
):
    """Set up mock_gh responses for the pure-REST get_pr_data and get_ci_status_rest.

    Uses full endpoint paths to avoid substring collision (e.g. /pulls/99
    matching /pulls/99/comments). The mock_gh pattern matcher checks
    ``pattern in key`` so longer patterns are more specific.
    """
    # Set sub-endpoints FIRST so the more specific patterns are checked first.
    # Actually, dict iteration order matches insertion order, and the mock
    # iterates checking `pattern in key`, so the FIRST match wins.
    # We need the more-specific patterns (/pulls/N/comments) to be inserted
    # BEFORE the less-specific one (/pulls/N) — but /pulls/N is a substring
    # of /pulls/N/comments, not the other way around.
    #
    # Solution: for the main PR endpoint, use a pattern that includes the
    # trailing context. The actual command is:
    #   gh api repos/testorg/testrepo/pulls/99
    # which as a joined key ends with "/pulls/99". The sub-endpoints have
    # "/pulls/99/" (with trailing slash). So we match the main endpoint
    # with a pattern that won't appear in sub-endpoints.

    mock_gh.set_response(
        f"/pulls/{pr_number}/comments",
        0,
        json.dumps(review_comments or []),
    )
    mock_gh.set_response(
        f"/issues/{pr_number}/comments",
        0,
        json.dumps(issue_comments or []),
    )
    mock_gh.set_response(
        f"/pulls/{pr_number}/reviews",
        0,
        json.dumps(reviews or []),
    )
    if check_runs is not None:
        mock_gh.set_response(
            f"/commits/{head_sha}/check-runs",
            0,
            json.dumps({"check_runs": check_runs}),
        )

    # Main PR endpoint last — since /pulls/N is a substring of
    # /pulls/N/comments, we need the more specific patterns to be checked first.
    # The mock iterates in insertion order and returns the first match,
    # so this must come AFTER the sub-endpoint patterns.
    pr_json = json.dumps(
        {
            "state": state,
            "head": {"sha": head_sha},
            "mergeable_state": mergeable_state,
        }
    )
    mock_gh.set_response(f"/pulls/{pr_number}", 0, pr_json)


async def _create_worktop_in_db(git_env, branch: str, worktop_id: str) -> Worktop:
    """Helper: create a worktree and DB record for a worktop."""
    wt_path = await git.create_worktree(git_env.repo_id, branch, worktop_id)
    worktop = Worktop(
        id=worktop_id,
        repo=git_env.repo_id,
        branch=branch,
        worktree_path=wt_path,
    )
    await db.create_worktop(worktop)
    return worktop


async def test_worktop_current_no_action(git_env, init_db):
    """If worktop is not behind main, process_worktop should do nothing."""
    worktop = await _create_worktop_in_db(git_env, "up-to-date", "daemon-1")

    await process_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched.sync_status is SyncStatus.current


async def test_worktop_clean_merge_no_action(git_env, init_db, mock_claude):
    """A clean merge from main should NOT be performed by the daemon.

    Auto-merging clean would produce noisy merge commits on the PR. The
    daemon only acts when there's a real conflict; a behind-but-mergeable
    branch stays `behind` until the user (or a later tend) decides to
    merge.
    """
    git_env.create_branch("feature")
    git_env.add_commit("feature.txt", "feature work", "add feature")
    git_env.push("feature")
    git_env.checkout("main")

    git_env.add_commit("main.txt", "main work", "advance main")
    git_env.push("main")

    worktop = await _create_worktop_in_db(git_env, "feature", "daemon-2")

    head_before = await git.run("git", "rev-parse", "HEAD", cwd=worktop.worktree_path)

    await process_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched.sync_status is SyncStatus.behind

    # Worktree HEAD should be unchanged — no merge was done.
    head_after = await git.run("git", "rev-parse", "HEAD", cwd=worktop.worktree_path)
    assert head_before == head_after
    assert not (Path(worktop.worktree_path) / "main.txt").exists()

    # No tend session was spawned.
    mock_claude.assert_not_called()
    sessions = await db.list_sessions(worktop.id)
    assert sessions == []


async def test_worktop_release_md_conflict_skipped(git_env, init_db, mock_claude):
    """A RELEASE.md-only conflict shouldn't trigger a tend session.

    The release job will eventually delete RELEASE.md from main, so the
    conflict resolves itself. Tending now would either spin or produce a
    merge commit that has to be undone.
    """
    git_env.create_branch("release-conflict")
    git_env.add_commit("RELEASE.md", "branch release", "add release on branch")
    git_env.push("release-conflict")
    git_env.checkout("main")

    git_env.add_commit("RELEASE.md", "main release", "add release on main")
    git_env.push("main")

    worktop = await _create_worktop_in_db(git_env, "release-conflict", "daemon-rel")

    await process_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched.sync_status is SyncStatus.behind
    mock_claude.assert_not_called()
    sessions = await db.list_sessions(worktop.id)
    assert sessions == []


async def test_worktop_release_rst_in_subdir_conflict_skipped(
    git_env, init_db, mock_claude
):
    """A RELEASE.rst conflict in a monorepo subdir should be ignored.

    Hypothesis is a monorepo where RELEASE.rst lives at hypothesis/RELEASE.rst,
    so git merge-tree reports the conflict path with the subdir prefix.
    The ignore filter matches by basename.
    """
    (git_env.clone / "hypothesis").mkdir()
    git_env.create_branch("release-subdir-conflict")
    git_env.add_commit(
        "hypothesis/RELEASE.rst", "branch release", "add release on branch"
    )
    git_env.push("release-subdir-conflict")
    git_env.checkout("main")

    (git_env.clone / "hypothesis").mkdir(exist_ok=True)
    git_env.add_commit("hypothesis/RELEASE.rst", "main release", "add release on main")
    git_env.push("main")

    worktop = await _create_worktop_in_db(
        git_env, "release-subdir-conflict", "daemon-rel-sub"
    )

    await process_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched.sync_status is SyncStatus.behind
    mock_claude.assert_not_called()
    sessions = await db.list_sessions(worktop.id)
    assert sessions == []


async def test_worktop_conflict_claude_resolves(git_env, init_db, mock_claude):
    """If merge has conflicts, Claude should be invoked to resolve them."""
    # Create a branch that edits README.md
    git_env.create_branch("conflict-branch")
    git_env.add_commit("README.md", "branch version", "edit on branch")
    git_env.push("conflict-branch")
    git_env.checkout("main")

    # Edit the same file on main
    git_env.add_commit("README.md", "main version", "edit on main")
    git_env.push("main")

    worktop = await _create_worktop_in_db(git_env, "conflict-branch", "daemon-3")

    # Mock spawn_session to actually resolve the conflict by doing a real merge
    async def fake_spawn(session_id, cmd, cwd, **kwargs):
        await git.run(
            "git",
            "merge",
            "origin/main",
            "--strategy-option=theirs",
            "--no-edit",
            cwd=cwd,
        )
        return 0

    mock_claude.side_effect = fake_spawn

    await process_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched.sync_status is SyncStatus.current

    # Verify a daemon session was created
    sessions = await db.list_sessions(worktop.id)
    assert len(sessions) == 1
    assert sessions[0].trigger == "tend"
    assert sessions[0].succeeded is True


async def test_worktop_conflict_claude_fails(git_env, init_db, mock_claude):
    """If Claude fails to resolve, worktop should be marked as failed."""
    # Set up a conflict
    git_env.create_branch("fail-branch")
    git_env.add_commit("README.md", "branch version", "edit on branch")
    git_env.push("fail-branch")
    git_env.checkout("main")

    git_env.add_commit("README.md", "main version", "edit on main")
    git_env.push("main")

    worktop = await _create_worktop_in_db(git_env, "fail-branch", "daemon-4")

    # Mock Claude to fail
    mock_claude.return_value = 1

    await process_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched.sync_status is SyncStatus.behind

    # Verify a failed daemon session was created
    sessions = await db.list_sessions(worktop.id)
    assert len(sessions) == 1
    assert sessions[0].succeeded is False


async def test_tends_disabled_skips_claude_on_conflict(git_env, init_db, mock_claude):
    """When tends_enabled=False, daemon must NOT spawn a tend session even
    if a real conflict exists. Manual tends (not exercised here) still work."""
    git_env.create_branch("paused-conflict")
    git_env.add_commit("README.md", "branch version", "edit on branch")
    git_env.push("paused-conflict")
    git_env.checkout("main")

    git_env.add_commit("README.md", "main version", "edit on main")
    git_env.push("main")

    worktop = await _create_worktop_in_db(git_env, "paused-conflict", "daemon-paused")
    await db.update_worktop(worktop.id, tends_enabled=0)
    worktop.tends_enabled = False

    result = await process_worktop(worktop)

    assert result["decision"] == "tends_disabled"
    assert "tends_disabled" in result["reasons"]
    mock_claude.assert_not_called()
    sessions = await db.list_sessions(worktop.id)
    assert sessions == []

    # Sync status was still derived (we want metadata to stay fresh even
    # when auto-tends are paused).
    fetched = await db.get_worktop(worktop.id)
    assert fetched.sync_status is SyncStatus.behind
    assert fetched.tends_enabled is False


async def test_tends_enabled_still_spawns_claude(git_env, init_db, mock_claude):
    """Sanity: tends_enabled=True (the default) preserves prior behavior."""
    git_env.create_branch("enabled-conflict")
    git_env.add_commit("README.md", "branch version", "edit on branch")
    git_env.push("enabled-conflict")
    git_env.checkout("main")

    git_env.add_commit("README.md", "main version", "edit on main")
    git_env.push("main")

    worktop = await _create_worktop_in_db(git_env, "enabled-conflict", "daemon-enabled")
    # Default is True, but assert it explicitly so the test is readable.
    assert worktop.tends_enabled is True

    async def fake_spawn(session_id, cmd, cwd, **kwargs):
        await git.run(
            "git",
            "merge",
            "origin/main",
            "--strategy-option=theirs",
            "--no-edit",
            cwd=cwd,
        )
        return 0

    mock_claude.side_effect = fake_spawn

    await process_worktop(worktop)

    mock_claude.assert_called_once()
    sessions = await db.list_sessions(worktop.id)
    assert len(sessions) == 1
    assert sessions[0].trigger == "tend"


async def test_ci_status_update(git_env, init_db, mock_gh):
    """If worktop has a PR, daemon should check and update CI status."""
    git_env.create_branch("ci-branch")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("ci-branch")
    git_env.checkout("main")

    worktop = await _create_worktop_in_db(git_env, "ci-branch", "daemon-5")
    # Manually set pr_number so CI check runs
    await db.update_worktop(worktop.id, pr_number=99)
    worktop.pr_number = 99

    _mock_pr_data(
        mock_gh,
        pr_number=99,
        check_runs=[
            {"name": "build", "status": "completed", "conclusion": "success"},
            {"name": "test", "status": "completed", "conclusion": "success"},
        ],
    )

    await process_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched.ci_status is CIStatus.passing


async def test_ci_fix_on_failure(git_env, init_db, mock_gh, mock_claude):
    """If CI transitions to failing, Claude should be invoked to fix it."""
    git_env.create_branch("ci-fix-branch")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("ci-fix-branch")
    git_env.checkout("main")

    worktop = await _create_worktop_in_db(git_env, "ci-fix-branch", "daemon-6")
    await db.update_worktop(worktop.id, pr_number=100)
    worktop.pr_number = 100

    _mock_pr_data(
        mock_gh,
        pr_number=100,
        check_runs=[
            {"name": "build", "status": "completed", "conclusion": "failure"},
        ],
    )
    # Mock CI failure logs
    mock_gh.set_response(
        "run list",
        0,
        json.dumps([{"databaseId": 999}]),
    )
    mock_gh.set_response("run view", 0, "Error: tests failed\nassert False")

    # Mock Claude to succeed at fixing
    mock_claude.return_value = 0

    await process_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched.ci_status is CIStatus.failing

    # Verify a ci_fix session was created
    sessions = await db.list_sessions(worktop.id)
    assert len(sessions) == 1
    assert sessions[0].trigger == "tend"
    assert sessions[0].succeeded is True


async def test_ci_failure_expected_cleared_on_head_change(
    git_env, init_db, mock_gh, mock_claude
):
    """ci_failure_expected_sha should be cleared when the PR's HEAD moves."""
    git_env.create_branch("ci-expected")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("ci-expected")
    git_env.checkout("main")

    worktop = await _create_worktop_in_db(git_env, "ci-expected", "daemon-ci-expected")
    await db.update_worktop(
        worktop.id,
        pr_number=150,
        ci_status=CIStatus.failing,
        ci_failure_expected_sha="old_sha",
    )
    worktop.pr_number = 150
    worktop.ci_status = CIStatus.failing
    worktop.ci_failure_expected_sha = "old_sha"

    # PR HEAD has moved to a new SHA
    _mock_pr_data(
        mock_gh,
        pr_number=150,
        head_sha="new_sha",
        check_runs=[
            {"name": "build", "status": "completed", "conclusion": "failure"},
        ],
    )
    mock_claude.return_value = 0

    await process_worktop(worktop)

    # Flag should be cleared because HEAD changed
    fetched = await db.get_worktop(worktop.id)
    assert fetched.ci_failure_expected_sha is None

    # And Claude should have been invoked to fix the CI failure
    mock_claude.assert_called_once()


async def test_ci_failure_expected_suppresses_when_head_matches(
    git_env, init_db, mock_gh, mock_claude
):
    """ci_failure_expected_sha should suppress tend when HEAD hasn't moved."""
    git_env.create_branch("ci-suppress")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("ci-suppress")
    git_env.checkout("main")

    worktop = await _create_worktop_in_db(git_env, "ci-suppress", "daemon-ci-suppress")
    await db.update_worktop(
        worktop.id,
        pr_number=151,
        ci_status=CIStatus.failing,
        ci_failure_expected_sha="same_sha",
    )
    worktop.pr_number = 151
    worktop.ci_status = CIStatus.failing
    worktop.ci_failure_expected_sha = "same_sha"

    # PR HEAD is still the same SHA
    _mock_pr_data(
        mock_gh,
        pr_number=151,
        head_sha="same_sha",
        check_runs=[
            {"name": "build", "status": "completed", "conclusion": "failure"},
        ],
    )

    await process_worktop(worktop)

    # Flag should still be set
    fetched = await db.get_worktop(worktop.id)
    assert fetched.ci_failure_expected_sha == "same_sha"

    # Claude should NOT have been invoked
    mock_claude.assert_not_called()


async def test_ci_fix_skipped_while_running(git_env, init_db, mock_gh, mock_claude):
    """If a ci_fix session is already running, no new fix should be started."""
    git_env.create_branch("ci-norepeat")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("ci-norepeat")
    git_env.checkout("main")

    worktop = await _create_worktop_in_db(git_env, "ci-norepeat", "daemon-7")
    await db.update_worktop(worktop.id, pr_number=101, ci_status=CIStatus.failing)
    worktop.pr_number = 101
    worktop.ci_status = CIStatus.failing

    # Simulate an in-flight tend session
    key = (worktop.id, "tend")
    daemon._in_flight.add(key)
    try:
        _mock_pr_data(
            mock_gh,
            pr_number=101,
            check_runs=[
                {"name": "build", "status": "completed", "conclusion": "failure"},
            ],
        )

        await process_worktop(worktop)

        # Claude should NOT have been called — existing session still running
        mock_claude.assert_not_called()
    finally:
        daemon._in_flight.discard(key)


async def test_ci_fix_retried_after_previous_failure(
    git_env, init_db, mock_gh, mock_claude
):
    """If CI is still failing and the previous fix session ended, try again."""
    git_env.create_branch("ci-retry")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("ci-retry")
    git_env.checkout("main")

    worktop = await _create_worktop_in_db(git_env, "ci-retry", "daemon-8")
    await db.update_worktop(worktop.id, pr_number=102, ci_status=CIStatus.failing)
    worktop.pr_number = 102
    worktop.ci_status = CIStatus.failing

    # Previous ci_fix session that ended (failed)
    prev_session = Session(
        worktop_id=worktop.id, role=SessionRole.daemon, trigger="tend"
    )
    await db.create_session(prev_session)
    await db.update_session(
        prev_session.id, succeeded=0, ended_at="2024-01-01T00:00:00+00:00"
    )

    _mock_pr_data(
        mock_gh,
        pr_number=102,
        check_runs=[
            {"name": "build", "status": "completed", "conclusion": "failure"},
        ],
    )
    # Mock CI failure logs
    mock_gh.set_response(
        "run list",
        0,
        json.dumps([{"databaseId": 999}]),
    )
    mock_gh.set_response("run view", 0, "Error: tests failed")

    await process_worktop(worktop)

    # Claude SHOULD have been called — previous session ended
    mock_claude.assert_called_once()


async def test_merge_retried_after_previous_failure(git_env, init_db, mock_claude):
    """If merge previously failed but no session is running, try again."""
    git_env.create_branch("retry-branch")
    git_env.add_commit("README.md", "branch version", "edit on branch")
    git_env.push("retry-branch")
    git_env.checkout("main")

    git_env.add_commit("README.md", "main version", "edit on main")
    git_env.push("main")

    worktop = await _create_worktop_in_db(git_env, "retry-branch", "daemon-retry")

    # Pre-fill several failed merge sessions (all ended)
    for _ in range(5):
        s = Session(
            worktop_id=worktop.id,
            role=SessionRole.daemon,
            trigger="tend",
            succeeded=False,
            ended_at="2024-01-01T00:00:00+00:00",
        )
        await db.create_session(s)

    # Claude should still be invoked — no running session
    async def fake_spawn(session_id, cmd, cwd, **kwargs):
        await git.run(
            "git",
            "merge",
            "origin/main",
            "--strategy-option=theirs",
            "--no-edit",
            cwd=cwd,
        )
        return 0

    mock_claude.side_effect = fake_spawn

    await process_worktop(worktop)

    sessions = await db.list_sessions(worktop.id)
    assert len(sessions) == 6
    succeeded = [s for s in sessions if s.succeeded is True]
    assert len(succeeded) == 1


async def test_auto_archive_on_pr_merged(git_env, init_db, mock_gh):
    """If the PR has been merged, the worktop should be auto-archived."""
    git_env.create_branch("merged-branch")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("merged-branch")
    git_env.checkout("main")

    worktop = await _create_worktop_in_db(git_env, "merged-branch", "daemon-merged")
    await db.update_worktop(worktop.id, pr_number=200)
    worktop.pr_number = 200

    _mock_pr_data(mock_gh, pr_number=200, state="merged")

    result = await process_worktop(worktop)

    assert result["decision"] == "archived"
    assert "pr_merged" in result["reasons"]

    fetched = await db.get_worktop(worktop.id)
    assert fetched.status is WorktopStatus.archived
    assert fetched.archived_at is not None
    assert fetched.archive_reason == "merged"


async def test_auto_archive_on_pr_closed(git_env, init_db, mock_gh):
    """If the PR has been closed without merging, the worktop should be auto-archived."""
    git_env.create_branch("closed-branch")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("closed-branch")
    git_env.checkout("main")

    worktop = await _create_worktop_in_db(git_env, "closed-branch", "daemon-closed")
    await db.update_worktop(worktop.id, pr_number=201)
    worktop.pr_number = 201

    _mock_pr_data(mock_gh, pr_number=201, state="closed")

    result = await process_worktop(worktop)

    assert result["decision"] == "archived"
    assert "pr_closed" in result["reasons"]

    fetched = await db.get_worktop(worktop.id)
    assert fetched.status is WorktopStatus.archived
    assert fetched.archive_reason == "closed"


async def test_pr_activity_cooldown_skips_recent(
    git_env, init_db, mock_gh, mock_claude
):
    """Daemon defers tend when latest PR comment is less than 5 minutes old."""
    git_env.create_branch("cooldown-branch")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("cooldown-branch")
    git_env.checkout("main")

    worktop = await _create_worktop_in_db(git_env, "cooldown-branch", "daemon-cooldown")
    await db.update_worktop(worktop.id, pr_number=300, pr_comment_count=0)
    worktop.pr_number = 300
    worktop.pr_comment_count = 0

    # New comment created just now — within cooldown window
    recent_time = datetime.now(timezone.utc).isoformat()
    _mock_pr_data(
        mock_gh,
        pr_number=300,
        issue_comments=[
            {
                "user": {"login": "reviewer"},
                "created_at": recent_time,
                "reactions": {"total_count": 0},
            }
        ],
    )

    result = await process_worktop(worktop)

    # Should NOT have triggered a tend (cooldown active)
    mock_claude.assert_not_called()
    assert result["decision"] == "ok"

    # Comment count should NOT have been updated in DB (so next run re-detects)
    fetched = await db.get_worktop(worktop.id)
    assert fetched.pr_comment_count == 0


# --- Local-only repo daemon tests ---


async def _create_local_worktop(git_env_local, branch: str, worktop_id: str) -> Worktop:
    wt_path = await git.create_worktree(git_env_local.repo_id, branch, worktop_id)
    worktop = Worktop(
        id=worktop_id,
        repo=git_env_local.repo_id,
        branch=branch,
        worktree_path=wt_path,
    )
    await db.create_worktop(worktop)
    return worktop


async def test_local_worktop_current_no_action(git_env_local, init_db, mock_claude):
    """A current local worktop does nothing."""
    worktop = await _create_local_worktop(git_env_local, "feature", "local-1")

    await process_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched.sync_status is SyncStatus.current
    mock_claude.assert_not_called()


async def test_local_worktop_behind_no_conflict_no_action(
    git_env_local, init_db, mock_claude
):
    """A behind-but-mergeable local worktop shouldn't trigger a tend session."""
    worktop = await _create_local_worktop(git_env_local, "feature", "local-2")
    # Add a non-conflicting commit to main from the clone.
    git_env_local.add_commit("main.txt", "main", "advance main")

    await process_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched.sync_status is SyncStatus.behind
    mock_claude.assert_not_called()


async def test_local_worktop_conflict_invokes_tend(git_env_local, init_db, mock_claude):
    """A conflict against local main triggers a tend session."""
    worktop = await _create_local_worktop(git_env_local, "conflict", "local-3")
    # Edit README.md on the branch (in the worktree)
    await git.run(
        "git",
        "commit",
        "--allow-empty",
        "-m",
        "noop",
        cwd=worktop.worktree_path,
    )
    (Path(worktop.worktree_path) / "README.md").write_text("branch")
    await git.run("git", "add", "README.md", cwd=worktop.worktree_path)
    await git.run("git", "commit", "-m", "edit branch", cwd=worktop.worktree_path)
    # Edit README.md on main (in the clone)
    git_env_local.add_commit("README.md", "main", "edit on main")

    async def fake_spawn(session_id, cmd, cwd, **kwargs):
        await git.run(
            "git", "merge", "main", "--strategy-option=theirs", "--no-edit", cwd=cwd
        )
        return 0

    mock_claude.side_effect = fake_spawn

    await process_worktop(worktop)

    sessions = await db.list_sessions(worktop.id)
    assert len(sessions) == 1
    assert sessions[0].trigger == "tend"
    assert sessions[0].succeeded is True


async def test_local_worktop_archived_when_merged_into_main(
    git_env_local, init_db, mock_claude
):
    """When the worktop's branch is merged into local main, the worktop is archived."""
    worktop = await _create_local_worktop(git_env_local, "done", "local-4")
    # Add a commit to the branch in the worktree.
    (Path(worktop.worktree_path) / "done.txt").write_text("done")
    await git.run("git", "add", "done.txt", cwd=worktop.worktree_path)
    await git.run("git", "commit", "-m", "done work", cwd=worktop.worktree_path)

    # Merge done into main from the clone (which is on main).
    git_env_local.run_git("merge", "--no-ff", "-m", "merge done", "done")

    result = await process_worktop(worktop)

    assert result["decision"] == "archived"
    assert "local_merged" in result["reasons"]

    fetched = await db.get_worktop(worktop.id)
    assert fetched.status is WorktopStatus.archived
    assert fetched.archive_reason == "merged"

    # Branch should have been deleted by the daemon.
    rc, _, _ = await git.run(
        "git",
        "rev-parse",
        "--verify",
        "refs/heads/done",
        cwd=git_env_local.clone,
    )
    assert rc != 0
    mock_claude.assert_not_called()


async def test_local_worktop_no_pr_polling(
    git_env_local, init_db, mock_gh, mock_claude
):
    """The daemon must not call gh for local worktops."""
    worktop = await _create_local_worktop(git_env_local, "clean", "local-5")

    # No mock_gh responses set; if any gh command runs, the mock returns rc=1
    # with "no mock for: ..." — but more importantly we just confirm no tend.
    await process_worktop(worktop)
    mock_claude.assert_not_called()


async def test_pr_activity_cooldown_allows_old(git_env, init_db, mock_gh, mock_claude):
    """Daemon proceeds when latest PR comment is older than cooldown period."""
    git_env.create_branch("cooldown-ok")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("cooldown-ok")
    git_env.checkout("main")

    worktop = await _create_worktop_in_db(git_env, "cooldown-ok", "daemon-cooldown-ok")
    await db.update_worktop(worktop.id, pr_number=301, pr_comment_count=0)
    worktop.pr_number = 301
    worktop.pr_comment_count = 0

    # Comment from 10 minutes ago — past cooldown window
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    _mock_pr_data(
        mock_gh,
        pr_number=301,
        issue_comments=[
            {
                "user": {"login": "reviewer"},
                "created_at": old_time,
                "reactions": {"total_count": 0},
            }
        ],
    )

    mock_claude.return_value = 0

    result = await process_worktop(worktop)

    # Should have triggered a tend
    mock_claude.assert_called_once()
    assert result["decision"] == "tended"

    # Comment count should be updated
    fetched = await db.get_worktop(worktop.id)
    assert fetched.pr_comment_count == 1


# --- GitHub mergeable_state as the conflict signal ---


async def _setup_pr_worktop(
    git_env, branch: str, worktop_id: str, pr_number: int
) -> Worktop:
    """Create a worktop with a synthetic PR number set."""
    git_env.create_branch(branch)
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push(branch)
    git_env.checkout("main")
    worktop = await _create_worktop_in_db(git_env, branch, worktop_id)
    await db.update_worktop(worktop.id, pr_number=pr_number)
    worktop.pr_number = pr_number
    return worktop


async def test_mergeable_state_dirty_triggers_tend(
    git_env, init_db, mock_gh, mock_claude
):
    """GitHub `dirty` → tend, even if our local check would resolve cleanly.

    This is the rename/edit case: the branch renames a file, upstream
    edits the old path. Git's default rename detection would auto-merge
    (local check returns []), but GitHub blocks the merge as modify/delete.
    We trust GitHub.
    """
    worktop = await _setup_pr_worktop(git_env, "dirty-branch", "daemon-dirty", 400)

    # Local check returns no conflicts (no actual concurrent edits in the
    # test fixture) — simulating the rename-detection scenario. Behind+clean
    # locally, but GitHub says dirty.
    git_env.add_commit("upstream.txt", "upstream", "advance main")
    git_env.push("main")

    _mock_pr_data(mock_gh, pr_number=400, mergeable_state="dirty")
    mock_claude.return_value = 0

    result = await process_worktop(worktop)

    mock_claude.assert_called_once()
    assert result["decision"] == "tended"
    assert any("conflict" in r for r in result["reasons"])


async def test_mergeable_state_dirty_with_only_ignored_paths_skips(
    git_env, init_db, mock_gh, mock_claude
):
    """GitHub `dirty` but local check shows only RELEASE.md → skip."""
    git_env.create_branch("release-conflict")
    git_env.add_commit("RELEASE.md", "branch release", "add release on branch")
    git_env.push("release-conflict")
    git_env.checkout("main")
    git_env.add_commit("RELEASE.md", "main release", "add release on main")
    git_env.push("main")

    worktop = await _create_worktop_in_db(git_env, "release-conflict", "daemon-rel-pr")
    await db.update_worktop(worktop.id, pr_number=401)
    worktop.pr_number = 401

    _mock_pr_data(mock_gh, pr_number=401, mergeable_state="dirty")

    await process_worktop(worktop)

    mock_claude.assert_not_called()
    sessions = await db.list_sessions(worktop.id)
    assert sessions == []


async def test_mergeable_state_dirty_with_only_ignored_subdir_paths_skips(
    git_env, init_db, mock_gh, mock_claude
):
    """GitHub `dirty` but local check shows only `subdir/RELEASE.rst` → skip.

    Covers the Hypothesis monorepo case where RELEASE.rst lives under
    hypothesis/, not at the repo root.
    """
    (git_env.clone / "hypothesis").mkdir()
    git_env.create_branch("release-subdir-pr-conflict")
    git_env.add_commit(
        "hypothesis/RELEASE.rst", "branch release", "add release on branch"
    )
    git_env.push("release-subdir-pr-conflict")
    git_env.checkout("main")
    (git_env.clone / "hypothesis").mkdir(exist_ok=True)
    git_env.add_commit("hypothesis/RELEASE.rst", "main release", "add release on main")
    git_env.push("main")

    worktop = await _create_worktop_in_db(
        git_env, "release-subdir-pr-conflict", "daemon-rel-sub-pr"
    )
    await db.update_worktop(worktop.id, pr_number=403)
    worktop.pr_number = 403

    _mock_pr_data(mock_gh, pr_number=403, mergeable_state="dirty")

    await process_worktop(worktop)

    mock_claude.assert_not_called()
    sessions = await db.list_sessions(worktop.id)
    assert sessions == []


async def test_mergeable_state_clean_no_tend(git_env, init_db, mock_gh, mock_claude):
    """GitHub `clean` → no conflict tend, even if behind main.

    Replicates the existing "behind but mergeable → leave it alone"
    behavior, now sourced from GitHub instead of the local merge-tree
    check.
    """
    worktop = await _setup_pr_worktop(git_env, "clean-branch", "daemon-clean-pr", 402)
    git_env.add_commit("upstream.txt", "upstream", "advance main")
    git_env.push("main")

    _mock_pr_data(mock_gh, pr_number=402, mergeable_state="clean")

    await process_worktop(worktop)

    mock_claude.assert_not_called()


async def test_mergeable_state_unknown_defers_conflict_signal(
    git_env, init_db, mock_gh, mock_claude
):
    """GitHub `unknown` → don't tend for conflicts (recheck next tick).

    GitHub returns `unknown` for a few seconds after every push while it
    recomputes. We should not act on that uncertainty.
    """
    worktop = await _setup_pr_worktop(
        git_env, "unknown-branch", "daemon-unknown-pr", 403
    )
    git_env.add_commit("upstream.txt", "upstream", "advance main")
    git_env.push("main")

    _mock_pr_data(mock_gh, pr_number=403, mergeable_state="unknown")

    result = await process_worktop(worktop)

    mock_claude.assert_not_called()
    assert not any("conflict" in r for r in result["reasons"])


async def test_mergeable_state_behind_does_not_tend(
    git_env, init_db, mock_gh, mock_claude
):
    """GitHub `behind` (strict-mode required-up-to-date) → no tend.

    The user prefers the one-click "Update branch" on GitHub for this
    case — auto-merging would produce the same noisy merge commit
    earlier than necessary, with no benefit beyond saving a click.
    """
    worktop = await _setup_pr_worktop(git_env, "strict-branch", "daemon-strict-pr", 404)
    git_env.add_commit("upstream.txt", "upstream", "advance main")
    git_env.push("main")

    _mock_pr_data(mock_gh, pr_number=404, mergeable_state="behind")

    result = await process_worktop(worktop)

    mock_claude.assert_not_called()
    assert not any("conflict" in r for r in result["reasons"])


async def test_mergeable_state_blocked_no_conflict_tend(
    git_env, init_db, mock_gh, mock_claude
):
    """GitHub `blocked` (e.g. missing required review) → no conflict tend.

    `blocked` overlaps with CI/review state, both of which are handled
    by other signals. The mergeable_state branch alone shouldn't trigger.
    """
    worktop = await _setup_pr_worktop(
        git_env, "blocked-branch", "daemon-blocked-pr", 405
    )
    _mock_pr_data(mock_gh, pr_number=405, mergeable_state="blocked")

    result = await process_worktop(worktop)

    mock_claude.assert_not_called()
    assert not any("conflict" in r for r in result["reasons"])


async def test_run_once_prunes_stale_review_worktrees(git_env, init_db):
    """run_once garbage-collects review worktrees past the age cutoff,
    leaving fresh ones alone."""
    import os
    import time

    git_env.create_branch("pr-stale", push=False)
    git_env.add_commit("r.txt", "x", "x")
    git_env.run_git("push", "origin", "HEAD:refs/pull/13/head")
    git_env.checkout("main")
    wt = Path(
        await git.create_review_worktree(
            git_env.repo_id, 13, "pr-stale", str(git_env.remote)
        )
    )
    assert wt.exists()

    # Fresh review survives a run.
    await daemon.run_once()
    assert wt.exists()

    # Backdate it past the default max age — the next run sweeps it.
    old = time.time() - (git.REVIEW_WORKTREE_MAX_AGE_DAYS + 1) * 86400
    os.utime(wt, (old, old))
    await daemon.run_once()
    assert not wt.exists()
