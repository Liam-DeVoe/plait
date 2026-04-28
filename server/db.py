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
    FOREIGN KEY (worktop_id) REFERENCES worktops(id),
    FOREIGN KEY (slate_id) REFERENCES slates(id)
);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    return db


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
    await db.close()


# --- Worktops ---


async def create_worktop(worktop: Worktop) -> Worktop:
    db = await get_db()
    try:
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
    finally:
        await db.close()


async def list_worktops(status: WorktopStatus | None = None) -> list[Worktop]:
    db = await get_db()
    try:
        if status:
            cursor = await db.execute(
                "SELECT * FROM worktops WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            )
        else:
            cursor = await db.execute("SELECT * FROM worktops ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [_row_to_worktop(row) for row in rows]
    finally:
        await db.close()


async def get_worktop(worktop_id: str) -> Worktop | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM worktops WHERE id = ?", (worktop_id,))
        row = await cursor.fetchone()
        return _row_to_worktop(row) if row else None
    finally:
        await db.close()


async def update_worktop(worktop_id: str, **kwargs: object) -> Worktop | None:
    db = await get_db()
    try:
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
    finally:
        await db.close()


async def delete_worktop(worktop_id: str) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM worktops WHERE id = ?", (worktop_id,))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


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
    try:
        succeeded_val = None if session.succeeded is None else int(session.succeeded)
        await db.execute(
            """INSERT INTO sessions (id, worktop_id, slate_id, role, trigger_name,
               succeeded, transcript, xterm_state, started_at, ended_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            ),
        )
        await db.commit()
        return session
    finally:
        await db.close()


async def list_sessions(worktop_id: str) -> list[Session]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE worktop_id = ? ORDER BY started_at ASC",
            (worktop_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_session(row) for row in rows]
    finally:
        await db.close()


async def update_session(session_id: str, **kwargs: object) -> Session | None:
    db = await get_db()
    try:
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
    finally:
        await db.close()


async def delete_session(session_id: str) -> None:
    db = await get_db()
    try:
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await db.commit()
    finally:
        await db.close()


async def get_session(session_id: str) -> Session | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = await cursor.fetchone()
        return _row_to_session(row) if row else None
    finally:
        await db.close()


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
    )


# --- Slates ---


async def create_slate(slate: Slate) -> Slate:
    db = await get_db()
    try:
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
    finally:
        await db.close()


async def list_slates() -> list[Slate]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM slates ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [_row_to_slate(row) for row in rows]
    finally:
        await db.close()


async def get_slate(slate_id: str) -> Slate | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM slates WHERE id = ?", (slate_id,))
        row = await cursor.fetchone()
        return _row_to_slate(row) if row else None
    finally:
        await db.close()


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
    try:
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
    finally:
        await conn.close()


async def list_worktops_by_slate(slate_id: str) -> list[Worktop]:
    conn = await get_db()
    try:
        cursor = await conn.execute(
            "SELECT * FROM worktops WHERE slate_id = ? ORDER BY created_at DESC",
            (slate_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_worktop(row) for row in rows]
    finally:
        await conn.close()


async def delete_slate(slate_id: str) -> None:
    conn = await get_db()
    try:
        # Delete slate-level sessions (orchestrator etc)
        await conn.execute("DELETE FROM sessions WHERE slate_id = ?", (slate_id,))
        # Detach worktops — they continue to exist independently
        await conn.execute(
            "UPDATE worktops SET slate_id = NULL WHERE slate_id = ?",
            (slate_id,),
        )
        await conn.execute("DELETE FROM slates WHERE id = ?", (slate_id,))
        await conn.commit()
    finally:
        await conn.close()


# --- Daemon Ticks ---

MAX_RUNS = 100


async def create_daemon_run(
    tick_id: str, started_at: str, ended_at: str, results: list[dict]
) -> None:
    db = await get_db()
    try:
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
    finally:
        await db.close()


async def list_daemon_runs(limit: int = 20) -> list[dict]:
    db = await get_db()
    try:
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
    finally:
        await db.close()
