from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

import aiosqlite

from server.models import (
    Cell,
    CellStatus,
    CIStatus,
    Session,
    SessionRole,
    Sortie,
    SyncStatus,
)

DB_PATH: str | Path = Path(__file__).parent.parent / "orrery.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cells (
    id TEXT PRIMARY KEY,
    sortie_id TEXT,
    repo TEXT NOT NULL,
    branch TEXT NOT NULL,
    worktree_path TEXT NOT NULL,
    pr_number INTEGER,
    pr_url TEXT,
    ci_status TEXT NOT NULL DEFAULT 'unknown',
    ci_failure_expected INTEGER NOT NULL DEFAULT 0,
    pr_comment_count INTEGER NOT NULL DEFAULT 0,
    pr_reaction_count INTEGER NOT NULL DEFAULT 0,
    sync_status TEXT NOT NULL DEFAULT 'current',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    archived_at TEXT,
    FOREIGN KEY (sortie_id) REFERENCES sorties(id)
);

CREATE TABLE IF NOT EXISTS sorties (
    id TEXT PRIMARY KEY,
    session_id TEXT,
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
    cell_id TEXT,
    sortie_id TEXT,
    role TEXT NOT NULL DEFAULT 'user',
    trigger_name TEXT,
    succeeded INTEGER,
    transcript TEXT NOT NULL DEFAULT '',
    xterm_state BLOB,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    FOREIGN KEY (cell_id) REFERENCES cells(id),
    FOREIGN KEY (sortie_id) REFERENCES sorties(id)
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
        "ALTER TABLE sessions ADD COLUMN sortie_id TEXT",
        "ALTER TABLE sorties ADD COLUMN session_id TEXT",
        "ALTER TABLE cells ADD COLUMN pr_reaction_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE cells ADD COLUMN ci_failure_expected INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE sorties DROP COLUMN status",
    ]:
        try:
            await db.execute(migration)
            await db.commit()
        except Exception:
            pass
    await db.close()


# --- Cells ---


async def create_cell(cell: Cell) -> Cell:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO cells (id, sortie_id, repo, branch, worktree_path,
               pr_number, pr_url, ci_status, ci_failure_expected,
               pr_comment_count, pr_reaction_count,
               sync_status, status, created_at, archived_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cell.id,
                cell.sortie_id,
                cell.repo,
                cell.branch,
                cell.worktree_path,
                cell.pr_number,
                cell.pr_url,
                cell.ci_status.value,
                int(cell.ci_failure_expected),
                cell.pr_comment_count,
                cell.pr_reaction_count,
                cell.sync_status.value,
                cell.status.value,
                cell.created_at,
                cell.archived_at,
            ),
        )
        await db.commit()
        return cell
    finally:
        await db.close()


async def list_cells(status: CellStatus | None = None) -> list[Cell]:
    db = await get_db()
    try:
        if status:
            cursor = await db.execute(
                "SELECT * FROM cells WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            )
        else:
            cursor = await db.execute("SELECT * FROM cells ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [_row_to_cell(row) for row in rows]
    finally:
        await db.close()


async def get_cell(cell_id: str) -> Cell | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM cells WHERE id = ?", (cell_id,))
        row = await cursor.fetchone()
        return _row_to_cell(row) if row else None
    finally:
        await db.close()


async def update_cell(cell_id: str, **kwargs: object) -> Cell | None:
    db = await get_db()
    try:
        sets = []
        values = []
        for key, value in kwargs.items():
            if isinstance(value, Enum):
                value = value.value
            sets.append(f"{key} = ?")
            values.append(value)
        values.append(cell_id)
        await db.execute(
            f"UPDATE cells SET {', '.join(sets)} WHERE id = ?",
            values,
        )
        await db.commit()
        return await get_cell(cell_id)
    finally:
        await db.close()


async def delete_cell(cell_id: str) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM cells WHERE id = ?", (cell_id,))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


def _row_to_cell(row: aiosqlite.Row) -> Cell:
    return Cell(
        id=row["id"],
        sortie_id=row["sortie_id"],
        repo=row["repo"],
        branch=row["branch"],
        worktree_path=row["worktree_path"],
        pr_number=row["pr_number"],
        pr_url=row["pr_url"],
        ci_status=CIStatus(row["ci_status"]),
        ci_failure_expected=bool(row["ci_failure_expected"]),
        pr_comment_count=row["pr_comment_count"],
        pr_reaction_count=row["pr_reaction_count"],
        sync_status=SyncStatus(row["sync_status"]),
        status=CellStatus(row["status"]),
        created_at=row["created_at"],
        archived_at=row["archived_at"],
    )


# --- Sessions ---


async def create_session(session: Session) -> Session:
    db = await get_db()
    try:
        succeeded_val = None if session.succeeded is None else int(session.succeeded)
        await db.execute(
            """INSERT INTO sessions (id, cell_id, sortie_id, role, trigger_name,
               succeeded, transcript, xterm_state, started_at, ended_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.id,
                session.cell_id,
                session.sortie_id,
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


async def list_sessions(cell_id: str) -> list[Session]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE cell_id = ? ORDER BY started_at ASC",
            (cell_id,),
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
        cell_id=row["cell_id"],
        sortie_id=row["sortie_id"],
        role=SessionRole(row["role"]),
        trigger=row["trigger_name"],
        succeeded=None if succeeded_raw is None else bool(succeeded_raw),
        transcript=row["transcript"],
        xterm_state=row["xterm_state"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
    )


# --- Sorties ---


async def create_sortie(sortie: Sortie) -> Sortie:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO sorties (id, session_id, created_at)
               VALUES (?, ?, ?)""",
            (
                sortie.id,
                sortie.session_id,
                sortie.created_at,
            ),
        )
        await db.commit()
        return sortie
    finally:
        await db.close()


async def list_sorties() -> list[Sortie]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM sorties ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [_row_to_sortie(row) for row in rows]
    finally:
        await db.close()


async def get_sortie(sortie_id: str) -> Sortie | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM sorties WHERE id = ?", (sortie_id,))
        row = await cursor.fetchone()
        return _row_to_sortie(row) if row else None
    finally:
        await db.close()


def _row_to_sortie(row: aiosqlite.Row) -> Sortie:
    return Sortie(
        id=row["id"],
        session_id=row["session_id"],
        created_at=row["created_at"],
    )


async def update_sortie(sortie_id: str, **kwargs: object) -> Sortie | None:
    conn = await get_db()
    try:
        sets = []
        values = []
        for key, value in kwargs.items():
            if isinstance(value, Enum):
                value = value.value
            sets.append(f"{key} = ?")
            values.append(value)
        values.append(sortie_id)
        await conn.execute(
            f"UPDATE sorties SET {', '.join(sets)} WHERE id = ?",
            values,
        )
        await conn.commit()
        return await get_sortie(sortie_id)
    finally:
        await conn.close()


async def list_cells_by_sortie(sortie_id: str) -> list[Cell]:
    conn = await get_db()
    try:
        cursor = await conn.execute(
            "SELECT * FROM cells WHERE sortie_id = ? ORDER BY created_at DESC",
            (sortie_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_cell(row) for row in rows]
    finally:
        await conn.close()


async def delete_sortie(sortie_id: str) -> None:
    conn = await get_db()
    try:
        await conn.execute("DELETE FROM sessions WHERE sortie_id = ?", (sortie_id,))
        await conn.execute(
            "DELETE FROM sessions WHERE cell_id IN (SELECT id FROM cells WHERE sortie_id = ?)",
            (sortie_id,),
        )
        await conn.execute("DELETE FROM cells WHERE sortie_id = ?", (sortie_id,))
        await conn.execute("DELETE FROM sorties WHERE id = ?", (sortie_id,))
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
