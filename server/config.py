"""Cached, synchronous access to the repo configuration.

The data lives in the SQLite DB (tables `repos`, `settings`). This module
provides a thread-of-execution-friendly synchronous facade: callers like
`git.py` and `claude.py` read repo metadata in the middle of doing other
work without having to plumb async DB calls through every helper.

The cache is primed at server startup via `await refresh()` (in the
FastAPI lifespan), and explicitly refreshed after every write that
mutates the underlying tables. Reads are cheap dict lookups; writes go
through `db.py` and then call `await refresh()`.

The previous incarnation of this module read `config.toml` at module
import; that file no longer exists. Repos are managed entirely through
the Repos page in the UI, with a one-shot migration script
(`scripts/seed_config.py`) handling the initial port from the old TOML.
"""

from __future__ import annotations

from server.models import Repo

_repos_cache: dict[str, Repo] | None = None
_author_cache: str = ""


async def refresh() -> None:
    """Reload caches from the DB. Call at startup and after any write."""
    global _repos_cache, _author_cache
    from server import db

    repos = await db.list_repos()
    _repos_cache = {r.id: r for r in repos}
    author = await db.get_setting("author")
    _author_cache = author or ""


def _require_cache() -> dict[str, Repo]:
    if _repos_cache is None:
        raise RuntimeError(
            "config cache not initialized; call `await config.refresh()` first "
            "(this is done automatically in the FastAPI lifespan)"
        )
    return _repos_cache


def get_repos() -> dict[str, Repo]:
    """Return all known repos, keyed by ID, in canonical order.

    Order matches the `position` column on the `repos` table.
    """
    cache = _require_cache()
    # Preserve insertion order: list_repos() in db.py returns sorted by
    # position, so the cache dict already iterates in the right order.
    return cache


def get_repo(repo_id: str) -> Repo:
    cache = _require_cache()
    if repo_id not in cache:
        raise KeyError(f"Unknown repo: {repo_id!r}")
    return cache[repo_id]


def is_local(repo_id: str) -> bool:
    return get_repo(repo_id).kind == "local"


def require_upstream(repo_id: str) -> str:
    """Return the upstream for a repo, raising if it's a local repo."""
    repo = get_repo(repo_id)
    if repo.upstream is None:
        raise ValueError(f"Repo {repo_id!r} is local-only — no upstream available")
    return repo.upstream


def get_author() -> str:
    return _author_cache
