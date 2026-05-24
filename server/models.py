from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class WorktopStatus(str, Enum):
    open = "open"
    archived = "archived"


class CIStatus(str, Enum):
    unknown = "unknown"
    pending = "pending"
    passing = "passing"
    failing = "failing"


class SyncStatus(str, Enum):
    current = "current"
    behind = "behind"


class SessionRole(str, Enum):
    daemon = "daemon"
    user = "user"


@dataclass
class Worktop:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    slate_id: str | None = None
    repo: str = ""  # repo ID from config.toml
    branch: str = ""
    worktree_path: str = ""
    pr_number: int | None = None
    pr_url: str | None = None
    ci_status: CIStatus = CIStatus.unknown
    ci_failure_expected_sha: str | None = None
    pr_comment_count: int = 0
    pr_reaction_count: int = 0
    sync_status: SyncStatus = SyncStatus.current
    status: WorktopStatus = WorktopStatus.open
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    archived_at: str | None = None
    archive_reason: str | None = None  # "merged", "closed", or None (manual)
    last_activity_at: str | None = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class Slate:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str | None = None
    name: str | None = None
    archived: bool = False
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    worktop_id: str | None = None
    slate_id: str | None = None
    role: SessionRole = SessionRole.user
    trigger: str | None = None
    succeeded: bool | None = None
    transcript: str = ""
    xterm_state: bytes | None = None
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    ended_at: str | None = None
    # Set when this session was created as a fork of another session via the
    # "Fork" UI button. Points to the source session's id.
    parent_session_id: str | None = None
