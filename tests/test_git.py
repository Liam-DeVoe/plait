from __future__ import annotations

from pathlib import Path

from server import git


async def test_create_worktree_new_branch(git_env):
    """Creating a worktree for a branch that doesn't exist yet
    should create a new branch from origin/main."""
    wt_path = await git.create_worktree(git_env.repo_id, "new-feature", "cell-1")
    assert Path(wt_path).exists()
    assert (Path(wt_path) / "README.md").read_text() == "initial"


async def test_create_worktree_existing_branch(git_env):
    """Creating a worktree for a branch that exists on the remote."""
    # Create a branch with a commit and push it
    git_env.create_branch("existing-branch")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("existing-branch")
    git_env.checkout("main")

    wt_path = await git.create_worktree(git_env.repo_id, "existing-branch", "cell-2")
    assert Path(wt_path).exists()
    assert (Path(wt_path) / "file.txt").read_text() == "content"


async def test_remove_worktree(git_env):
    wt_path = await git.create_worktree(git_env.repo_id, "to-remove", "cell-3")
    assert Path(wt_path).exists()

    await git.remove_worktree(git_env.repo_id, wt_path)
    assert not Path(wt_path).exists()


async def test_is_behind_main_when_current(git_env):
    git_env.create_branch("up-to-date")
    git_env.checkout("main")
    wt_path = await git.create_worktree(git_env.repo_id, "up-to-date", "cell-4")
    assert not await git.is_behind_main(wt_path, "up-to-date")


async def test_is_behind_main_when_behind(git_env):
    # Create a branch and push it
    git_env.create_branch("behind-branch")
    git_env.checkout("main")

    # Advance main past the branch
    git_env.add_commit("new_file.txt", "new content", "advance main")
    git_env.push("main")

    wt_path = await git.create_worktree(git_env.repo_id, "behind-branch", "cell-5")
    await git.run("git", "fetch", "origin", cwd=wt_path)

    assert await git.is_behind_main(wt_path, "behind-branch")


async def test_merge_from_main_clean(git_env):
    # Create a branch with its own commit
    git_env.create_branch("feature")
    git_env.add_commit("feature.txt", "feature", "add feature")
    git_env.push("feature")
    git_env.checkout("main")

    # Advance main
    git_env.add_commit("main_change.txt", "main stuff", "advance main")
    git_env.push("main")

    # Create worktree from the feature branch
    wt_path = await git.create_worktree(git_env.repo_id, "feature", "cell-6")

    # Merge should succeed (no conflicts)
    success, output = await git.merge_from_main(wt_path)
    assert success

    # Verify the worktree has both files
    assert (Path(wt_path) / "feature.txt").exists()
    assert (Path(wt_path) / "main_change.txt").exists()


async def test_merge_from_main_with_conflict(git_env):
    # Create a branch that edits README.md
    git_env.create_branch("conflicting")
    git_env.add_commit("README.md", "branch version", "edit readme on branch")
    git_env.push("conflicting")
    git_env.checkout("main")

    # Edit the same file on main
    git_env.add_commit("README.md", "main version", "edit readme on main")
    git_env.push("main")

    # Create worktree from the conflicting branch
    wt_path = await git.create_worktree(git_env.repo_id, "conflicting", "cell-7")

    # Merge should fail (conflict on README.md)
    success, output = await git.merge_from_main(wt_path)
    assert not success
    assert "conflicts" in output.lower()

    # Verify merge was aborted (working tree is clean)
    rc, out, err = await git.run("git", "status", "--porcelain", cwd=wt_path)
    assert out.strip() == ""


async def test_push(git_env):
    # Create a branch and worktree
    git_env.create_branch("push-test")
    git_env.add_commit("file.txt", "v1", "initial")
    git_env.push("push-test")
    git_env.checkout("main")

    wt_path = await git.create_worktree(git_env.repo_id, "push-test", "cell-8")

    # Make a change in the worktree
    (Path(wt_path) / "file.txt").write_text("v2")
    await git.run("git", "add", "file.txt", cwd=wt_path)
    await git.run("git", "commit", "-m", "update", cwd=wt_path)

    # Push
    success, output = await git.push(wt_path, "push-test")
    assert success
