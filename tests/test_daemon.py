from __future__ import annotations

import json
from pathlib import Path

from server import daemon, db, git
from server.daemon import process_cell
from server.models import (
    Cell,
    CellStatus,
    CIStatus,
    Session,
    SessionRole,
    SyncStatus,
)


async def _create_cell_in_db(git_env, branch: str, cell_id: str) -> Cell:
    """Helper: create a worktree and DB record for a cell."""
    wt_path = await git.create_worktree(git_env.repo_id, branch, cell_id)
    cell = Cell(
        id=cell_id,
        repo=git_env.repo_id,
        branch=branch,
        worktree_path=wt_path,
    )
    await db.create_cell(cell)
    return cell


async def test_cell_current_no_action(git_env, init_db):
    """If cell is not behind main, process_cell should do nothing."""
    cell = await _create_cell_in_db(git_env, "up-to-date", "daemon-1")

    await process_cell(cell)

    fetched = await db.get_cell(cell.id)
    assert fetched.sync_status == SyncStatus.current


async def test_cell_clean_merge(git_env, init_db):
    """If cell is behind main and merge is clean, it should merge and push."""
    # Create branch with its own commit
    git_env.create_branch("feature")
    git_env.add_commit("feature.txt", "feature work", "add feature")
    git_env.push("feature")
    git_env.checkout("main")

    # Advance main
    git_env.add_commit("main.txt", "main work", "advance main")
    git_env.push("main")

    cell = await _create_cell_in_db(git_env, "feature", "daemon-2")

    await process_cell(cell)

    fetched = await db.get_cell(cell.id)
    assert fetched.sync_status == SyncStatus.current

    # Verify the worktree has both files
    assert (Path(cell.worktree_path) / "feature.txt").exists()
    assert (Path(cell.worktree_path) / "main.txt").exists()


async def test_cell_conflict_claude_resolves(git_env, init_db, mock_claude):
    """If merge has conflicts, Claude should be invoked to resolve them."""
    # Create a branch that edits README.md
    git_env.create_branch("conflict-branch")
    git_env.add_commit("README.md", "branch version", "edit on branch")
    git_env.push("conflict-branch")
    git_env.checkout("main")

    # Edit the same file on main
    git_env.add_commit("README.md", "main version", "edit on main")
    git_env.push("main")

    cell = await _create_cell_in_db(git_env, "conflict-branch", "daemon-3")

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

    await process_cell(cell)

    fetched = await db.get_cell(cell.id)
    assert fetched.sync_status == SyncStatus.current

    # Verify a daemon session was created
    sessions = await db.list_sessions(cell.id)
    assert len(sessions) == 1
    assert sessions[0].trigger == "tend"
    assert sessions[0].succeeded is True


async def test_cell_conflict_claude_fails(git_env, init_db, mock_claude):
    """If Claude fails to resolve, cell should be marked as failed."""
    # Set up a conflict
    git_env.create_branch("fail-branch")
    git_env.add_commit("README.md", "branch version", "edit on branch")
    git_env.push("fail-branch")
    git_env.checkout("main")

    git_env.add_commit("README.md", "main version", "edit on main")
    git_env.push("main")

    cell = await _create_cell_in_db(git_env, "fail-branch", "daemon-4")

    # Mock Claude to fail
    mock_claude.return_value = 1

    await process_cell(cell)

    fetched = await db.get_cell(cell.id)
    assert fetched.sync_status == SyncStatus.behind

    # Verify a failed daemon session was created
    sessions = await db.list_sessions(cell.id)
    assert len(sessions) == 1
    assert sessions[0].succeeded is False


async def test_ci_status_update(git_env, init_db, mock_gh):
    """If cell has a PR, daemon should check and update CI status."""
    git_env.create_branch("ci-branch")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("ci-branch")
    git_env.checkout("main")

    cell = await _create_cell_in_db(git_env, "ci-branch", "daemon-5")
    # Manually set pr_number so CI check runs
    await db.update_cell(cell.id, pr_number=99)
    cell.pr_number = 99

    mock_gh.set_response("pr checks", 0, "build\tpass\t1m\ntest\tpass\t2m")

    await process_cell(cell)

    fetched = await db.get_cell(cell.id)
    assert fetched.ci_status == CIStatus.passing


async def test_ci_fix_on_failure(git_env, init_db, mock_gh, mock_claude):
    """If CI transitions to failing, Claude should be invoked to fix it."""
    git_env.create_branch("ci-fix-branch")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("ci-fix-branch")
    git_env.checkout("main")

    cell = await _create_cell_in_db(git_env, "ci-fix-branch", "daemon-6")
    await db.update_cell(cell.id, pr_number=100)
    cell.pr_number = 100

    # Mock CI as failing
    mock_gh.set_response("pr checks", 0, "build\tfail\t1m")
    # Mock CI failure logs
    mock_gh.set_response(
        "run list",
        0,
        json.dumps([{"databaseId": 999}]),
    )
    mock_gh.set_response("run view", 0, "Error: tests failed\nassert False")

    # Mock Claude to succeed at fixing
    mock_claude.return_value = 0

    await process_cell(cell)

    fetched = await db.get_cell(cell.id)
    assert fetched.ci_status == CIStatus.failing

    # Verify a ci_fix session was created
    sessions = await db.list_sessions(cell.id)
    assert len(sessions) == 1
    assert sessions[0].trigger == "tend"
    assert sessions[0].succeeded is True


async def test_ci_fix_skipped_while_running(git_env, init_db, mock_gh, mock_claude):
    """If a ci_fix session is already running, no new fix should be started."""
    git_env.create_branch("ci-norepeat")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("ci-norepeat")
    git_env.checkout("main")

    cell = await _create_cell_in_db(git_env, "ci-norepeat", "daemon-7")
    await db.update_cell(cell.id, pr_number=101, ci_status=CIStatus.failing)
    cell.pr_number = 101
    cell.ci_status = CIStatus.failing

    # Simulate an in-flight tend session
    key = (cell.id, "tend")
    daemon._in_flight.add(key)
    try:
        mock_gh.set_response("pr checks", 0, "build\tfail\t1m")

        await process_cell(cell)

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

    cell = await _create_cell_in_db(git_env, "ci-retry", "daemon-8")
    await db.update_cell(cell.id, pr_number=102, ci_status=CIStatus.failing)
    cell.pr_number = 102
    cell.ci_status = CIStatus.failing

    # Previous ci_fix session that ended (failed)
    prev_session = Session(cell_id=cell.id, role=SessionRole.daemon, trigger="tend")
    await db.create_session(prev_session)
    await db.update_session(
        prev_session.id, succeeded=0, ended_at="2024-01-01T00:00:00+00:00"
    )

    mock_gh.set_response("pr checks", 0, "build\tfail\t1m")
    mock_gh.set_response("pr checks --json", 1, "")

    await process_cell(cell)

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

    cell = await _create_cell_in_db(git_env, "retry-branch", "daemon-retry")

    # Pre-fill several failed merge sessions (all ended)
    for _ in range(5):
        s = Session(
            cell_id=cell.id,
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

    await process_cell(cell)

    sessions = await db.list_sessions(cell.id)
    assert len(sessions) == 6
    succeeded = [s for s in sessions if s.succeeded is True]
    assert len(succeeded) == 1


async def test_auto_archive_on_pr_merged(git_env, init_db, mock_gh):
    """If the PR has been merged, the cell should be auto-archived."""
    git_env.create_branch("merged-branch")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("merged-branch")
    git_env.checkout("main")

    cell = await _create_cell_in_db(git_env, "merged-branch", "daemon-merged")
    await db.update_cell(cell.id, pr_number=200)
    cell.pr_number = 200

    mock_gh.set_response("pr view", 0, "MERGED")

    result = await process_cell(cell)

    assert result["decision"] == "archived"
    assert "pr_merged" in result["reasons"]

    fetched = await db.get_cell(cell.id)
    assert fetched.status == CellStatus.archived
    assert fetched.archived_at is not None


async def test_auto_archive_on_pr_closed(git_env, init_db, mock_gh):
    """If the PR has been closed without merging, the cell should be auto-archived."""
    git_env.create_branch("closed-branch")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("closed-branch")
    git_env.checkout("main")

    cell = await _create_cell_in_db(git_env, "closed-branch", "daemon-closed")
    await db.update_cell(cell.id, pr_number=201)
    cell.pr_number = 201

    mock_gh.set_response("pr view", 0, "CLOSED")

    result = await process_cell(cell)

    assert result["decision"] == "archived"
    assert "pr_closed" in result["reasons"]

    fetched = await db.get_cell(cell.id)
    assert fetched.status == CellStatus.archived
