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
    SortieStatus,
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
    sync_status TEXT NOT NULL DEFAULT 'current',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    archived_at TEXT,
    FOREIGN KEY (sortie_id) REFERENCES sorties(id)
);

CREATE TABLE IF NOT EXISTS sorties (
    id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    repos TEXT NOT NULL,  -- JSON array
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    cell_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    trigger_name TEXT,
    succeeded INTEGER,
    transcript TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    FOREIGN KEY (cell_id) REFERENCES cells(id)
);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    return db


async def init_db() -> None:
    db = await get_db()
    await db.close()


# --- Cells ---


async def create_cell(cell: Cell) -> Cell:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO cells (id, sortie_id, repo, branch, worktree_path,
               pr_number, pr_url, ci_status, sync_status, status, created_at, archived_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cell.id,
                cell.sortie_id,
                cell.repo,
                cell.branch,
                cell.worktree_path,
                cell.pr_number,
                cell.pr_url,
                cell.ci_status.value,
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
            """INSERT INTO sessions (id, cell_id, role, trigger_name, succeeded,
               transcript, started_at, ended_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.id,
                session.cell_id,
                session.role.value,
                session.trigger,
                succeeded_val,
                session.transcript,
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
            "SELECT * FROM sessions WHERE cell_id = ? ORDER BY started_at DESC",
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


def _row_to_session(row: aiosqlite.Row) -> Session:
    succeeded_raw = row["succeeded"]
    return Session(
        id=row["id"],
        cell_id=row["cell_id"],
        role=SessionRole(row["role"]),
        trigger=row["trigger_name"],
        succeeded=None if succeeded_raw is None else bool(succeeded_raw),
        transcript=row["transcript"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
    )


# --- Sorties ---


async def create_sortie(sortie: Sortie) -> Sortie:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO sorties (id, prompt, repos, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                sortie.id,
                sortie.prompt,
                json.dumps(sortie.repos),
                sortie.status.value,
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
        prompt=row["prompt"],
        repos=json.loads(row["repos"]),
        status=SortieStatus(row["status"]),
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
