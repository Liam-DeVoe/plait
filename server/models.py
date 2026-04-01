from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class CellStatus(str, Enum):
    active = "active"
    archived = "archived"


class CIStatus(str, Enum):
    unknown = "unknown"
    pending = "pending"
    passing = "passing"
    failing = "failing"


class RebaseStatus(str, Enum):
    current = "current"
    rebasing = "rebasing"
    conflict = "conflict"
    failed = "failed"


class SortieStatus(str, Enum):
    active = "active"
    completed = "completed"


class SessionStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"


class SessionRole(str, Enum):
    daemon = "daemon"
    user = "user"


@dataclass
class Cell:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sortie_id: str | None = None
    repo: str = ""  # e.g. "hegeldev/hegel-rust"
    branch: str = ""
    worktree_path: str = ""
    pr_number: int | None = None
    pr_url: str | None = None
    ci_status: CIStatus = CIStatus.unknown
    rebase_status: RebaseStatus = RebaseStatus.current
    status: CellStatus = CellStatus.active
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    archived_at: str | None = None


@dataclass
class Sortie:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    prompt: str = ""
    repos: list[str] = field(default_factory=list)
    status: SortieStatus = SortieStatus.active
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    cell_id: str = ""
    role: SessionRole = SessionRole.user
    trigger: str | None = None
    status: SessionStatus = SessionStatus.running
    transcript: str = ""  # JSON-encoded list of messages
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    ended_at: str | None = None
