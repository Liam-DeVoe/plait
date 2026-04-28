from __future__ import annotations

from pathlib import Path

from server import git


async def test_create_worktree_new_branch(git_env):
    """Creating a worktree for a branch that doesn't exist yet
    should create a new branch from origin/main."""
    wt_path = await git.create_worktree(git_env.repo_id, "new-feature", "worktop-1")
    assert Path(wt_path).exists()
    assert (Path(wt_path) / "README.md").read_text() == "initial"


async def test_create_worktree_existing_branch(git_env):
    """Creating a worktree for a branch that exists on the remote."""
    # Create a branch with a commit and push it
    git_env.create_branch("existing-branch")
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push("existing-branch")
    git_env.checkout("main")

    wt_path = await git.create_worktree(git_env.repo_id, "existing-branch", "worktop-2")
    assert Path(wt_path).exists()
    assert (Path(wt_path) / "file.txt").read_text() == "content"


async def test_remove_worktree(git_env):
    wt_path = await git.create_worktree(git_env.repo_id, "to-remove", "worktop-3")
    assert Path(wt_path).exists()

    await git.remove_worktree(git_env.repo_id, wt_path)
    assert not Path(wt_path).exists()


async def test_is_behind_main_when_current(git_env):
    git_env.create_branch("up-to-date")
    git_env.checkout("main")
    wt_path = await git.create_worktree(git_env.repo_id, "up-to-date", "worktop-4")
    assert not await git.is_behind_main(git_env.repo_id, wt_path, "up-to-date")


async def test_is_behind_main_when_behind(git_env):
    # Create a branch and push it
    git_env.create_branch("behind-branch")
    git_env.checkout("main")

    # Advance main past the branch
    git_env.add_commit("new_file.txt", "new content", "advance main")
    git_env.push("main")

    wt_path = await git.create_worktree(git_env.repo_id, "behind-branch", "worktop-5")
    await git.run("git", "fetch", "origin", cwd=wt_path)

    assert await git.is_behind_main(git_env.repo_id, wt_path, "behind-branch")


async def test_check_merge_conflicts_clean(git_env):
    # Create a branch with its own commit
    git_env.create_branch("feature")
    git_env.add_commit("feature.txt", "feature", "add feature")
    git_env.push("feature")
    git_env.checkout("main")

    # Advance main with a non-conflicting change
    git_env.add_commit("main_change.txt", "main stuff", "advance main")
    git_env.push("main")

    wt_path = await git.create_worktree(git_env.repo_id, "feature", "worktop-6")

    # No conflict expected — and the working tree must NOT have changed
    conflicts = await git.check_merge_conflicts(git_env.repo_id, wt_path)
    assert conflicts == []

    # No merge commit should have been created (HEAD still points at the
    # original branch tip, not a merge), and the working tree is clean.
    assert (Path(wt_path) / "feature.txt").exists()
    assert not (Path(wt_path) / "main_change.txt").exists()
    _, out, _ = await git.run("git", "status", "--porcelain", cwd=wt_path)
    assert out.strip() == ""


async def test_check_merge_conflicts_with_conflict(git_env):
    # Create a branch that edits README.md
    git_env.create_branch("conflicting")
    git_env.add_commit("README.md", "branch version", "edit readme on branch")
    git_env.push("conflicting")
    git_env.checkout("main")

    # Edit the same file on main
    git_env.add_commit("README.md", "main version", "edit readme on main")
    git_env.push("main")

    wt_path = await git.create_worktree(git_env.repo_id, "conflicting", "worktop-7")

    conflicts = await git.check_merge_conflicts(git_env.repo_id, wt_path)
    assert conflicts == ["README.md"]

    # Working tree is untouched — no in-progress merge.
    _, out, _ = await git.run("git", "status", "--porcelain", cwd=wt_path)
    assert out.strip() == ""


async def test_check_merge_conflicts_ignores_listed_paths(git_env):
    """Paths in `ignore` are filtered out of the returned conflict list."""
    git_env.create_branch("ignore-branch")
    git_env.add_commit("RELEASE.md", "branch release notes", "add release on branch")
    git_env.push("ignore-branch")
    git_env.checkout("main")

    git_env.add_commit("RELEASE.md", "main release notes", "add release on main")
    git_env.push("main")

    wt_path = await git.create_worktree(
        git_env.repo_id, "ignore-branch", "worktop-ignore"
    )

    # Without ignore: conflict shows up.
    assert await git.check_merge_conflicts(git_env.repo_id, wt_path) == ["RELEASE.md"]

    # With ignore: filtered out.
    assert (
        await git.check_merge_conflicts(
            git_env.repo_id, wt_path, ignore=frozenset({"RELEASE.md"})
        )
        == []
    )


async def test_check_merge_conflicts_ignore_with_other_conflict(git_env):
    """An ignored path is filtered, but other conflicts are still reported."""
    git_env.create_branch("mixed-branch")
    git_env.add_commit("RELEASE.md", "branch release", "add release on branch")
    git_env.add_commit("README.md", "branch readme", "edit readme on branch")
    git_env.push("mixed-branch")
    git_env.checkout("main")

    git_env.add_commit("RELEASE.md", "main release", "add release on main")
    git_env.add_commit("README.md", "main readme", "edit readme on main")
    git_env.push("main")

    wt_path = await git.create_worktree(
        git_env.repo_id, "mixed-branch", "worktop-mixed"
    )

    conflicts = await git.check_merge_conflicts(
        git_env.repo_id, wt_path, ignore=frozenset({"RELEASE.md"})
    )
    assert conflicts == ["README.md"]


# --- Local-only repo tests ---


async def test_main_branch_local(git_env_local):
    """For a local-only repo, main_branch resolves to the local default branch."""
    mb = await git.main_branch(git_env_local.repo_id)
    assert mb == "main"


async def test_main_ref_local(git_env_local):
    """main_ref returns just the bare branch name for local repos."""
    ref = await git.main_ref(git_env_local.repo_id)
    assert ref == "main"


async def test_create_worktree_local_new_branch(git_env_local):
    """Creating a worktree for a new branch in a local repo should branch off local main."""
    wt_path = await git.create_worktree(
        git_env_local.repo_id, "new-feature", "worktop-l1"
    )
    assert Path(wt_path).exists()
    assert (Path(wt_path) / "README.md").read_text() == "initial"


async def test_create_worktree_local_existing_branch(git_env_local):
    """Creating a worktree for an existing local branch reuses it."""
    # Create the branch in a separate worktree and add a commit there, so
    # the clone stays on main.
    wt0 = await git.create_worktree(git_env_local.repo_id, "existing", "worktop-pre")
    (Path(wt0) / "file.txt").write_text("content")
    await git.run("git", "add", "file.txt", cwd=wt0)
    await git.run("git", "commit", "-m", "add file", cwd=wt0)
    await git.remove_worktree(git_env_local.repo_id, wt0)

    wt_path = await git.create_worktree(git_env_local.repo_id, "existing", "worktop-l2")
    assert Path(wt_path).exists()
    assert (Path(wt_path) / "file.txt").read_text() == "content"


async def test_is_behind_main_local(git_env_local):
    """is_behind_main works against local main for local repos."""
    wt_path = await git.create_worktree(git_env_local.repo_id, "behind", "worktop-l3")
    git_env_local.add_commit("new.txt", "main work", "advance main")

    assert await git.is_behind_main(git_env_local.repo_id, wt_path, "behind")


async def test_is_behind_main_local_when_current(git_env_local):
    """is_behind_main returns False when the branch is current with local main."""
    wt_path = await git.create_worktree(
        git_env_local.repo_id, "up-to-date", "worktop-l4"
    )
    assert not await git.is_behind_main(git_env_local.repo_id, wt_path, "up-to-date")


async def test_check_merge_conflicts_local(git_env_local):
    """Conflicts with local main are detected without a fetch."""
    wt_path = await git.create_worktree(git_env_local.repo_id, "conflict", "worktop-l5")
    (Path(wt_path) / "README.md").write_text("branch")
    await git.run("git", "add", "README.md", cwd=wt_path)
    await git.run("git", "commit", "-m", "edit branch", cwd=wt_path)
    git_env_local.add_commit("README.md", "main", "edit on main")

    conflicts = await git.check_merge_conflicts(git_env_local.repo_id, wt_path)
    assert conflicts == ["README.md"]


async def test_is_merged_into_main_local(git_env_local):
    """is_merged_into_main detects a true merge into local main."""
    wt_path = await git.create_worktree(git_env_local.repo_id, "done", "worktop-l6")
    (Path(wt_path) / "done.txt").write_text("feature")
    await git.run("git", "add", "done.txt", cwd=wt_path)
    await git.run("git", "commit", "-m", "feature work", cwd=wt_path)
    # Merge from the clone (which is still on main).
    git_env_local.run_git("merge", "--no-ff", "-m", "merge done", "done")

    assert await git.is_merged_into_main(git_env_local.repo_id, "done")


async def test_is_merged_into_main_local_not_merged(git_env_local):
    """is_merged_into_main returns False when the branch isn't merged."""
    wt_path = await git.create_worktree(git_env_local.repo_id, "not-done", "worktop-l7")
    (Path(wt_path) / "wip.txt").write_text("wip")
    await git.run("git", "add", "wip.txt", cwd=wt_path)
    await git.run("git", "commit", "-m", "wip", cwd=wt_path)

    assert not await git.is_merged_into_main(git_env_local.repo_id, "not-done")


async def test_fetch_origin_local_is_noop(git_env_local):
    """fetch_origin must not network for local repos."""
    # Should not raise — there is no origin remote in a local repo.
    await git.fetch_origin(git_env_local.repo_id)


async def test_push(git_env):
    # Create a branch and worktree
    git_env.create_branch("push-test")
    git_env.add_commit("file.txt", "v1", "initial")
    git_env.push("push-test")
    git_env.checkout("main")

    wt_path = await git.create_worktree(git_env.repo_id, "push-test", "worktop-8")

    # Make a change in the worktree
    (Path(wt_path) / "file.txt").write_text("v2")
    await git.run("git", "add", "file.txt", cwd=wt_path)
    await git.run("git", "commit", "-m", "update", cwd=wt_path)

    # Push
    success, output = await git.push(wt_path, "push-test")
    assert success
