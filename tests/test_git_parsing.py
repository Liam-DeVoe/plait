import json

import pytest

from server.git import get_ci_status, get_pr_info_from_url, repo_path


def test_repo_path_extracts_name():
    from pathlib import Path

    import server.git as git_module

    # repo_path should use REPO_ROOT / repo_name
    original = git_module.REPO_ROOT
    git_module.REPO_ROOT = Path("/fake/root")
    try:
        assert repo_path("hegeldev/hegel-rust") == Path("/fake/root/hegel-rust")
        assert repo_path("owner/my-repo") == Path("/fake/root/my-repo")
    finally:
        git_module.REPO_ROOT = original


async def test_get_pr_info_parses_url(mock_gh):
    pr_url = "https://github.com/hegeldev/hegel-rust/pull/42"
    mock_gh.set_response(
        "pr view",
        0,
        json.dumps({"number": 42, "url": pr_url, "headRefName": "my-branch"}),
    )

    info = await get_pr_info_from_url(pr_url)
    assert info["repo"] == "hegeldev/hegel-rust"
    assert info["branch"] == "my-branch"
    assert info["number"] == 42
    assert info["url"] == pr_url


async def test_get_pr_info_bad_url():
    with pytest.raises(RuntimeError, match="Could not parse repo"):
        await get_pr_info_from_url("not-a-github-url")


async def test_get_pr_info_gh_failure(mock_gh):
    mock_gh.set_response("pr view", 1, "", "not found")

    with pytest.raises(RuntimeError, match="Failed to fetch PR info"):
        await get_pr_info_from_url("https://github.com/org/repo/pull/1")


async def test_get_ci_status_passing(mock_gh):
    mock_gh.set_response("pr checks", 0, "build\tpass\t1m\ntest\tpass\t2m")
    assert await get_ci_status("org/repo", 1) == "passing"


async def test_get_ci_status_failing(mock_gh):
    mock_gh.set_response("pr checks", 0, "build\tfail\t1m\ntest\tpass\t2m")
    assert await get_ci_status("org/repo", 1) == "failing"


async def test_get_ci_status_pending(mock_gh):
    mock_gh.set_response("pr checks", 0, "build\tpending\t0m")
    assert await get_ci_status("org/repo", 1) == "pending"


async def test_get_ci_status_queued(mock_gh):
    mock_gh.set_response("pr checks", 0, "build\tqueued\t0m")
    assert await get_ci_status("org/repo", 1) == "pending"


async def test_get_ci_status_gh_failure(mock_gh):
    mock_gh.set_response("pr checks", 1, "", "error")
    assert await get_ci_status("org/repo", 1) == "unknown"
