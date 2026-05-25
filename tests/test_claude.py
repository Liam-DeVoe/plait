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
