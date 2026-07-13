"""Tests for prompt rendering in server/claude.py."""

from __future__ import annotations

import pytest

from server import claude


def test_tend_prompt_uses_upstream_remote_for_merge():
    """The merge instruction should target the upstream remote, not origin.

    In fork-and-PR setups `origin` is the user's fork (often behind the
    canonical upstream); merging from it would be a no-op against the
    actual conflict. The prompt must instruct Claude to merge from the
    upstream remote.
    """
    prompt = claude.tend_prompt(
        branch="my-branch",
        worktop_id="wt-1",
        session_id="sess-1",
        main_branch="main",
        has_conflict=True,
        upstream_remote="upstream",
    )
    assert "git merge upstream/main" in prompt
    assert "origin/main" not in prompt


def test_tend_prompt_merge_skip_uses_upstream_remote():
    """The no-conflict merge-skip section also references the upstream remote."""
    prompt = claude.tend_prompt(
        branch="my-branch",
        worktop_id="wt-1",
        session_id="sess-1",
        main_branch="main",
        has_conflict=False,
        upstream_remote="upstream",
    )
    assert "upstream/main" in prompt
    assert "origin/main" not in prompt


def test_tend_prompt_requires_upstream_remote_for_remote_repo():
    """Calling tend_prompt for a non-local repo without an upstream_remote
    must fail loudly — silent fallback to a wrong ref would re-create the
    bug we're guarding against."""
    with pytest.raises(AssertionError):
        claude.tend_prompt(
            branch="my-branch",
            worktop_id="wt-1",
            session_id="sess-1",
            main_branch="main",
            has_conflict=True,
        )


def test_tend_prompt_local_repo_omits_upstream_remote():
    """Local repos don't have an upstream and shouldn't need one."""
    prompt = claude.tend_prompt(
        branch="my-branch",
        worktop_id="wt-1",
        session_id="sess-1",
        main_branch="main",
        has_conflict=True,
        is_local=True,
    )
    # The local template references `main` directly (no remote prefix).
    assert "git merge main" in prompt


# --- write_worktop_claude_md + copy_globs layering ---


async def test_write_worktop_claude_md_layers_copied_files(git_env):
    """Copied gitignored files land in the worktree, but plait's own
    claude_files overlay wins any path collision (guardrails must survive),
    and a copied CLAUDE.local.md is preserved with plait's block appended."""
    from pathlib import Path

    from server import config, git

    git_env.add_commit(".gitignore", ".claude/\nCLAUDE.local.md\n", "ignore")
    claude_dir = git_env.clone / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.local.json").write_text('{"user_settings": true}')
    (claude_dir / "my-notes.md").write_text("personal")
    (git_env.clone / "CLAUDE.local.md").write_text("# my local notes\n")
    config.get_repo(git_env.repo_id).copy_globs = [".claude/**", "CLAUDE.local.md"]

    wt = Path(await git.create_worktree(git_env.repo_id, "copy-branch", "worktop-copy"))
    await claude.write_worktop_claude_md(str(wt), "worktop-copy", git_env.repo_id)

    # Non-colliding copied file survives.
    assert (wt / ".claude" / "my-notes.md").read_text() == "personal"
    # Colliding settings.local.json is plait's rendered version, not the
    # user's copy.
    settings = (wt / ".claude" / "settings.local.json").read_text()
    assert "user_settings" not in settings
    assert "{worktree_root}" not in settings
    # Copied CLAUDE.local.md is preserved, with plait's worktop block after.
    local_md = (wt / "CLAUDE.local.md").read_text()
    assert local_md.startswith("# my local notes")
    assert "worktop-copy" in local_md
