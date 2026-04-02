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


def _setup_gh_for_pr(mock_gh, git_env, branch="test-branch", pr_number=42):
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
    return pr_url


async def _create_cell_via_api(client_tuple, branch="test-branch", pr_number=42):
    """Helper to create a cell via the API and return the response."""
    client, git_env, mock_gh = client_tuple

    # Push a branch so the worktree can be created from it
    git_env.create_branch(branch)
    git_env.add_commit("file.txt", "content", "add file")
    git_env.push(branch)
    git_env.checkout("main")

    pr_url = _setup_gh_for_pr(mock_gh, git_env, branch, pr_number)
    resp = await client.post("/cells", json={"pr_url": pr_url})
    return resp


async def test_create_cell(client):
    resp = await _create_cell_via_api(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo"] == client[1].repo_id
    assert data["branch"] == "test-branch"
    assert data["pr_number"] == 42
    assert data["status"] == "active"
    assert data["worktree_path"]  # non-empty


async def test_list_cells_empty(client):
    c, _, _ = client
    resp = await c.get("/cells")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_cells_after_create(client):
    await _create_cell_via_api(client)
    c, _, _ = client
    resp = await c.get("/cells")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_get_cell(client):
    create_resp = await _create_cell_via_api(client)
    cell_id = create_resp.json()["id"]
    c, _, _ = client
    resp = await c.get(f"/cells/{cell_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == cell_id
    assert "sessions" in data


async def test_get_cell_not_found(client):
    c, _, _ = client
    resp = await c.get("/cells/nonexistent")
    assert resp.status_code == 404


async def test_archive_cell(client):
    create_resp = await _create_cell_via_api(client)
    cell_id = create_resp.json()["id"]
    c, _, _ = client
    resp = await c.post(f"/cells/{cell_id}/archive")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"
    assert resp.json()["archived_at"] is not None


async def test_archive_cell_not_found(client):
    c, _, _ = client
    resp = await c.post("/cells/nonexistent/archive")
    assert resp.status_code == 404


async def test_delete_cell(client):
    create_resp = await _create_cell_via_api(client)
    cell_id = create_resp.json()["id"]
    c, _, _ = client
    resp = await c.delete(f"/cells/{cell_id}")
    assert resp.status_code == 200

    # Verify it's gone
    resp = await c.get(f"/cells/{cell_id}")
    assert resp.status_code == 404


async def test_delete_cell_not_found(client):
    c, _, _ = client
    resp = await c.delete("/cells/nonexistent")
    assert resp.status_code == 404


async def test_create_local_cell(client):
    """Creating a cell with just repo should create a local cell with generic branch."""
    c, git_env, _ = client
    resp = await c.post("/cells", json={"repo": git_env.repo_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo"] == git_env.repo_id
    assert data["branch"].startswith("cell/")
    assert data["pr_number"] is None
    assert data["pr_url"] is None
    assert data["status"] == "active"
    assert data["worktree_path"]


async def test_create_cell_requires_pr_url_or_repo(client):
    c, _, _ = client
    resp = await c.post("/cells", json={})
    assert resp.status_code == 400


async def test_create_cell_bad_url(client):
    c, _, _ = client
    resp = await c.post("/cells", json={"pr_url": "not-a-url"})
    assert resp.status_code == 400


async def test_create_cell_gh_returns_non_json(client):
    """When gh pr view returns non-JSON output, the API should return 400, not 500."""
    c, git_env, mock_gh = client
    from server.config import get_repo

    upstream = get_repo(git_env.repo_id).upstream
    pr_url = f"https://github.com/{upstream}/pull/99"
    # gh returns success (rc=0) but non-JSON output
    mock_gh.set_response("pr view", 0, "Internal Server Error")

    resp = await c.post("/cells", json={"pr_url": pr_url})
    assert resp.status_code == 400


async def test_create_cell_repo_path_missing(client):
    """When the configured repo path doesn't exist on disk, should return 400, not 500."""
    c, git_env, mock_gh = client
    from pathlib import Path

    import server.config as config_module
    from server.config import Repo

    # Add a second repo whose path doesn't exist
    config_module._repos["ghost"] = Repo(
        id="ghost", path=Path("/nonexistent/path"), upstream="org/ghost"
    )
    pr_url = "https://github.com/org/ghost/pull/1"
    mock_gh.set_response(
        "pr view",
        0,
        json.dumps({"number": 1, "url": pr_url, "headRefName": "fix-bug"}),
    )

    resp = await c.post("/cells", json={"pr_url": pr_url})
    assert resp.status_code == 400


async def test_list_cells_filter_by_status(client):
    create_resp = await _create_cell_via_api(client)
    cell_id = create_resp.json()["id"]
    c, _, _ = client

    # Archive the cell
    await c.post(f"/cells/{cell_id}/archive")

    # Filter active — should be empty
    resp = await c.get("/cells?status=active")
    assert len(resp.json()) == 0

    # Filter archived — should have one
    resp = await c.get("/cells?status=archived")
    assert len(resp.json()) == 1


async def test_create_sortie(client):
    c, git_env, _ = client
    resp = await c.post(
        "/sorties",
        json={"prompt": "update everything"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["prompt"] == "update everything"
    assert data["session_id"] is not None  # session created inline with PTY


async def test_list_sorties(client):
    c, git_env, _ = client
    await c.post("/sorties", json={"prompt": "first"})
    resp = await c.get("/sorties")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "cell_count" in data[0]
    assert "status" in data[0]


async def test_sortie_derived_status(client):
    """Sortie status should be active when session hasn't been created yet."""
    c, git_env, mock_gh = client

    resp = await c.post(
        "/sorties",
        json={"prompt": "update"},
    )
    sortie_id = resp.json()["id"]

    resp = await c.get(f"/sorties/{sortie_id}")
    assert resp.json()["status"] == "active"


async def test_hook_create_sortie_cell(client):
    """Sortie create-cell hook should create a cell linked to the sortie."""
    c, git_env, _ = client
    resp = await c.post("/sorties", json={"prompt": "test"})
    sortie_id = resp.json()["id"]

    resp = await c.post(
        f"/hooks/sorties/{sortie_id}/create-cell",
        json={"repo": git_env.repo_id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["cell_id"]
    assert data["worktree_path"]
    assert data["branch"] == f"sortie/{sortie_id[:8]}/{git_env.repo_id}"

    # Verify cell exists and is linked to sortie
    resp = await c.get(f"/cells/{data['cell_id']}")
    assert resp.json()["sortie_id"] == sortie_id


async def test_hook_create_sortie_cell_duplicate(client):
    """Creating a duplicate cell for the same repo in a sortie should fail."""
    c, git_env, _ = client
    resp = await c.post("/sorties", json={"prompt": "test"})
    sortie_id = resp.json()["id"]

    await c.post(
        f"/hooks/sorties/{sortie_id}/create-cell",
        json={"repo": git_env.repo_id},
    )
    resp = await c.post(
        f"/hooks/sorties/{sortie_id}/create-cell",
        json={"repo": git_env.repo_id},
    )
    assert resp.status_code == 400


async def test_hook_create_sortie_cell_bad_repo(client):
    c, _, _ = client
    resp = await c.post("/sorties", json={"prompt": "test"})
    sortie_id = resp.json()["id"]

    resp = await c.post(
        f"/hooks/sorties/{sortie_id}/create-cell",
        json={"repo": "nonexistent"},
    )
    assert resp.status_code == 400


async def test_hook_branch_updated(client):
    """Hook should update the cell's branch in the DB."""
    create_resp = await _create_cell_via_api(client)
    cell_id = create_resp.json()["id"]
    c, _, _ = client

    resp = await c.post(
        f"/hooks/cells/{cell_id}/branch-updated",
        json={"branch": "fix-timeout-handling"},
    )
    assert resp.status_code == 200

    # Verify branch was updated
    resp = await c.get(f"/cells/{cell_id}")
    assert resp.json()["branch"] == "fix-timeout-handling"


async def test_hook_pr_created(client):
    """Hook should update the cell's PR info in the DB."""
    create_resp = await _create_cell_via_api(client)
    cell_id = create_resp.json()["id"]
    c, _, _ = client

    resp = await c.post(
        f"/hooks/cells/{cell_id}/pr-created",
        json={"pr_url": "https://github.com/org/repo/pull/99", "pr_number": 99},
    )
    assert resp.status_code == 200

    # Verify PR info was updated
    resp = await c.get(f"/cells/{cell_id}")
    assert resp.json()["pr_number"] == 99
    assert resp.json()["pr_url"] == "https://github.com/org/repo/pull/99"


async def test_hook_cell_not_found(client):
    c, _, _ = client
    resp = await c.post(
        "/hooks/cells/nonexistent/branch-updated",
        json={"branch": "foo"},
    )
    assert resp.status_code == 404


async def test_create_session(client, mock_pty):
    """POST /cells/:id/sessions should spawn an interactive PTY session."""
    create_resp = await _create_cell_via_api(client)
    cell_id = create_resp.json()["id"]
    c, _, _ = client

    resp = await c.post(
        f"/cells/{cell_id}/sessions",
        json={"prompt": "fix the tests"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["cell_id"] == cell_id
    assert data["role"] == "user"
    assert data["alive"] is True
    assert data["ended_at"] is None
    assert mock_pty.spawn.called

    # Verify it shows up in cell sessions
    resp = await c.get(f"/cells/{cell_id}/sessions")
    assert len(resp.json()) == 1


async def test_create_session_no_prompt(client, mock_pty):
    """Sessions can be created without a prompt."""
    create_resp = await _create_cell_via_api(client)
    cell_id = create_resp.json()["id"]
    c, _, _ = client

    resp = await c.post(f"/cells/{cell_id}/sessions", json={})
    assert resp.status_code == 200
    assert mock_pty.spawn.called


async def test_create_session_cell_not_found(client, mock_pty):
    c, _, _ = client
    resp = await c.post(
        "/cells/nonexistent/sessions",
        json={"prompt": "hello"},
    )
    assert resp.status_code == 404


async def test_resume_session(client, mock_pty):
    """POST /cells/:id/sessions/:sid/resume should spawn a new PTY."""
    create_resp = await _create_cell_via_api(client)
    cell_id = create_resp.json()["id"]
    c, _, _ = client

    # Create a session, then simulate it dying
    resp = await c.post(f"/cells/{cell_id}/sessions", json={})
    session_id = resp.json()["id"]

    # Simulate the PTY dying and mark ended in DB
    await mock_pty.terminate(session_id)
    from server import db

    await db.update_session(session_id, ended_at="2024-01-01T00:00:00+00:00")

    resp = await c.post(f"/cells/{cell_id}/sessions/{session_id}/resume")
    assert resp.status_code == 200
    assert resp.json()["ended_at"] is None


async def test_resume_alive_session_fails(client, mock_pty):
    """Resuming an already-alive session should return 400."""
    create_resp = await _create_cell_via_api(client)
    cell_id = create_resp.json()["id"]
    c, _, _ = client

    resp = await c.post(f"/cells/{cell_id}/sessions", json={})
    session_id = resp.json()["id"]

    # Session is alive (mock default), so resume should fail
    resp = await c.post(f"/cells/{cell_id}/sessions/{session_id}/resume")
    assert resp.status_code == 400
