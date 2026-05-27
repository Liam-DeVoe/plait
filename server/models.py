from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


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


class MergeableState(str, Enum):
    """GitHub's `mergeable_state` field on a pull request.

    Meanings:
      - clean: no conflicts, all required checks pass
      - dirty: merge conflict
      - unknown: GitHub is still computing (typically right after a push)
      - behind: head is behind base and branch protection requires
        up-to-date branches (strict mode)
      - blocked: blocked by some other branch protection rule —
        required check failing/pending, missing required review,
        codeowners, ruleset
      - unstable: a non-required check is failing/pending; can still merge
      - has_hooks: repo has custom pre-receive hooks (mergeable); rare,
        primarily seen on GitHub Enterprise
      - draft: PR is marked draft
    """

    clean = "clean"
    dirty = "dirty"
    unknown = "unknown"
    behind = "behind"
    blocked = "blocked"
    unstable = "unstable"
    has_hooks = "has_hooks"
    draft = "draft"


class SessionRole(str, Enum):
    daemon = "daemon"
    user = "user"


@dataclass
class Worktop:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    slate_id: str | None = None
    repo: str = ""  # repo ID; references repos.id in the DB
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
    # When False, the daemon will not auto-spawn tend sessions for this
    # worktop. Manual tends (via the API/UI) bypass this gate.
    tends_enabled: bool = True


@dataclass
class Slate:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str | None = None
    name: str | None = None
    archived: bool = False
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    # Snapshot of repo IDs the slate operates on, taken at creation time.
    # Editing or deleting the source view later does NOT change this list —
    # the snapshot is the slate's authoritative scope. Empty list means
    # "no scope set" (legacy behavior: spans every configured repo).
    repo_ids: list[str] = field(default_factory=list)
    # The view this slate was created from, if any. Purely for display
    # ("Created from view: X"). The actual scope is `repo_ids`.
    view_id: str | None = None


@dataclass
class Repo:
    id: str
    path: Path
    kind: str  # "remote" or "local"
    upstream: str | None  # None iff kind == "local"
    position: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class View:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    repo_ids: list[str] = field(default_factory=list)
    position: int = 0
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
