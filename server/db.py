from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

import aiosqlite

from server.models import (
    CIStatus,
    Session,
    SessionRole,
    Slate,
    SyncStatus,
    Worktop,
    WorktopStatus,
)

DB_PATH: str | Path = Path(__file__).parent.parent / "plait.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS worktops (
    id TEXT PRIMARY KEY,
    slate_id TEXT,
    repo TEXT NOT NULL,
    branch TEXT NOT NULL,
    worktree_path TEXT NOT NULL,
    pr_number INTEGER,
    pr_url TEXT,
    ci_status TEXT NOT NULL DEFAULT 'unknown',
    ci_failure_expected_sha TEXT,
    pr_comment_count INTEGER NOT NULL DEFAULT 0,
    pr_reaction_count INTEGER NOT NULL DEFAULT 0,
    sync_status TEXT NOT NULL DEFAULT 'current',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    archived_at TEXT,
    archive_reason TEXT,
    last_activity_at TEXT,
    FOREIGN KEY (slate_id) REFERENCES slates(id)
);

CREATE TABLE IF NOT EXISTS slates (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    name TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daemon_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    results TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    worktop_id TEXT,
    slate_id TEXT,
    role TEXT NOT NULL DEFAULT 'user',
    trigger_name TEXT,
    succeeded INTEGER,
    transcript TEXT NOT NULL DEFAULT '',
    xterm_state BLOB,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    parent_session_id TEXT REFERENCES sessions(id),
    FOREIGN KEY (worktop_id) REFERENCES worktops(id),
    FOREIGN KEY (slate_id) REFERENCES slates(id)
);

CREATE TABLE IF NOT EXISTS repo_order (
    repo_id TEXT PRIMARY KEY,
    position INTEGER NOT NULL
);
"""

# Process-wide cached connection. Opened lazily on first use, closed on
# shutdown (or between tests, see _use_memory_db fixture). aiosqlite serializes
# all calls on a single worker thread per connection, which is the right
# concurrency model for this single-user app.
_conn: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _conn
    if _conn is None:
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        await conn.executescript(SCHEMA)
        # WAL allows concurrent readers; synchronous=NORMAL is the recommended
        # pairing — durable enough for our use case, faster than FULL.
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        _conn = conn
    return _conn


async def close_db() -> None:
    """Close the cached connection. Used at shutdown and between tests."""
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


async def init_db() -> None:
    db = await get_db()
    for migration in [
        "ALTER TABLE sessions ADD COLUMN xterm_state BLOB",
        "ALTER TABLE sessions ADD COLUMN slate_id TEXT",
        "ALTER TABLE slates ADD COLUMN session_id TEXT",
        "ALTER TABLE worktops ADD COLUMN pr_reaction_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE slates DROP COLUMN status",
        "ALTER TABLE worktops ADD COLUMN last_activity_at TEXT",
        "ALTER TABLE worktops ADD COLUMN ci_failure_expected_sha TEXT",
        "ALTER TABLE slates ADD COLUMN name TEXT",
        "ALTER TABLE slates ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
    ]:
        try:
            await db.execute(migration)
            await db.commit()
        except Exception:
            pass


# --- Worktops ---


async def create_worktop(worktop: Worktop) -> Worktop:
    db = await get_db()
    await db.execute(
        """INSERT INTO worktops (id, slate_id, repo, branch, worktree_path,
           pr_number, pr_url, ci_status, ci_failure_expected_sha,
           pr_comment_count, pr_reaction_count,
           sync_status, status, created_at, archived_at, archive_reason,
           last_activity_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            worktop.id,
            worktop.slate_id,
            worktop.repo,
            worktop.branch,
            worktop.worktree_path,
            worktop.pr_number,
            worktop.pr_url,
            worktop.ci_status.value,
            worktop.ci_failure_expected_sha,
            worktop.pr_comment_count,
            worktop.pr_reaction_count,
            worktop.sync_status.value,
            worktop.status.value,
            worktop.created_at,
            worktop.archived_at,
            worktop.archive_reason,
            worktop.last_activity_at,
        ),
    )
    await db.commit()
    return worktop


async def list_worktops(status: WorktopStatus | None = None) -> list[Worktop]:
    db = await get_db()
    if status:
        cursor = await db.execute(
            "SELECT * FROM worktops WHERE status = ? ORDER BY created_at DESC",
            (status.value,),
        )
    else:
        cursor = await db.execute("SELECT * FROM worktops ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    return [_row_to_worktop(row) for row in rows]


async def get_worktop(worktop_id: str) -> Worktop | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM worktops WHERE id = ?", (worktop_id,))
    row = await cursor.fetchone()
    return _row_to_worktop(row) if row else None


async def update_worktop(worktop_id: str, **kwargs: object) -> Worktop | None:
    db = await get_db()
    sets = []
    values = []
    for key, value in kwargs.items():
        if isinstance(value, Enum):
            value = value.value
        sets.append(f"{key} = ?")
        values.append(value)
    values.append(worktop_id)
    await db.execute(
        f"UPDATE worktops SET {', '.join(sets)} WHERE id = ?",
        values,
    )
    await db.commit()
    return await get_worktop(worktop_id)


async def delete_worktop(worktop_id: str) -> bool:
    db = await get_db()
    cursor = await db.execute("DELETE FROM worktops WHERE id = ?", (worktop_id,))
    await db.commit()
    return cursor.rowcount > 0


def _row_to_worktop(row: aiosqlite.Row) -> Worktop:
    return Worktop(
        id=row["id"],
        slate_id=row["slate_id"],
        repo=row["repo"],
        branch=row["branch"],
        worktree_path=row["worktree_path"],
        pr_number=row["pr_number"],
        pr_url=row["pr_url"],
        ci_status=CIStatus(row["ci_status"]),
        ci_failure_expected_sha=row["ci_failure_expected_sha"],
        pr_comment_count=row["pr_comment_count"],
        pr_reaction_count=row["pr_reaction_count"],
        sync_status=SyncStatus(row["sync_status"]),
        status=WorktopStatus(row["status"]),
        created_at=row["created_at"],
        archived_at=row["archived_at"],
        archive_reason=row["archive_reason"],
        last_activity_at=row["last_activity_at"],
    )


# --- Sessions ---


async def create_session(session: Session) -> Session:
    db = await get_db()
    succeeded_val = None if session.succeeded is None else int(session.succeeded)
    await db.execute(
        """INSERT INTO sessions (id, worktop_id, slate_id, role, trigger_name,
           succeeded, transcript, xterm_state, started_at, ended_at,
           parent_session_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session.id,
            session.worktop_id,
            session.slate_id,
            session.role.value,
            session.trigger,
            succeeded_val,
            session.transcript,
            session.xterm_state,
            session.started_at,
            session.ended_at,
            session.parent_session_id,
        ),
    )
    await db.commit()
    return session


async def list_sessions(worktop_id: str) -> list[Session]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM sessions WHERE worktop_id = ? ORDER BY started_at ASC",
        (worktop_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_session(row) for row in rows]


async def list_running_tend_worktop_ids() -> set[str]:
    """Return the set of worktop IDs with an active (un-ended) tend session.

    Single aggregate query — avoids the N+1 you'd get from calling
    `list_sessions` for each worktop just to derive tend_status.
    """
    db = await get_db()
    cursor = await db.execute(
        "SELECT DISTINCT worktop_id FROM sessions "
        "WHERE trigger_name = 'tend' AND ended_at IS NULL "
        "AND worktop_id IS NOT NULL"
    )
    rows = await cursor.fetchall()
    return {row["worktop_id"] for row in rows}


async def update_session(session_id: str, **kwargs: object) -> Session | None:
    db = await get_db()
    sets = []
    values = []
    for key, value in kwargs.items():
        if isinstance(value, Enum):
            value = value.value
        sets.append(f"{key} = ?")
        values.append(value)
    values.append(session_id)
    await db.execute(
        f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?",
        values,
    )
    await db.commit()
    cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    return _row_to_session(row) if row else None


async def delete_session(session_id: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    await db.commit()


async def get_session(session_id: str) -> Session | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    return _row_to_session(row) if row else None


def _row_to_session(row: aiosqlite.Row) -> Session:
    succeeded_raw = row["succeeded"]
    return Session(
        id=row["id"],
        worktop_id=row["worktop_id"],
        slate_id=row["slate_id"],
        role=SessionRole(row["role"]),
        trigger=row["trigger_name"],
        succeeded=None if succeeded_raw is None else bool(succeeded_raw),
        transcript=row["transcript"],
        xterm_state=row["xterm_state"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        parent_session_id=row["parent_session_id"],
    )


# --- Slates ---


async def create_slate(slate: Slate) -> Slate:
    db = await get_db()
    await db.execute(
        """INSERT INTO slates (id, session_id, name, archived, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            slate.id,
            slate.session_id,
            slate.name,
            int(slate.archived),
            slate.created_at,
        ),
    )
    await db.commit()
    return slate


async def list_slates() -> list[Slate]:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM slates ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    return [_row_to_slate(row) for row in rows]


async def get_slate(slate_id: str) -> Slate | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM slates WHERE id = ?", (slate_id,))
    row = await cursor.fetchone()
    return _row_to_slate(row) if row else None


def _row_to_slate(row: aiosqlite.Row) -> Slate:
    return Slate(
        id=row["id"],
        session_id=row["session_id"],
        name=row["name"],
        archived=bool(row["archived"]),
        created_at=row["created_at"],
    )


async def update_slate(slate_id: str, **kwargs: object) -> Slate | None:
    conn = await get_db()
    sets = []
    values = []
    for key, value in kwargs.items():
        if isinstance(value, Enum):
            value = value.value
        sets.append(f"{key} = ?")
        values.append(value)
    values.append(slate_id)
    await conn.execute(
        f"UPDATE slates SET {', '.join(sets)} WHERE id = ?",
        values,
    )
    await conn.commit()
    return await get_slate(slate_id)


async def list_worktops_by_slate(slate_id: str) -> list[Worktop]:
    conn = await get_db()
    cursor = await conn.execute(
        "SELECT * FROM worktops WHERE slate_id = ? ORDER BY created_at DESC",
        (slate_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_worktop(row) for row in rows]


async def delete_slate(slate_id: str) -> None:
    conn = await get_db()
    # Delete slate-level sessions (orchestrator etc)
    await conn.execute("DELETE FROM sessions WHERE slate_id = ?", (slate_id,))
    # Detach worktops — they continue to exist independently
    await conn.execute(
        "UPDATE worktops SET slate_id = NULL WHERE slate_id = ?",
        (slate_id,),
    )
    await conn.execute("DELETE FROM slates WHERE id = ?", (slate_id,))
    await conn.commit()


# --- Daemon Ticks ---

MAX_RUNS = 100


async def create_daemon_run(
    tick_id: str, started_at: str, ended_at: str, results: list[dict]
) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO daemon_runs (id, started_at, ended_at, results) VALUES (?, ?, ?, ?)",
        (tick_id, started_at, ended_at, json.dumps(results)),
    )
    # Prune old ticks beyond MAX_RUNS
    await db.execute(
        """DELETE FROM daemon_runs WHERE id NOT IN (
            SELECT id FROM daemon_runs ORDER BY started_at DESC LIMIT ?
        )""",
        (MAX_RUNS,),
    )
    await db.commit()


async def get_repo_order() -> list[str]:
    db = await get_db()
    cursor = await db.execute("SELECT repo_id FROM repo_order ORDER BY position ASC")
    rows = await cursor.fetchall()
    return [row["repo_id"] for row in rows]


async def set_repo_order(order: list[str]) -> None:
    db = await get_db()
    await db.execute("DELETE FROM repo_order")
    if order:
        await db.executemany(
            "INSERT INTO repo_order (repo_id, position) VALUES (?, ?)",
            [(repo_id, idx) for idx, repo_id in enumerate(order)],
        )
    await db.commit()


async def list_daemon_runs(limit: int = 20) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM daemon_runs ORDER BY started_at DESC LIMIT ?",
        (limit,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": row["id"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "results": json.loads(row["results"]),
        }
        for row in rows
    ]
