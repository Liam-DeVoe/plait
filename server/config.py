from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tomllib

CONFIG_PATH = Path(__file__).parent.parent / "config.toml"


@dataclass
class Repo:
    id: str
    path: Path
    kind: str  # "remote" or "local"
    # The GitHub "owner/repo" where PRs live and `main` is authoritative.
    # For "fork-and-PR" workflows this is the *upstream* repo, not the user's
    # fork — the fork is identified at runtime by matching this against the
    # local clone's git remotes (see `git.upstream_remote`). Plait still
    # pushes to whatever remote is named `origin` in the local clone.
    # None for kind == "local".
    upstream: str | None


def _load() -> dict:
    return tomllib.loads(CONFIG_PATH.read_text())


_data: dict | None = None


def _get_data() -> dict:
    global _data
    if _data is None:
        _data = _load()
    return _data


def get_repos() -> dict[str, Repo]:
    data = _get_data()
    repos = {}
    for repo_id, info in data.get("repos", {}).items():
        kind = info.get("kind", "remote")
        if kind not in ("remote", "local"):
            raise ValueError(
                f"Repo {repo_id!r}: kind must be 'remote' or 'local', got {kind!r}"
            )
        upstream = info.get("upstream")
        if kind == "remote" and not upstream:
            raise ValueError(f"Repo {repo_id!r}: kind='remote' requires upstream")
        if kind == "local" and upstream:
            raise ValueError(
                f"Repo {repo_id!r}: kind='local' must not have upstream "
                f"(got {upstream!r})"
            )
        repos[repo_id] = Repo(
            id=repo_id,
            path=Path(info["path"]),
            kind=kind,
            upstream=upstream,
        )
    return repos


def get_repo(repo_id: str) -> Repo:
    repos = get_repos()
    if repo_id not in repos:
        raise KeyError(f"Unknown repo: {repo_id!r}")
    return repos[repo_id]


def is_local(repo_id: str) -> bool:
    return get_repo(repo_id).kind == "local"


def require_upstream(repo_id: str) -> str:
    """Return the upstream for a repo, raising if it's a local repo."""
    repo = get_repo(repo_id)
    if repo.upstream is None:
        raise ValueError(f"Repo {repo_id!r} is local-only — no upstream available")
    return repo.upstream


def get_author() -> str:
    return _get_data().get("author", "")


def reload() -> None:
    global _data
    _data = None
