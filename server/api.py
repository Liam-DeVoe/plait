from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from server import daemon, db, git
from server.models import Cell, CellStatus, Sortie

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
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
    repo: str
    branch: str
    pr_url: str | None = None
    sortie_id: str | None = None


class UpdateCellRequest(BaseModel):
    ci_status: str | None = None
    rebase_status: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None


@app.post("/cells")
async def create_cell(req: CreateCellRequest):
    pr_number = None
    if req.pr_url:
        m = re.search(r"/pull/(\d+)", req.pr_url)
        if m:
            pr_number = int(m.group(1))

    cell = Cell(
        repo=req.repo,
        branch=req.branch,
        worktree_path="",  # set below
        pr_number=pr_number,
        pr_url=req.pr_url,
        sortie_id=req.sortie_id,
    )

    try:
        cell.worktree_path = await git.create_worktree(req.repo, req.branch, cell.id)
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
    result["sessions"] = [asdict(s) for s in sessions]
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


@app.post("/cells/{cell_id}/rebase")
async def trigger_rebase(cell_id: str):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    # Run rebase in background
    asyncio.create_task(daemon.process_cell(cell))
    return {"status": "rebase triggered"}


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


@app.get("/cells/{cell_id}/sessions")
async def list_sessions(cell_id: str):
    sessions = await db.list_sessions(cell_id)
    return [asdict(s) for s in sessions]


# --- Sortie endpoints ---


class CreateSortieRequest(BaseModel):
    prompt: str
    repos: list[str]


@app.post("/sorties")
async def create_sortie(req: CreateSortieRequest):
    sortie = Sortie(prompt=req.prompt, repos=req.repos)
    await db.create_sortie(sortie)
    return asdict(sortie)


@app.get("/sorties")
async def list_sorties():
    sorties = await db.list_sorties()
    return [asdict(s) for s in sorties]


@app.get("/sorties/{sortie_id}")
async def get_sortie(sortie_id: str):
    sortie = await db.get_sortie(sortie_id)
    if not sortie:
        raise HTTPException(status_code=404, detail="Sortie not found")
    # Get child cells
    all_cells = await db.list_cells()
    child_cells = [c for c in all_cells if c.sortie_id == sortie_id]
    result = asdict(sortie)
    result["cells"] = [asdict(c) for c in child_cells]
    return result
