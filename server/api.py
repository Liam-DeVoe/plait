from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from server import daemon, db, git
from server.models import (
    Cell,
    CellStatus,
    Session,
    SessionRole,
    Sortie,
)
from server.pty import pty_manager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    # Sweep stale sessions from a previous crash
    await _cleanup_stale_sessions()
    # Start daemon
    event_queue: asyncio.Queue = asyncio.Queue()
    daemon.notify_callback = event_queue
    daemon_task = asyncio.create_task(daemon.daemon_loop())
    # Start WebSocket broadcaster
    broadcast_task = asyncio.create_task(broadcast_events(event_queue))
    yield
    daemon_task.cancel()
    broadcast_task.cancel()


app = FastAPI(title="Orrery", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connections for live updates
ws_connections: list[WebSocket] = []


async def broadcast_events(queue: asyncio.Queue) -> None:
    while True:
        event = await queue.get()
        dead = []
        for ws in ws_connections:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            ws_connections.remove(ws)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_connections.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_connections.remove(ws)


# --- Cell endpoints ---


class CreateCellRequest(BaseModel):
    pr_url: str


@app.post("/cells")
async def create_cell(req: CreateCellRequest):
    try:
        pr_info = await git.get_pr_info_from_url(req.pr_url)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    repo = pr_info["repo"]
    branch = pr_info["branch"]
    pr_number = pr_info["number"]
    pr_url = pr_info["url"]

    cell = Cell(
        repo=repo,
        branch=branch,
        worktree_path="",
        pr_number=pr_number,
        pr_url=pr_url,
    )

    try:
        cell.worktree_path = await git.create_worktree(repo, branch, cell.id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.create_cell(cell)
    return asdict(cell)


@app.get("/cells")
async def list_cells(status: str | None = None):
    cell_status = CellStatus(status) if status else None
    cells = await db.list_cells(cell_status)
    return [asdict(c) for c in cells]


@app.get("/cells/{cell_id}")
async def get_cell(cell_id: str):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")
    sessions = await db.list_sessions(cell_id)
    result = asdict(cell)
    result["sessions"] = [_session_dict(s) for s in sessions]
    return result


@app.post("/cells/{cell_id}/archive")
async def archive_cell(cell_id: str):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    # Remove worktree
    try:
        await git.remove_worktree(cell.repo, cell.worktree_path)
    except Exception:
        logger.warning(f"Failed to remove worktree for cell {cell_id}")

    updated = await db.update_cell(
        cell_id,
        status=CellStatus.archived,
        archived_at=datetime.now(timezone.utc).isoformat(),
    )
    assert updated is not None
    return asdict(updated)


@app.post("/cells/{cell_id}/reopen")
async def reopen_cell(cell_id: str):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")
    if cell.status != CellStatus.archived:
        raise HTTPException(status_code=400, detail="Cell is not archived")

    # Recreate worktree
    try:
        worktree_path = await git.create_worktree(cell.repo, cell.branch, cell.id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    updated = await db.update_cell(
        cell_id,
        status=CellStatus.active,
        worktree_path=worktree_path,
        archived_at=None,
    )
    assert updated is not None
    return asdict(updated)


@app.post("/cells/{cell_id}/sync")
async def trigger_sync(cell_id: str):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    # Run merge in background, bypassing retry limits
    asyncio.create_task(daemon.process_cell(cell, force=True))
    return {"status": "sync triggered"}


@app.delete("/cells/{cell_id}")
async def delete_cell(cell_id: str):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    try:
        await git.remove_worktree(cell.repo, cell.worktree_path)
    except Exception:
        pass

    await db.delete_cell(cell_id)
    return {"status": "deleted"}


@app.post("/cells/{cell_id}/vscode")
async def open_in_vscode(cell_id: str):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")
    subprocess.Popen(["code", cell.worktree_path])
    return {"status": "opened"}


# --- Session endpoints ---


def _session_dict(s: Session) -> dict:
    """Serialize a session, adding runtime 'alive' field."""
    d = asdict(s)
    d["alive"] = pty_manager.is_alive(s.id)
    return d


@app.get("/cells/{cell_id}/sessions")
async def list_sessions(cell_id: str):
    sessions = await db.list_sessions(cell_id)
    return [_session_dict(s) for s in sessions]


class CreateSessionRequest(BaseModel):
    prompt: str = ""


@app.post("/cells/{cell_id}/sessions")
async def create_session_endpoint(cell_id: str, req: CreateSessionRequest):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    session = Session(
        cell_id=cell.id,
        role=SessionRole.user,
    )
    await db.create_session(session)

    _spawn_pty_for_session(session.id, cell.worktree_path, prompt=req.prompt)

    return _session_dict(session)


def _spawn_pty_for_session(
    session_id: str, worktree_path: str, prompt: str = ""
) -> None:
    """Spawn a PTY running claude for a session and start watching it."""
    pty_manager.spawn(
        session_id,
        cwd=worktree_path,
        cmd=["claude", "--session-id", session_id],
    )
    if prompt.strip():

        async def _send_initial_prompt():
            await asyncio.sleep(1.0)
            pty_manager.write(session_id, (prompt + "\n").encode())

        asyncio.create_task(_send_initial_prompt())

    asyncio.create_task(_watch_pty(session_id))


async def _watch_pty(session_id: str) -> None:
    """Watch a PTY process: flush transcript periodically, finalize on exit."""
    flush_interval = 2.0
    while pty_manager.is_alive(session_id):
        # Periodically flush transcript to DB so it survives crashes
        transcript = pty_manager.get_transcript(session_id)
        if transcript:
            await db.update_session(session_id, transcript=transcript)
        await asyncio.sleep(flush_interval)

    # Final flush
    transcript = pty_manager.get_transcript(session_id)
    pty_manager.remove(session_id)

    await db.update_session(
        session_id,
        transcript=transcript,
        ended_at=datetime.now(timezone.utc).isoformat(),
    )

    # Look up cell_id for the notification
    conn = await db.get_db()
    try:
        cursor = await conn.execute(
            "SELECT cell_id FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if row:
            await daemon.notify("cell_updated", {"id": row["cell_id"]})
    finally:
        await conn.close()


@app.post("/cells/{cell_id}/sessions/{session_id}/resume")
async def resume_session(cell_id: str, session_id: str):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    session_list = await db.list_sessions(cell_id)
    session = next((s for s in session_list if s.id == session_id), None)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if pty_manager.is_alive(session_id):
        raise HTTPException(status_code=400, detail="Session is already alive")

    # Reset ended_at so daemon sees it as active
    await db.update_session(session_id, ended_at=None)

    _spawn_pty_for_session(session.id, cell.worktree_path)

    session.ended_at = None
    return _session_dict(session)


@app.post("/cells/{cell_id}/sessions/{session_id}/stop")
async def stop_session(cell_id: str, session_id: str):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    session_list = await db.list_sessions(cell_id)
    session = next((s for s in session_list if s.id == session_id), None)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not pty_manager.is_alive(session_id):
        raise HTTPException(status_code=400, detail="Session is not alive")

    transcript = pty_manager.get_transcript(session_id)
    await pty_manager.terminate(session_id)
    pty_manager.remove(session_id)

    updated = await db.update_session(
        session_id,
        transcript=transcript,
        ended_at=datetime.now(timezone.utc).isoformat(),
    )
    return _session_dict(updated) if updated else {}


@app.websocket("/ws/sessions/{session_id}")
async def session_terminal_ws(ws: WebSocket, session_id: str):
    pty_session = pty_manager.get(session_id)
    if pty_session is None:
        await ws.close(code=4004, reason="No active PTY for this session")
        return

    await ws.accept()

    # Send buffered output so far
    if pty_session.output_buffer:
        await ws.send_bytes(bytes(pty_session.output_buffer))

    # Queue for forwarding PTY output to this WebSocket
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def on_output(data: bytes) -> None:
        queue.put_nowait(data)

    pty_session.listeners.append(on_output)

    async def forward_output():
        try:
            while True:
                data = await queue.get()
                await ws.send_bytes(data)
        except Exception:
            pass

    forward_task = asyncio.create_task(forward_output())

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            if msg.get("type") == "input":
                pty_manager.write(session_id, msg["data"].encode())
            elif msg.get("type") == "resize":
                pty_manager.resize(session_id, msg["rows"], msg["cols"])
    except WebSocketDisconnect:
        pass
    finally:
        forward_task.cancel()
        if on_output in pty_session.listeners:
            pty_session.listeners.remove(on_output)


async def _cleanup_stale_sessions() -> None:
    """On startup, mark any sessions without ended_at as ended.
    Their PTY processes died when the previous server process exited."""
    conn = await db.get_db()
    try:
        await conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE ended_at IS NULL",
            (datetime.now(timezone.utc).isoformat(),),
        )
        await conn.commit()
    finally:
        await conn.close()


# --- Sortie endpoints ---


class CreateSortieRequest(BaseModel):
    prompt: str
    repos: list[str]


@app.post("/sorties")
async def create_sortie(req: CreateSortieRequest):
    sortie = Sortie(prompt=req.prompt, repos=req.repos)
    await db.create_sortie(sortie)

    # Spawn a cell per repo in the background
    for repo in req.repos:
        asyncio.create_task(daemon.spawn_sortie_cell(sortie, repo))

    return asdict(sortie)


def _derive_sortie_status(sortie: Sortie, cells: list[Cell]) -> str:
    """Compute sortie status from child cells."""
    if (
        len(cells) >= len(sortie.repos)
        and cells
        and all(c.status == CellStatus.archived for c in cells)
    ):
        return "completed"
    return "active"


@app.get("/sorties")
async def list_sorties():
    sorties = await db.list_sorties()
    result = []
    for s in sorties:
        d = asdict(s)
        cells = await db.list_cells_by_sortie(s.id)
        d["status"] = _derive_sortie_status(s, cells)
        d["cell_count"] = len(cells)
        result.append(d)
    return result


@app.get("/sorties/{sortie_id}")
async def get_sortie(sortie_id: str):
    sortie = await db.get_sortie(sortie_id)
    if not sortie:
        raise HTTPException(status_code=404, detail="Sortie not found")
    child_cells = await db.list_cells_by_sortie(sortie_id)
    result = asdict(sortie)
    result["cells"] = [asdict(c) for c in child_cells]
    result["status"] = _derive_sortie_status(sortie, child_cells)
    return result
