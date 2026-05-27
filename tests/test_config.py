from __future__ import annotations

from pathlib import Path

import pytest

import server.config as config_module
from server import db
from server.models import Repo


def _seed_cache(repos: list[Repo] | None = None, author: str = "") -> None:
    config_module._repos_cache = {r.id: r for r in (repos or [])}
    config_module._author_cache = author


def test_get_repo_returns_cached(tmp_path):
    _seed_cache(
        [Repo(id="r", path=tmp_path, kind="remote", upstream="org/r")],
        author="alice",
    )
    repo = config_module.get_repo("r")
    assert repo.kind == "remote"
    assert repo.upstream == "org/r"
    assert config_module.get_author() == "alice"


def test_get_repo_unknown_raises(tmp_path):
    _seed_cache([Repo(id="r", path=tmp_path, kind="remote", upstream="org/r")])
    with pytest.raises(KeyError, match="Unknown repo"):
        config_module.get_repo("nope")


def test_is_local(tmp_path):
    _seed_cache(
        [
            Repo(id="local", path=tmp_path, kind="local", upstream=None),
            Repo(id="remote", path=tmp_path, kind="remote", upstream="org/r"),
        ]
    )
    assert config_module.is_local("local") is True
    assert config_module.is_local("remote") is False


def test_require_upstream_for_local_raises(tmp_path):
    _seed_cache([Repo(id="local", path=tmp_path, kind="local", upstream=None)])
    with pytest.raises(ValueError, match="local-only"):
        config_module.require_upstream("local")


def test_require_upstream_returns_upstream(tmp_path):
    _seed_cache([Repo(id="r", path=tmp_path, kind="remote", upstream="org/r")])
    assert config_module.require_upstream("r") == "org/r"


def test_uninitialized_cache_raises():
    config_module._repos_cache = None
    with pytest.raises(RuntimeError, match="not initialized"):
        config_module.get_repos()


async def test_refresh_loads_from_db(init_db, tmp_path):
    """refresh() should pick up repos and author written to the DB."""
    _seed_cache([], author="")
    await db.create_repo(
        Repo(
            id="seeded",
            path=Path(tmp_path),
            kind="remote",
            upstream="org/seeded",
            position=0,
        )
    )
    await db.set_setting("author", "alice")

    await config_module.refresh()

    repos = config_module.get_repos()
    assert "seeded" in repos
    assert repos["seeded"].upstream == "org/seeded"
    assert config_module.get_author() == "alice"
