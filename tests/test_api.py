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
    pr_url = f"https://github.com/{git_env.repo_name}/pull/{pr_number}"
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
    assert data["repo"] == client[1].repo_name
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


async def test_create_cell_bad_url(client):
    c, _, _ = client
    resp = await c.post("/cells", json={"pr_url": "not-a-url"})
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
    c, _, _ = client
    resp = await c.post(
        "/sorties",
        json={
            "prompt": "update everything",
            "repos": ["org/a", "org/b"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["prompt"] == "update everything"
    assert data["repos"] == ["org/a", "org/b"]


async def test_list_sorties(client):
    c, _, _ = client
    await c.post("/sorties", json={"prompt": "first", "repos": ["a"]})
    resp = await c.get("/sorties")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
