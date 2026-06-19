from __future__ import annotations

from pathlib import Path

import pytest

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


async def test_create_review_worktree(git_env):
    """A review worktree is the PR checked out on a deterministic, plait-owned
    local branch (not detached, and not named after the PR head)."""
    # Simulate a PR: push a commit to the upstream's pull ref. GitHub
    # maintains refs/pull/<n>/head; we recreate one by hand.
    git_env.create_branch("pr-branch", push=False)
    git_env.add_commit("pr.txt", "from the PR", "pr work")
    git_env.run_git("push", "origin", "HEAD:refs/pull/42/head")
    git_env.checkout("main")

    wt_path = await git.create_review_worktree(
        git_env.repo_id, 42, "pr-branch", str(git_env.remote)
    )
    assert Path(wt_path).exists()
    assert Path(wt_path).name == f"review-{git_env.repo_id}-42"
    assert (Path(wt_path) / "pr.txt").read_text() == "from the PR"
    # On a deterministic plait-owned branch, not detached, not the PR head name.
    assert not await git.is_detached(wt_path)
    branch = git_env.run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=Path(wt_path))
    assert branch.strip() == f"plait/review-{git_env.repo_id}-42"


async def test_create_review_worktree_push_updates_pr(git_env):
    """A bare `git push` in the review worktree lands on the PR's head branch,
    even though the local branch has a different (plait-owned) name."""
    git_env.create_branch("contrib-branch", push=False)
    git_env.add_commit("p.txt", "pr", "pr work")
    git_env.run_git("push", "origin", "HEAD:refs/pull/55/head")
    # Publish the head branch too, so the push updates an existing ref.
    git_env.run_git("push", "origin", "HEAD:refs/heads/contrib-branch")
    git_env.checkout("main")

    wt = Path(
        await git.create_review_worktree(
            git_env.repo_id, 55, "contrib-branch", str(git_env.remote)
        )
    )
    # Make a fix and push with a bare `git push` — no push.default set here; the
    # worktree's own config (push.default=upstream) must route it correctly.
    (wt / "fix.txt").write_text("maintainer fix")
    git_env.run_git("add", "fix.txt", cwd=wt)
    git_env.run_git("commit", "-m", "maintainer fix", cwd=wt)
    git_env.run_git("push", cwd=wt)

    # The fix landed on the PR's *head* branch (not the plait-owned local name).
    local_head = git_env.run_git("rev-parse", "HEAD", cwd=wt)
    remote_head = git_env.run_git(
        "--git-dir", str(git_env.remote), "rev-parse", "refs/heads/contrib-branch"
    )
    assert local_head == remote_head
    # No branch named after the local plait branch was created on the remote.
    remote_branches = git_env.run_git("--git-dir", str(git_env.remote), "branch")
    assert "plait/review" not in remote_branches


async def test_create_review_worktree_persists(git_env):
    """Re-reviewing the same PR reuses the existing worktree untouched,
    preserving local commits and edits rather than resetting to the PR head."""
    git_env.create_branch("pr-evolving", push=False)
    git_env.add_commit("a.txt", "v1", "v1")
    git_env.run_git("push", "origin", "HEAD:refs/pull/7/head")
    wt1 = await git.create_review_worktree(
        git_env.repo_id, 7, "pr-evolving", str(git_env.remote)
    )
    assert (Path(wt1) / "a.txt").read_text() == "v1"

    # A local-only edit in the review worktree must survive a re-click.
    (Path(wt1) / "local.txt").write_text("work in progress")

    # The PR head moves on, but a second review must NOT clobber the worktree.
    git_env.add_commit("a.txt", "v2", "v2")
    git_env.run_git("push", "-f", "origin", "HEAD:refs/pull/7/head")
    wt2 = await git.create_review_worktree(
        git_env.repo_id, 7, "pr-evolving", str(git_env.remote)
    )
    assert wt1 == wt2  # deterministic per-PR path
    # Reused as-is: local edit kept, head still at v1 (not advanced to v2).
    assert (Path(wt2) / "local.txt").read_text() == "work in progress"
    assert (Path(wt2) / "a.txt").read_text() == "v1"


async def test_prune_stale_review_worktrees(git_env):
    """Stale review worktrees are garbage-collected by age."""
    git_env.create_branch("pr-old", push=False)
    git_env.add_commit("b.txt", "x", "x")
    git_env.run_git("push", "origin", "HEAD:refs/pull/9/head")
    git_env.checkout("main")
    wt = Path(
        await git.create_review_worktree(
            git_env.repo_id, 9, "pr-old", str(git_env.remote)
        )
    )
    assert wt.exists()

    # A fresh review survives a 7-day sweep but not a zero-day one.
    await git.prune_stale_review_worktrees(max_age_days=7)
    assert wt.exists()
    await git.prune_stale_review_worktrees(max_age_days=0)
    assert not wt.exists()


async def test_create_review_worktree_rejects_local(git_env_local):
    """PRs live on a remote — a local-only repo has no upstream to fetch."""
    with pytest.raises(RuntimeError, match="local-only"):
        await git.create_review_worktree(
            git_env_local.repo_id, 1, "some-branch", "https://github.com/x/y.git"
        )


async def test_is_detached(git_env):
    wt_path = await git.create_worktree(git_env.repo_id, "on-a-branch", "worktop-d")
    assert not await git.is_detached(wt_path)


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


async def test_fetch_upstream_local_is_noop(git_env_local):
    """fetch_upstream must not network for local repos."""
    # Should not raise — there is no remote in a local repo.
    await git.fetch_upstream(git_env_local.repo_id)


async def test_upstream_remote_matches_origin(git_env):
    """In a standard setup (single remote == upstream), upstream_remote → origin."""
    # Rewrite origin's URL to a synthetic GitHub URL that matches the
    # configured upstream so we exercise the live URL matcher rather than
    # the cache-seeding shortcut in conftest.
    git._upstream_remote_cache.clear()
    rc, _, err = await git.run(
        "git",
        "remote",
        "set-url",
        "origin",
        "git@github.com:testorg/testrepo.git",
        cwd=git_env.clone,
    )
    assert rc == 0, err

    assert await git.upstream_remote(git_env.repo_id) == "origin"


async def test_upstream_remote_picks_fork_remote(git_env):
    """Fork-and-PR setup: origin points to fork, separate remote is upstream."""
    git._upstream_remote_cache.clear()
    # origin → fork (does NOT match config's `testorg/testrepo`)
    await git.run(
        "git",
        "remote",
        "set-url",
        "origin",
        "git@github.com:myfork/testrepo.git",
        cwd=git_env.clone,
    )
    # add a separate "upstream" remote that DOES match
    await git.run(
        "git",
        "remote",
        "add",
        "upstream",
        "https://github.com/testorg/testrepo.git",
        cwd=git_env.clone,
    )

    assert await git.upstream_remote(git_env.repo_id) == "upstream"


async def test_upstream_remote_no_match_raises(git_env):
    """No remote URL matches config.upstream → raise loudly."""
    git._upstream_remote_cache.clear()
    await git.run(
        "git",
        "remote",
        "set-url",
        "origin",
        "git@github.com:wrong/repo.git",
        cwd=git_env.clone,
    )

    import pytest

    with pytest.raises(RuntimeError, match="does not match any remote"):
        await git.upstream_remote(git_env.repo_id)


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
