import json

import pytest

from server.git import get_ci_status, get_pr_info_from_url


async def test_get_pr_info_parses_url(mock_gh, git_env):
    from server.config import get_repo

    upstream = get_repo(git_env.repo_id).upstream
    pr_url = f"https://github.com/{upstream}/pull/42"
    mock_gh.set_response(
        "pr view",
        0,
        json.dumps({"number": 42, "url": pr_url, "headRefName": "my-branch"}),
    )

    info = await get_pr_info_from_url(pr_url)
    assert info["repo_id"] == git_env.repo_id
    assert info["branch"] == "my-branch"
    assert info["number"] == 42
    assert info["url"] == pr_url


async def test_get_pr_info_bad_url():
    with pytest.raises(RuntimeError, match="Could not parse repo"):
        await get_pr_info_from_url("not-a-github-url")


async def test_get_pr_info_unknown_upstream(mock_gh):
    """PR URL with an upstream not in config should raise."""
    pr_url = "https://github.com/unknown/repo/pull/1"
    with pytest.raises(RuntimeError, match="No configured repo"):
        await get_pr_info_from_url(pr_url)


async def test_get_pr_info_gh_failure(mock_gh, git_env):
    from server.config import get_repo

    upstream = get_repo(git_env.repo_id).upstream
    mock_gh.set_response("pr view", 1, "", "not found")

    with pytest.raises(RuntimeError, match="Failed to fetch PR info"):
        await get_pr_info_from_url(f"https://github.com/{upstream}/pull/1")


async def test_get_ci_status_passing(mock_gh, git_env):
    mock_gh.set_response("pr checks", 0, "build\tpass\t1m\ntest\tpass\t2m")
    assert await get_ci_status(git_env.repo_id, 1) == "passing"


async def test_get_ci_status_failing(mock_gh, git_env):
    mock_gh.set_response("pr checks", 0, "build\tfail\t1m\ntest\tpass\t2m")
    assert await get_ci_status(git_env.repo_id, 1) == "failing"


async def test_get_ci_status_pending(mock_gh, git_env):
    mock_gh.set_response("pr checks", 0, "build\tpending\t0m")
    assert await get_ci_status(git_env.repo_id, 1) == "pending"


async def test_get_ci_status_queued(mock_gh, git_env):
    mock_gh.set_response("pr checks", 0, "build\tqueued\t0m")
    assert await get_ci_status(git_env.repo_id, 1) == "pending"


async def test_get_ci_status_gh_failure(mock_gh, git_env):
    mock_gh.set_response("pr checks", 1, "", "error")
    assert await get_ci_status(git_env.repo_id, 1) == "unknown"
