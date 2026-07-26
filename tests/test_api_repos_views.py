"""Tests for the repos / views / settings endpoints and view-scoped slates."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from server.api import app


@pytest.fixture
async def client(init_db, git_env, mock_gh):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, git_env, mock_gh


# --- Repos CRUD ---


async def test_list_repos_includes_position(client):
    c, _, _ = client
    resp = await c.get("/repos")
    assert resp.status_code == 200
    data = resp.json()
    # The git_env fixture seeds one repo with position=0.
    assert any(r["id"] == "testrepo" and r["position"] == 0 for r in data)


async def test_create_repo_remote(client, tmp_path):
    c, _, _ = client
    resp = await c.post(
        "/repos",
        json={
            "id": "new-remote",
            "path": str(tmp_path),
            "kind": "remote",
            "upstream": "org/new-remote",
        },
    )
    # warning is acceptable: the path isn't a real git clone, so upstream
    # validation may complain. We only care that the row was created.
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "new-remote"
    assert data["kind"] == "remote"
    # Cache was refreshed so subsequent calls see the new repo.
    repos = (await c.get("/repos")).json()
    assert any(r["id"] == "new-remote" for r in repos)


async def test_create_repo_local(client, tmp_path):
    c, _, _ = client
    resp = await c.post(
        "/repos",
        json={"id": "new-local", "path": str(tmp_path), "kind": "local"},
    )
    assert resp.status_code == 200
    assert resp.json()["kind"] == "local"
    assert resp.json()["upstream"] is None


async def test_create_repo_remote_without_upstream_rejected(client, tmp_path):
    c, _, _ = client
    resp = await c.post(
        "/repos",
        json={"id": "bad", "path": str(tmp_path), "kind": "remote"},
    )
    assert resp.status_code == 400
    assert "upstream" in resp.json()["detail"]


async def test_create_repo_local_with_upstream_rejected(client, tmp_path):
    c, _, _ = client
    resp = await c.post(
        "/repos",
        json={
            "id": "bad",
            "path": str(tmp_path),
            "kind": "local",
            "upstream": "org/bad",
        },
    )
    assert resp.status_code == 400
    assert "must not have upstream" in resp.json()["detail"]


async def test_create_repo_duplicate_rejected(client, tmp_path):
    c, _, _ = client
    # The git_env fixture already has "testrepo" — but only in the in-memory
    # cache, not the DB. Insert directly so the duplicate check fires.
    from server import db
    from server.models import Repo

    await db.create_repo(Repo(id="dupe", path=tmp_path, kind="local", upstream=None))
    resp = await c.post(
        "/repos",
        json={"id": "dupe", "path": str(tmp_path), "kind": "local"},
    )
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


async def test_update_repo_path(client, tmp_path):
    c, _, _ = client
    from server import db
    from server.models import Repo

    await db.create_repo(Repo(id="r1", path=tmp_path, kind="local", upstream=None))
    new_path = tmp_path / "moved"
    new_path.mkdir()
    resp = await c.put(f"/repos/r1", json={"path": str(new_path)})
    assert resp.status_code == 200
    assert resp.json()["path"] == str(new_path)


async def test_create_repo_with_copy_globs(client):
    c, git_env, _ = client
    git_env.add_commit(".gitignore", "secret.txt\n", "ignore")
    (git_env.clone / "secret.txt").write_text("shh")
    resp = await c.post(
        "/repos",
        json={
            "id": "copyrepo",
            "path": str(git_env.clone),
            "kind": "local",
            "copy_globs": ["secret.txt"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["copy_globs"] == ["secret.txt"]
    repos = (await c.get("/repos")).json()
    assert any(
        r["id"] == "copyrepo" and r["copy_globs"] == ["secret.txt"] for r in repos
    )


async def test_create_repo_rejects_dead_copy_glob(client):
    c, git_env, _ = client
    resp = await c.post(
        "/repos",
        json={
            "id": "copyrepo",
            "path": str(git_env.clone),
            "kind": "local",
            "copy_globs": ["does-not-exist/**"],
        },
    )
    assert resp.status_code == 400
    assert "matches no files" in resp.json()["detail"]


async def test_create_repo_rejects_tracked_copy_glob(client):
    c, git_env, _ = client
    resp = await c.post(
        "/repos",
        json={
            "id": "copyrepo",
            "path": str(git_env.clone),
            "kind": "local",
            "copy_globs": ["README.md"],
        },
    )
    assert resp.status_code == 400
    assert "tracked by git" in resp.json()["detail"]


async def test_update_repo_copy_globs_validated(client):
    """Updating copy_globs re-validates against the clone; a dead glob is a
    400 at save time, a valid one persists."""
    c, git_env, _ = client
    from server import db
    from server.models import Repo

    git_env.add_commit(".gitignore", "secret.txt\n", "ignore")
    (git_env.clone / "secret.txt").write_text("shh")
    await db.create_repo(
        Repo(id="copyrepo", path=git_env.clone, kind="local", upstream=None)
    )

    resp = await c.put("/repos/copyrepo", json={"copy_globs": ["nope*"]})
    assert resp.status_code == 400
    assert "matches no files" in resp.json()["detail"]

    resp = await c.put("/repos/copyrepo", json={"copy_globs": ["secret.txt"]})
    assert resp.status_code == 200
    assert resp.json()["copy_globs"] == ["secret.txt"]
    refetched = await db.get_repo("copyrepo")
    assert refetched.copy_globs == ["secret.txt"]


async def test_repo_metr_flag_roundtrips(client, tmp_path):
    c, _, _ = client
    from server import db

    resp = await c.post(
        "/repos",
        json={"id": "study", "path": str(tmp_path), "kind": "local", "metr": True},
    )
    assert resp.status_code == 200
    assert resp.json()["metr"] is True

    resp = await c.put("/repos/study", json={"metr": False})
    assert resp.status_code == 200
    assert resp.json()["metr"] is False
    refetched = await db.get_repo("study")
    assert refetched.metr is False


async def test_delete_repo_cascade(client, mock_pty, tmp_path):
    """Deleting a repo deletes its worktops and removes it from views."""
    c, git_env, _ = client
    from server import db
    from server.models import Repo, View, Worktop

    # Seed a repo in the DB so the cascade has something to delete.
    await db.create_repo(Repo(id="cascade", path=tmp_path, kind="local", upstream=None))
    # Add a worktop in that repo.
    worktop = Worktop(repo="cascade", branch="b", worktree_path="/tmp/x")
    await db.create_worktop(worktop)
    # Add a view containing the repo.
    view = View(name="my-view", repo_ids=["cascade"])
    await db.create_view(view)

    resp = await c.delete(f"/repos/cascade")
    assert resp.status_code == 200
    body = resp.json()
    assert body["worktops_deleted"] == 1

    # Worktop row gone.
    assert await db.get_worktop(worktop.id) is None
    # View no longer references the deleted repo.
    refetched = await db.get_view(view.id)
    assert refetched is not None
    assert "cascade" not in refetched.repo_ids
    # Repo gone.
    assert await db.get_repo("cascade") is None


async def test_repo_order_update(client, tmp_path):
    c, _, _ = client
    from server import config, db
    from server.models import Repo

    await db.create_repo(
        Repo(id="a", path=tmp_path, kind="local", upstream=None, position=1)
    )
    await db.create_repo(
        Repo(id="b", path=tmp_path, kind="local", upstream=None, position=2)
    )
    # The order endpoint validates against the config cache, not the DB.
    await config.refresh()

    resp = await c.put("/repos/order", json={"order": ["b", "a"]})
    assert resp.status_code == 200
    rows = await db.list_repos()
    by_id = {r.id: r for r in rows}
    assert by_id["b"].position < by_id["a"].position


# --- Views CRUD ---


async def test_create_view(client, tmp_path):
    c, _, _ = client
    from server import db
    from server.models import Repo

    await db.create_repo(Repo(id="r-a", path=tmp_path, kind="local", upstream=None))
    await db.create_repo(Repo(id="r-b", path=tmp_path, kind="local", upstream=None))
    # Refresh cache so the validate step sees the new repos.
    from server import config

    await config.refresh()

    resp = await c.post(
        "/views",
        json={"name": "Backend", "repo_ids": ["r-a", "r-b"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Backend"
    assert set(data["repo_ids"]) == {"r-a", "r-b"}


async def test_create_view_with_unknown_repo_rejected(client):
    c, _, _ = client
    resp = await c.post(
        "/views",
        json={"name": "Bad", "repo_ids": ["nope"]},
    )
    assert resp.status_code == 400


async def test_create_view_named_all_rejected(client):
    c, _, _ = client
    resp = await c.post("/views", json={"name": "All", "repo_ids": []})
    assert resp.status_code == 400
    assert "reserved" in resp.json()["detail"]


async def test_create_view_duplicate_name_rejected(client):
    c, _, _ = client
    await c.post("/views", json={"name": "X", "repo_ids": []})
    resp = await c.post("/views", json={"name": "X", "repo_ids": []})
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


async def test_update_view(client, tmp_path):
    c, _, _ = client
    create = await c.post("/views", json={"name": "Original", "repo_ids": []})
    vid = create.json()["id"]

    resp = await c.put(f"/views/{vid}", json={"name": "Renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"


async def test_delete_view_empty(client):
    """Deleting a view with no attached slates succeeds."""
    c, _, _ = client
    create = await c.post("/views", json={"name": "Doomed", "repo_ids": []})
    vid = create.json()["id"]

    resp = await c.delete(f"/views/{vid}")
    assert resp.status_code == 200

    resp = await c.get("/views")
    assert all(v["id"] != vid for v in resp.json())


async def test_delete_view_blocked_when_slates_attached(client, tmp_path):
    """Deleting a view that has slates attached is blocked with 400."""
    c, _, _ = client
    from unittest.mock import patch

    from server import config, db
    from server.models import Repo

    await db.create_repo(Repo(id="r-keep", path=tmp_path, kind="local", upstream=None))
    await config.refresh()

    create = await c.post("/views", json={"name": "Anchor", "repo_ids": ["r-keep"]})
    vid = create.json()["id"]

    async def _noop(*args, **kwargs):
        return {}

    with patch("server.git.create_slate_worktrees", side_effect=_noop):
        resp = await c.post("/slates", json={"view_id": vid})
    assert resp.status_code == 200

    resp = await c.delete(f"/views/{vid}")
    assert resp.status_code == 400
    assert "Anchor" in resp.json()["detail"]
    assert "1 slate" in resp.json()["detail"]

    # And the view should still be there.
    resp = await c.get("/views")
    assert any(v["id"] == vid for v in resp.json())


# --- Settings ---


async def test_get_settings_initial(client):
    c, _, _ = client
    resp = await c.get("/settings")
    assert resp.status_code == 200
    # Empty DB initially.
    assert resp.json() == {"author": ""}


async def test_update_settings_author(client):
    c, _, _ = client
    resp = await c.put("/settings", json={"author": "alice"})
    assert resp.status_code == 200
    assert resp.json()["author"] == "alice"
    # The config cache is refreshed too.
    from server import config

    assert config.get_author() == "alice"


# --- Slate scoping via view ---


async def test_create_slate_with_view_snapshots_repo_ids(client, tmp_path):
    """Creating a slate from a view should copy the view's repo_ids onto
    the slate. Editing the view later must not change the slate's scope.
    """
    c, git_env, _ = client
    from server import db
    from server.models import Repo

    # Seed two extra local repos so the slate has something concrete to
    # snapshot besides the git_env testrepo.
    await db.create_repo(Repo(id="extra-a", path=tmp_path, kind="local", upstream=None))
    await db.create_repo(Repo(id="extra-b", path=tmp_path, kind="local", upstream=None))
    from server import config

    await config.refresh()

    create_view = await c.post(
        "/views",
        json={"name": "subset", "repo_ids": ["extra-a", "extra-b"]},
    )
    view_id = create_view.json()["id"]

    # Stub git so the slate creation doesn't try to actually run git on
    # the non-existent extra repos. We don't care about success here —
    # the test is about snapshot semantics, not worktree creation.
    from unittest.mock import patch

    async def _noop(*args, **kwargs):
        return {}

    with patch("server.git.create_slate_worktrees", side_effect=_noop):
        resp = await c.post("/slates", json={"view_id": view_id})
    assert resp.status_code == 200
    slate_data = resp.json()
    assert set(slate_data["repo_ids"]) == {"extra-a", "extra-b"}
    assert slate_data["view_id"] == view_id

    # Mutate the view — slate scope must not change.
    await c.put(f"/views/{view_id}", json={"repo_ids": []})
    refetched = await db.get_slate(slate_data["id"])
    assert refetched is not None
    assert set(refetched.repo_ids) == {"extra-a", "extra-b"}


async def test_create_slate_with_repo_ids_override(client, tmp_path):
    """Explicit repo_ids overrides the view's snapshot, but view_id is
    still required and stored on the slate."""
    c, _, _ = client
    from server import config, db
    from server.models import Repo

    await db.create_repo(Repo(id="x", path=tmp_path, kind="local", upstream=None))
    await db.create_repo(Repo(id="y", path=tmp_path, kind="local", upstream=None))
    await config.refresh()

    create_view = await c.post(
        "/views",
        json={"name": "wider", "repo_ids": ["x", "y"]},
    )
    view_id = create_view.json()["id"]

    from unittest.mock import patch

    async def _noop(*args, **kwargs):
        return {}

    with patch("server.git.create_slate_worktrees", side_effect=_noop):
        resp = await c.post(
            "/slates",
            json={"view_id": view_id, "repo_ids": ["x"]},
        )
    assert resp.status_code == 200
    assert resp.json()["repo_ids"] == ["x"]
    assert resp.json()["view_id"] == view_id


async def test_create_slate_without_view_id_rejected(client):
    """view_id is required — POST /slates without it must fail."""
    c, _, _ = client
    resp = await c.post("/slates", json={})
    assert resp.status_code == 422  # pydantic validation error

    # Also fail if only repo_ids is given.
    resp = await c.post("/slates", json={"repo_ids": ["whatever"]})
    assert resp.status_code == 422


async def test_create_slate_empty_scope_rejected(client):
    """If view_id resolves to no repos, fail with a clear message."""
    c, _, _ = client
    create_view = await c.post("/views", json={"name": "empty", "repo_ids": []})
    view_id = create_view.json()["id"]
    resp = await c.post("/slates", json={"view_id": view_id})
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()
