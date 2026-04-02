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


class SyncStatus(str, Enum):
    current = "current"
    syncing = "syncing"
    conflict = "conflict"
    failed = "failed"


class SortieStatus(str, Enum):
    active = "active"
    completed = "completed"


class SessionRole(str, Enum):
    daemon = "daemon"
    user = "user"


@dataclass
class Cell:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sortie_id: str | None = None
    repo: str = ""  # repo ID from config.toml
    branch: str = ""
    worktree_path: str = ""
    pr_number: int | None = None
    pr_url: str | None = None
    ci_status: CIStatus = CIStatus.unknown
    pr_comment_count: int = 0
    pr_reaction_count: int = 0
    sync_status: SyncStatus = SyncStatus.current
    status: CellStatus = CellStatus.active
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    archived_at: str | None = None


@dataclass
class Sortie:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    prompt: str = ""
    session_id: str | None = None
    status: SortieStatus = SortieStatus.active
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    cell_id: str | None = None
    sortie_id: str | None = None
    role: SessionRole = SessionRole.user
    trigger: str | None = None
    succeeded: bool | None = None
    transcript: str = ""
    xterm_state: bytes | None = None
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    ended_at: str | None = None
