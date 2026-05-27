from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from server.api import app


@pytest.fixture
async def client(init_db, git_env, mock_gh):
    """Async HTTP client for the FastAPI app.
    Uses in-memory DB, temp git repos, and mocked gh CLI.
    Disables the lifespan (daemon) to avoid background tasks in tests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, git_env, mock_gh


def _setup_gh_for_pr(
    mock_gh, git_env, branch="test-branch", pr_number=42, ci_status="passing"
):
    """Configure mock_gh to return valid PR info for a test PR URL."""
    from server.config import get_repo

    upstream = get_repo(git_env.repo_id).upstream
    pr_url = f"https://github.com/{upstream}/pull/{pr_number}"
    mock_gh.set_response(
        "pr view",
        0,
        json.dumps(
            {
                "number": pr_number,
                "url": pr_url,
                "headRefName": branch,
            }
        ),
    )
    mock_gh.set_response("pr checks", 0, ci_status)
    return pr_url


async def _create_worktop_via_api(client_tuple, branch="test-branch", pr_number=42):
    """Helper to create a worktop via the API and return the response."""
    client, git_env, mock_gh = client_tuple

    # Push a branch so the worktree can be created from it
    git_env.create_branch(branch)
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push(branch)
    git_env.checkout("main")

    pr_url = _setup_gh_for_pr(mock_gh, git_env, branch, pr_number)
    resp = await client.post("/worktops", json={"pr_url": pr_url})
    return resp


async def test_create_worktop(client):
    resp = await _create_worktop_via_api(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo"] == client[1].repo_id
    assert data["branch"] == "test-branch"
    assert data["pr_number"] == 42
    assert data["status"] == "open"
    assert data["ci_status"] == "passing"
    assert data["worktree_path"]  # non-empty


async def test_list_worktops_empty(client):
    c, _, _ = client
    resp = await c.get("/worktops")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_worktops_after_create(client):
    await _create_worktop_via_api(client)
    c, _, _ = client
    resp = await c.get("/worktops")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_get_worktop(client):
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client
    resp = await c.get(f"/worktops/{worktop_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == worktop_id
    assert "sessions" in data


async def test_get_worktop_not_found(client):
    c, _, _ = client
    resp = await c.get("/worktops/nonexistent")
    assert resp.status_code == 404


async def test_archive_worktop(client):
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client
    resp = await c.post(f"/worktops/{worktop_id}/archive")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"
    assert resp.json()["archived_at"] is not None


async def test_archive_worktop_not_found(client):
    c, _, _ = client
    resp = await c.post("/worktops/nonexistent/archive")
    assert resp.status_code == 404


async def test_set_tends_enabled_toggles_column(client):
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    # Default is True
    assert create_resp.json()["tends_enabled"] is True

    c, _, _ = client
    # Disable
    resp = await c.post(
        f"/worktops/{worktop_id}/tends-enabled", json={"enabled": False}
    )
    assert resp.status_code == 200
    assert resp.json()["tends_enabled"] is False

    # Confirm via GET
    resp = await c.get(f"/worktops/{worktop_id}")
    assert resp.json()["tends_enabled"] is False

    # Re-enable
    resp = await c.post(f"/worktops/{worktop_id}/tends-enabled", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["tends_enabled"] is True


async def test_set_tends_enabled_not_found(client):
    c, _, _ = client
    resp = await c.post("/worktops/nonexistent/tends-enabled", json={"enabled": False})
    assert resp.status_code == 404


async def test_delete_worktop(client):
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client
    resp = await c.delete(f"/worktops/{worktop_id}")
    assert resp.status_code == 200

    # Verify it's gone
    resp = await c.get(f"/worktops/{worktop_id}")
    assert resp.status_code == 404


async def test_delete_worktop_not_found(client):
    c, _, _ = client
    resp = await c.delete("/worktops/nonexistent")
    assert resp.status_code == 404


async def test_create_local_worktop(client):
    """Creating a worktop with just repo should create a local worktop with generic branch."""
    c, git_env, _ = client
    resp = await c.post("/worktops", json={"repo": git_env.repo_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo"] == git_env.repo_id
    assert data["branch"].startswith("worktop/")
    assert data["pr_number"] is None
    assert data["pr_url"] is None
    assert data["status"] == "open"
    assert data["worktree_path"]


async def test_create_worktop_requires_pr_url_or_repo(client):
    c, _, _ = client
    resp = await c.post("/worktops", json={})
    assert resp.status_code == 400


async def test_create_worktop_bad_url(client):
    c, _, _ = client
    resp = await c.post("/worktops", json={"pr_url": "not-a-url"})
    assert resp.status_code == 400


async def test_create_worktop_gh_returns_non_json(client):
    """When gh pr view returns non-JSON output, the API should return 400, not 500."""
    c, git_env, mock_gh = client
    from server.config import get_repo

    upstream = get_repo(git_env.repo_id).upstream
    pr_url = f"https://github.com/{upstream}/pull/99"
    # gh returns success (rc=0) but non-JSON output
    mock_gh.set_response("pr view", 0, "Internal Server Error")

    resp = await c.post("/worktops", json={"pr_url": pr_url})
    assert resp.status_code == 400


async def test_create_worktop_repo_path_missing(client):
    """When the configured repo path doesn't exist on disk, should return 400, not 500."""
    c, git_env, mock_gh = client

    from pathlib import Path

    import server.config as config_module
    from server.models import Repo

    # Add a second repo whose path doesn't exist
    assert config_module._repos_cache is not None
    config_module._repos_cache["ghost"] = Repo(
        id="ghost",
        path=Path("/nonexistent/path"),
        kind="remote",
        upstream="org/ghost",
    )
    pr_url = "https://github.com/org/ghost/pull/1"
    mock_gh.set_response(
        "pr view",
        0,
        json.dumps({"number": 1, "url": pr_url, "headRefName": "fix-bug"}),
    )

    resp = await c.post("/worktops", json={"pr_url": pr_url})
    assert resp.status_code == 400


async def test_list_worktops_filter_by_status(client):
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client

    # Archive the worktop
    await c.post(f"/worktops/{worktop_id}/archive")

    # Filter open — should be empty
    resp = await c.get("/worktops?status=open")
    assert len(resp.json()) == 0

    # Filter archived — should have one
    resp = await c.get("/worktops?status=archived")
    assert len(resp.json()) == 1


async def test_create_slate(client):
    c, git_env, _ = client
    resp = await c.post(
        "/slates",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] is not None  # session created inline with PTY


async def test_list_slates(client):
    c, git_env, _ = client
    await c.post(
        "/slates",
    )
    resp = await c.get("/slates")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "worktop_count" in data[0]


async def test_hook_create_slate_worktop(client):
    """Slate create-worktop hook should create a worktop linked to the slate."""
    c, git_env, _ = client
    resp = await c.post(
        "/slates",
    )
    slate_id = resp.json()["id"]

    resp = await c.post(
        f"/hooks/slates/{slate_id}/create-worktop",
        json={"repo": git_env.repo_id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["worktop_id"]
    assert data["worktree_path"]
    assert data["branch"] == f"slate/{slate_id[:8]}/{git_env.repo_id}"

    # Verify worktop exists and is linked to slate
    resp = await c.get(f"/worktops/{data['worktop_id']}")
    assert resp.json()["slate_id"] == slate_id


async def test_hook_create_slate_worktop_duplicate(client):
    """Creating a duplicate worktop for the same repo in a slate should fail."""
    c, git_env, _ = client
    resp = await c.post(
        "/slates",
    )
    slate_id = resp.json()["id"]

    await c.post(
        f"/hooks/slates/{slate_id}/create-worktop",
        json={"repo": git_env.repo_id},
    )
    resp = await c.post(
        f"/hooks/slates/{slate_id}/create-worktop",
        json={"repo": git_env.repo_id},
    )
    assert resp.status_code == 400


async def test_hook_create_slate_worktop_bad_repo(client):
    c, _, _ = client
    resp = await c.post(
        "/slates",
    )
    slate_id = resp.json()["id"]

    resp = await c.post(
        f"/hooks/slates/{slate_id}/create-worktop",
        json={"repo": "nonexistent"},
    )
    assert resp.status_code == 400


async def test_hook_create_worktop(client):
    """Hook should create a standalone worktop in the given repo."""
    c, git_env, _ = client
    resp = await c.post("/hooks/create-worktop", json={"repo": git_env.repo_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["worktop_id"]
    assert f"/worktops/{data['worktop_id']}" in data["url"]

    # Verify worktop exists and has no slate
    resp = await c.get(f"/worktops/{data['worktop_id']}")
    assert resp.status_code == 200
    assert resp.json()["slate_id"] is None
    assert resp.json()["repo"] == git_env.repo_id


async def test_hook_create_worktop_bad_repo(client):
    c, _, _ = client
    resp = await c.post("/hooks/create-worktop", json={"repo": "nonexistent"})
    assert resp.status_code == 400


async def test_hook_branch_updated(client):
    """Hook should update the worktop's branch in the DB."""
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client

    resp = await c.post(
        f"/hooks/worktops/{worktop_id}/branch-updated",
        json={"branch": "fix-timeout-handling"},
    )
    assert resp.status_code == 200

    # Verify branch was updated
    resp = await c.get(f"/worktops/{worktop_id}")
    assert resp.json()["branch"] == "fix-timeout-handling"


async def test_hook_pr_created(client):
    """Hook should update the worktop's PR info in the DB."""
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client

    resp = await c.post(
        f"/hooks/worktops/{worktop_id}/pr-created",
        json={"pr_url": "https://github.com/org/repo/pull/99", "pr_number": 99},
    )
    assert resp.status_code == 200

    # Verify PR info was updated
    resp = await c.get(f"/worktops/{worktop_id}")
    assert resp.json()["pr_number"] == 99
    assert resp.json()["pr_url"] == "https://github.com/org/repo/pull/99"


async def test_hook_worktop_not_found(client):
    c, _, _ = client
    resp = await c.post(
        "/hooks/worktops/nonexistent/branch-updated",
        json={"branch": "foo"},
    )
    assert resp.status_code == 404


async def test_create_session(client, mock_pty):
    """POST /worktops/:id/sessions should spawn an interactive PTY session."""
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client

    resp = await c.post(
        f"/worktops/{worktop_id}/sessions",
        json={"prompt": "fix the tests"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["worktop_id"] == worktop_id
    assert data["role"] == "user"
    assert data["alive"] is True
    assert data["ended_at"] is None
    assert mock_pty.spawn.called

    # Verify it shows up in worktop sessions
    resp = await c.get(f"/worktops/{worktop_id}/sessions")
    assert len(resp.json()) == 1


async def test_create_session_no_prompt(client, mock_pty):
    """Sessions can be created without a prompt."""
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client

    resp = await c.post(f"/worktops/{worktop_id}/sessions", json={})
    assert resp.status_code == 200
    assert mock_pty.spawn.called


async def test_create_session_worktop_not_found(client, mock_pty):
    c, _, _ = client
    resp = await c.post(
        "/worktops/nonexistent/sessions",
        json={"prompt": "hello"},
    )
    assert resp.status_code == 404


async def test_resume_session(client, mock_pty):
    """POST /worktops/:id/sessions/:sid/resume should spawn a new PTY."""
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client

    # Create a session, then simulate it dying
    resp = await c.post(f"/worktops/{worktop_id}/sessions", json={})
    session_id = resp.json()["id"]

    # Simulate the PTY dying and mark ended in DB
    await mock_pty.terminate(session_id)
    from server import db

    await db.update_session(session_id, ended_at="2024-01-01T00:00:00+00:00")

    resp = await c.post(f"/worktops/{worktop_id}/sessions/{session_id}/resume")
    assert resp.status_code == 200
    assert resp.json()["ended_at"] is None

    # `claude --resume` does not preserve the original --system-prompt, so the
    # resume cmd must re-pass it. Verify the resumed worktop session got one.
    cmd = mock_pty.spawn.call_args.kwargs["cmd"]
    assert "--system-prompt" in cmd


async def test_fork_session(client, mock_pty):
    """POST /worktops/:id/sessions/:sid/fork should spawn an independent
    forked session and leave the source session untouched."""
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client

    # Create a source session.
    resp = await c.post(f"/worktops/{worktop_id}/sessions", json={})
    source_id = resp.json()["id"]
    assert mock_pty.spawn.called

    # Snapshot the source's spawn call so we can confirm it was not re-spawned.
    source_call_count = mock_pty.spawn.call_count

    # Fork it.
    resp = await c.post(f"/worktops/{worktop_id}/sessions/{source_id}/fork")
    assert resp.status_code == 200
    fork = resp.json()
    fork_id = fork["id"]
    assert fork_id != source_id
    assert fork["worktop_id"] == worktop_id
    assert fork["role"] == "user"
    assert fork["trigger"] is None
    assert fork["parent_session_id"] == source_id
    assert fork["alive"] is True

    # The fork command should be `claude --resume <source> --fork-session
    # --session-id <new> --system-prompt ...` in the worktop's worktree.
    cmd = mock_pty.spawn.call_args.kwargs["cmd"]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == source_id
    assert "--fork-session" in cmd
    assert "--session-id" in cmd
    assert cmd[cmd.index("--session-id") + 1] == fork_id
    # --system-prompt must be re-passed (--resume does not preserve it).
    assert "--system-prompt" in cmd

    # The source session should not have been re-spawned by the fork — only
    # the new fork session was. (Source was spawned once, fork once.)
    assert mock_pty.spawn.call_count == source_call_count + 1

    # Worktop session listing should include both source and fork.
    resp = await c.get(f"/worktops/{worktop_id}/sessions")
    ids = {s["id"] for s in resp.json()}
    assert source_id in ids
    assert fork_id in ids


async def test_fork_session_worktop_not_found(client, mock_pty):
    c, _, _ = client
    resp = await c.post("/worktops/nonexistent/sessions/abc/fork")
    assert resp.status_code == 404


async def test_fork_session_source_not_found(client, mock_pty):
    """Forking a nonexistent source session returns 404."""
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client

    resp = await c.post(f"/worktops/{worktop_id}/sessions/nonexistent/fork")
    assert resp.status_code == 404


async def test_fork_daemon_session_becomes_user(client, mock_pty):
    """Forking a daemon (tend) session produces a user-role session.

    Once forked it's user-driven, so the daemon should never see it.
    """
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client

    # Manually insert a daemon-role session to simulate a tend session.
    from server import db
    from server.models import Session, SessionRole

    daemon_source = Session(
        worktop_id=worktop_id,
        role=SessionRole.daemon,
        trigger="tend",
    )
    await db.create_session(daemon_source)

    resp = await c.post(f"/worktops/{worktop_id}/sessions/{daemon_source.id}/fork")
    assert resp.status_code == 200
    fork = resp.json()
    assert fork["role"] == "user"
    assert fork["trigger"] is None
    assert fork["parent_session_id"] == daemon_source.id


async def test_session_dict_includes_parent_session_id(client, mock_pty):
    """The session-listing GET should round-trip parent_session_id."""
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client

    resp = await c.post(f"/worktops/{worktop_id}/sessions", json={})
    source_id = resp.json()["id"]
    # A freshly-created (non-forked) session has parent_session_id == None.
    assert resp.json()["parent_session_id"] is None

    resp = await c.post(f"/worktops/{worktop_id}/sessions/{source_id}/fork")
    fork_id = resp.json()["id"]

    resp = await c.get(f"/worktops/{worktop_id}/sessions")
    by_id = {s["id"]: s for s in resp.json()}
    assert by_id[source_id]["parent_session_id"] is None
    assert by_id[fork_id]["parent_session_id"] == source_id


async def test_resume_slate_session_passes_system_prompt(client, mock_pty):
    """Resuming a slate session must re-pass the slate orchestrator system
    prompt, since `claude --resume` does not preserve it."""
    c, _, _ = client
    resp = await c.post("/slates")
    slate_id = resp.json()["id"]
    session_id = resp.json()["session_id"]

    from server import db

    await mock_pty.terminate(session_id)
    await db.update_session(session_id, ended_at="2024-01-01T00:00:00+00:00")

    resp = await c.post(f"/slates/{slate_id}/sessions/{session_id}/resume")
    assert resp.status_code == 200

    cmd = mock_pty.spawn.call_args.kwargs["cmd"]
    assert "--system-prompt" in cmd
    sp = cmd[cmd.index("--system-prompt") + 1]
    assert "orchestrator" in sp.lower() or "slate" in sp.lower()


async def test_repos_endpoint_includes_kind(client):
    c, _, _ = client
    resp = await c.get("/repos")
    assert resp.status_code == 200
    data = resp.json()
    assert data
    for r in data:
        assert "kind" in r


async def test_hook_pr_created_rejected_for_local_repo(client):
    """The pr-created hook must reject worktops in local-only repos."""
    c, git_env, _ = client
    # Add a local repo, then create a worktop in it.
    import server.config as config_module
    from server.models import Repo

    assert config_module._repos_cache is not None
    config_module._repos_cache["local-r"] = Repo(
        id="local-r",
        path=git_env.clone,
        kind="local",
        upstream=None,
    )
    resp = await c.post("/hooks/create-worktop", json={"repo": "local-r"})
    assert resp.status_code == 200
    worktop_id = resp.json()["worktop_id"]

    resp = await c.post(
        f"/hooks/worktops/{worktop_id}/pr-created",
        json={"pr_url": "https://github.com/x/y/pull/1", "pr_number": 1},
    )
    assert resp.status_code == 400
    assert "local-only" in resp.json()["detail"]


async def test_hook_ci_failure_expected_rejected_for_local_repo(client):
    c, git_env, _ = client
    import server.config as config_module
    from server.models import Repo

    assert config_module._repos_cache is not None
    config_module._repos_cache["local-r2"] = Repo(
        id="local-r2",
        path=git_env.clone,
        kind="local",
        upstream=None,
    )
    resp = await c.post("/hooks/create-worktop", json={"repo": "local-r2"})
    assert resp.status_code == 200
    worktop_id = resp.json()["worktop_id"]

    resp = await c.post(f"/hooks/worktops/{worktop_id}/ci-failure-expected")
    assert resp.status_code == 400
    assert "local-only" in resp.json()["detail"]


async def test_resume_alive_session_fails(client, mock_pty):
    """Resuming an already-alive session should return 400."""
    create_resp = await _create_worktop_via_api(client)
    worktop_id = create_resp.json()["id"]
    c, _, _ = client

    resp = await c.post(f"/worktops/{worktop_id}/sessions", json={})
    session_id = resp.json()["id"]

    # Session is alive (mock default), so resume should fail
    resp = await c.post(f"/worktops/{worktop_id}/sessions/{session_id}/resume")
    assert resp.status_code == 400
