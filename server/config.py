from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tomllib

CONFIG_PATH = Path(__file__).parent.parent / "config.toml"


@dataclass
class Repo:
    id: str
    path: Path
    upstream: str  # GitHub "owner/repo" for gh CLI


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
        repos[repo_id] = Repo(
            id=repo_id,
            path=Path(info["path"]),
            upstream=info["upstream"],
        )
    return repos


def get_repo(repo_id: str) -> Repo:
    repos = get_repos()
    if repo_id not in repos:
        raise KeyError(f"Unknown repo: {repo_id!r}")
    return repos[repo_id]


def get_author() -> str:
    return _get_data().get("author", "")


def reload() -> None:
    global _data
    _data = None
