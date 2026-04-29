import json

import pytest

from server.git import _normalize_github_url, get_ci_status, get_pr_info_from_url


def test_normalize_github_url_ssh():
    assert (
        _normalize_github_url("git@github.com:HypothesisWorks/hypothesis.git")
        == "hypothesisworks/hypothesis"
    )
    assert _normalize_github_url("git@github.com:foo/bar") == "foo/bar"


def test_normalize_github_url_https():
    assert (
        _normalize_github_url("https://github.com/HypothesisWorks/hypothesis.git")
        == "hypothesisworks/hypothesis"
    )
    assert _normalize_github_url("https://github.com/foo/bar/") == "foo/bar"
    assert _normalize_github_url("https://user:tok@github.com/foo/bar.git") == "foo/bar"


def test_normalize_github_url_ssh_protocol():
    assert _normalize_github_url("ssh://git@github.com/foo/bar.git") == "foo/bar"


def test_normalize_github_url_non_github():
    assert _normalize_github_url("git@gitlab.com:foo/bar.git") is None
    assert _normalize_github_url("/local/path") is None
    assert _normalize_github_url("") is None


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
    # gh pr checks exits with 1 when any check is failing
    mock_gh.set_response("pr checks", 1, "build\tfail\t1m\ntest\tpass\t2m")
    assert await get_ci_status(git_env.repo_id, 1) == "failing"


async def test_get_ci_status_pending(mock_gh, git_env):
    mock_gh.set_response("pr checks", 0, "build\tpending\t0m")
    assert await get_ci_status(git_env.repo_id, 1) == "pending"


async def test_get_ci_status_queued(mock_gh, git_env):
    mock_gh.set_response("pr checks", 0, "build\tqueued\t0m")
    assert await get_ci_status(git_env.repo_id, 1) == "pending"


async def test_get_ci_status_gh_failure(mock_gh, git_env):
    mock_gh.set_response("pr checks", 1, "", "network error")
    assert await get_ci_status(git_env.repo_id, 1) == "unknown"
