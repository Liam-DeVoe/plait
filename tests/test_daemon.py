from __future__ import annotations

from pathlib import Path

from server import db, git
from server.daemon import process_cell
from server.models import (
    Cell,
    CIStatus,
    RebaseStatus,
    SessionStatus,
)


async def _create_cell_in_db(git_env, branch: str, cell_id: str) -> Cell:
    """Helper: create a worktree and DB record for a cell."""
    wt_path = await git.create_worktree(git_env.repo_name, branch, cell_id)
    cell = Cell(
        id=cell_id,
        repo=git_env.repo_name,
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
    assert fetched.rebase_status == RebaseStatus.current


async def test_cell_clean_rebase(git_env, init_db):
    """If cell is behind main and rebase is clean, it should rebase and push."""
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
    assert fetched.rebase_status == RebaseStatus.current

    # Verify the worktree has both files
    assert (Path(cell.worktree_path) / "feature.txt").exists()
    assert (Path(cell.worktree_path) / "main.txt").exists()


async def test_cell_conflict_claude_resolves(git_env, init_db, mock_claude):
    """If rebase has conflicts, Claude should be invoked to resolve them."""
    # Create a branch that edits README.md
    git_env.create_branch("conflict-branch")
    git_env.add_commit("README.md", "branch version", "edit on branch")
    git_env.push("conflict-branch")
    git_env.checkout("main")

    # Edit the same file on main
    git_env.add_commit("README.md", "main version", "edit on main")
    git_env.push("main")

    cell = await _create_cell_in_db(git_env, "conflict-branch", "daemon-3")

    # Mock Claude to actually resolve the conflict by doing a real rebase
    async def fake_claude(prompt, cwd):
        # Simulate Claude resolving: just do a rebase accepting theirs
        await git.run(
            "git", "rebase", "origin/main", "--strategy-option=theirs", cwd=cwd
        )
        return True, "resolved"

    mock_claude.side_effect = fake_claude

    await process_cell(cell)

    fetched = await db.get_cell(cell.id)
    assert fetched.rebase_status == RebaseStatus.current

    # Verify a daemon session was created
    sessions = await db.list_sessions(cell.id)
    assert len(sessions) == 1
    assert sessions[0].trigger == "rebase"
    assert sessions[0].status == SessionStatus.completed


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
    mock_claude.return_value = (False, "Claude couldn't figure it out")

    await process_cell(cell)

    fetched = await db.get_cell(cell.id)
    assert fetched.rebase_status == RebaseStatus.failed

    # Verify a failed daemon session was created
    sessions = await db.list_sessions(cell.id)
    assert len(sessions) == 1
    assert sessions[0].status == SessionStatus.failed


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
