from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "repos.json"


@dataclass
class Repo:
    id: str
    path: Path
    upstream: str  # GitHub "owner/repo" for gh CLI


def load_repos(path: Path = CONFIG_PATH) -> dict[str, Repo]:
    data = json.loads(path.read_text())
    repos = {}
    for repo_id, info in data.items():
        repos[repo_id] = Repo(
            id=repo_id,
            path=Path(info["path"]),
            upstream=info["upstream"],
        )
    return repos


_repos: dict[str, Repo] | None = None


def get_repos() -> dict[str, Repo]:
    global _repos
    if _repos is None:
        _repos = load_repos()
    return _repos


def get_repo(repo_id: str) -> Repo:
    repos = get_repos()
    if repo_id not in repos:
        raise KeyError(f"Unknown repo: {repo_id!r}")
    return repos[repo_id]


def reload() -> None:
    global _repos
    _repos = None
