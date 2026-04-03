from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from server import config, daemon, db, git
from server.models import (
    Cell,
    CellStatus,
    CIStatus,
    Session,
    SessionRole,
    Sortie,
)
from server.pty import pty_manager
from server.sessions import (
    resume_cmd,
    spawn_session,
    user_cell_cmd,
    user_sortie_cmd,
)

logger = logging.getLogger(__name__)


def _sortie_repo_worktrees(sortie_id: str) -> dict[str, str]:
    """Reconstruct the repo_id -> worktree path mapping for a sortie."""
    return {
        repo_id: str(git.WORKTREE_ROOT / f"sortie-{sortie_id}" / repo_id)
        for repo_id in config.get_repos()
    }


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


# --- Repo endpoints ---


@app.get("/repos")
async def list_repos():
    repos = config.get_repos()
    return [
        {"id": r.id, "path": str(r.path), "upstream": r.upstream}
        for r in repos.values()
    ]


# --- Daemon endpoints ---


@app.get("/daemon/runs")
async def list_daemon_runs(limit: int = 20):
    return await db.list_daemon_runs(limit)


@app.post("/daemon/runs")
async def trigger_daemon_run():
    asyncio.create_task(daemon.run_once())
    return {"status": "started"}


# --- Cell endpoints ---


class CreateCellRequest(BaseModel):
    pr_url: str | None = None
    repo: str | None = None


@app.post("/cells")
async def create_cell(req: CreateCellRequest):
    if req.pr_url:
        # Import from existing PR
        try:
            pr_info = await git.get_pr_info_from_url(req.pr_url)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))

        cell = Cell(
            repo=pr_info["repo_id"],
            branch=pr_info["branch"],
            worktree_path="",
            pr_number=pr_info["number"],
            pr_url=pr_info["url"],
        )
    elif req.repo:
        # Validate repo ID exists in config
        try:
            config.get_repo(req.repo)
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Unknown repo: {req.repo!r}")
        # Create local cell with generic branch name
        cell = Cell(
            repo=req.repo,
            worktree_path="",
        )
        cell.branch = f"cell/{cell.id[:8]}"
    else:
        raise HTTPException(status_code=400, detail="Either pr_url or repo is required")

    try:
        cell.worktree_path = await git.create_worktree(cell.repo, cell.branch, cell.id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if cell.pr_number:
        ci = await git.get_ci_status(cell.repo, cell.pr_number)
        cell.ci_status = CIStatus(ci)
        cell.pr_comment_count = (
            await git.get_pr_comment_count(cell.repo, cell.pr_number)
        ) or 0

    await db.create_cell(cell)
    return await _cell_dict(cell)


async def _tend_status(cell_id: str) -> str:
    """Derive tend status from sessions: 'running' if a tend session is active."""
    sessions = await db.list_sessions(cell_id)
    for s in sessions:
        if s.trigger == "tend" and s.ended_at is None:
            return "running"
    return "current"


async def _cell_dict(cell: Cell) -> dict:
    """Serialize a cell with derived tend_status."""
    result = asdict(cell)
    result["tend_status"] = await _tend_status(cell.id)
    return result


@app.get("/cells")
async def list_cells(status: str | None = None):
    cell_status = CellStatus(status) if status else None
    cells = await db.list_cells(cell_status)
    return [await _cell_dict(c) for c in cells]


@app.get("/cells/{cell_id}")
async def get_cell(cell_id: str):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")
    sessions = await db.list_sessions(cell_id)
    result = await _cell_dict(cell)
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
    await daemon.notify("cell_updated", {"id": cell_id, "status": "archived"})
    return await _cell_dict(updated)


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
        status=CellStatus.open,
        worktree_path=worktree_path,
        archived_at=None,
        archive_reason=None,
    )
    assert updated is not None
    await daemon.notify("cell_updated", {"id": cell_id, "status": "open"})
    return await _cell_dict(updated)


@app.post("/cells/{cell_id}/sync")
async def trigger_sync(cell_id: str):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    asyncio.create_task(daemon.tend_cell(cell))
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


@app.post("/cells/{cell_id}/sessions/{session_id}/vscode")
async def open_session_in_vscode(cell_id: str, session_id: str):
    """Stop the web PTY session (if alive) and open VS Code + a terminal
    that resumes the Claude Code session in the cell's worktree."""
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    sessions = await db.list_sessions(cell_id)
    session = next((s for s in sessions if s.id == session_id), None)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Stop the PTY if it's still alive
    if pty_manager.is_alive(session_id):
        transcript = pty_manager.get_transcript(session_id)
        xterm_state = pty_manager.get_raw_output(session_id)
        await pty_manager.terminate(session_id)

        update_kwargs: dict[str, object] = {}
        if not session.ended_at:
            update_kwargs["ended_at"] = datetime.now(timezone.utc).isoformat()
        if transcript:
            update_kwargs["transcript"] = transcript
        if xterm_state:
            update_kwargs["xterm_state"] = xterm_state
        if update_kwargs:
            await db.update_session(session_id, **update_kwargs)

    # Open VS Code at the worktree
    subprocess.Popen(["code", cell.worktree_path])

    # Open a new integrated terminal in VS Code and type the resume command.
    # Uses AppleScript to wait for VS Code, open a terminal (Ctrl+Shift+`),
    # and type the command.
    resume_cmd = f"claude --resume {session_id}"
    applescript = f"""delay 3
tell application "Visual Studio Code" to activate
delay 0.3
tell application "System Events"
    tell process "Code"
        keystroke ";" using {{command down}}
        delay 0.3
        keystroke "{resume_cmd}"
        keystroke return
    end tell
end tell"""
    subprocess.Popen(["osascript", "-e", applescript])

    return {"status": "opened"}


# --- Hook endpoints (called by Claude inside sessions) ---


class BranchUpdatedHook(BaseModel):
    branch: str


@app.post("/hooks/cells/{cell_id}/branch-updated")
async def hook_branch_updated(cell_id: str, req: BranchUpdatedHook):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")
    await db.update_cell(cell_id, branch=req.branch)
    await daemon.notify("cell_updated", {"id": cell_id, "branch": req.branch})
    return {"status": "ok"}


class PRCreatedHook(BaseModel):
    pr_url: str
    pr_number: int


@app.post("/hooks/cells/{cell_id}/pr-created")
async def hook_pr_created(cell_id: str, req: PRCreatedHook):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")
    await db.update_cell(cell_id, pr_number=req.pr_number, pr_url=req.pr_url)
    await daemon.notify(
        "cell_updated",
        {"id": cell_id, "pr_number": req.pr_number, "pr_url": req.pr_url},
    )
    return {"status": "ok"}


@app.post("/hooks/cells/{cell_id}/ci-failure-expected")
async def hook_ci_failure_expected(cell_id: str):
    """Called by a tend session when it determines CI failures are expected
    (e.g. the PR depends on another unmerged PR). Suppresses CI-failure
    as a tend trigger until something else changes."""
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")
    await db.update_cell(cell_id, ci_failure_expected=True)
    await daemon.notify("cell_updated", {"id": cell_id, "ci_failure_expected": True})
    return {"status": "ok"}


@app.post("/hooks/sessions/{session_id}/done")
async def hook_session_done(session_id: str):
    """Called by a session when it has finished its work.

    Terminates the PTY process. The watcher task will finalize the transcript
    and xterm state as usual. The session remains viewable and resumable.
    """
    if not pty_manager.is_alive(session_id):
        raise HTTPException(status_code=404, detail="Session not alive")
    await pty_manager.terminate(session_id)
    return {"status": "ok"}


# --- Session endpoints ---


def _session_dict(s: Session) -> dict:
    """Serialize a session, adding runtime 'alive' field."""
    d = asdict(s)
    d["alive"] = pty_manager.is_alive(s.id)
    d.pop("xterm_state", None)
    return d


@app.get("/cells/{cell_id}/sessions")
async def list_sessions(cell_id: str):
    sessions = await db.list_sessions(cell_id)
    return [_session_dict(s) for s in sessions]


class CreateSessionRequest(BaseModel):
    prompt: str = ""
    prompt_file: str = ""


@app.post("/cells/{cell_id}/sessions")
async def create_session_endpoint(cell_id: str, req: CreateSessionRequest):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    prompt = req.prompt
    if req.prompt_file:
        try:
            prompt = Path(req.prompt_file).read_text()
        except FileNotFoundError:
            raise HTTPException(
                status_code=400, detail=f"Prompt file not found: {req.prompt_file}"
            )

    session = Session(
        cell_id=cell.id,
        role=SessionRole.user,
    )
    await db.create_session(session)

    cmd, cwd = user_cell_cmd(session.id, cell)
    spawn_session(session.id, cmd, cwd, initial_input=prompt)

    return _session_dict(session)


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

    cmd, cwd = resume_cmd(session.id, cell.worktree_path)
    idle_timeout = (
        daemon.SESSION_IDLE_TIMEOUT if session.role == SessionRole.daemon else None
    )
    spawn_session(session.id, cmd, cwd, idle_timeout=idle_timeout)

    session.ended_at = None
    return _session_dict(session)


@app.delete("/cells/{cell_id}/sessions/{session_id}")
async def delete_session(cell_id: str, session_id: str):
    cell = await db.get_cell(cell_id)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    session_list = await db.list_sessions(cell_id)
    session = next((s for s in session_list if s.id == session_id), None)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Kill PTY if still running
    if pty_manager.is_alive(session_id):
        await pty_manager.terminate(session_id)
    elif pty_manager.get(session_id):
        pty_manager.remove(session_id)

    await db.delete_session(session_id)
    await daemon.notify("cell_updated", {"id": cell_id})


@app.get("/cells/{cell_id}/sessions/{session_id}/xterm-state")
async def get_xterm_state(cell_id: str, session_id: str):
    sessions = await db.list_sessions(cell_id)
    session = next((s for s in sessions if s.id == session_id), None)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    # Prefer live PTY buffer for running sessions, fall back to DB
    xterm_state = pty_manager.get_raw_output(session_id) or session.xterm_state
    if not xterm_state:
        raise HTTPException(status_code=404, detail="No xterm state available")
    return Response(content=xterm_state, media_type="application/octet-stream")


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


@app.post("/sorties")
async def create_sortie():
    sortie = Sortie()
    await db.create_sortie(sortie)

    try:
        await git.create_sortie_worktrees(sortie.id)
    except RuntimeError:
        raise HTTPException(status_code=500, detail="Failed to create sortie worktrees")

    session = Session(
        sortie_id=sortie.id,
        role=SessionRole.user,
        trigger="sortie",
    )
    await db.create_session(session)
    await db.update_sortie(sortie.id, session_id=session.id)

    result = asdict(sortie)
    result["session_id"] = session.id
    return result


@app.get("/sorties")
async def list_sorties():
    sorties = await db.list_sorties()
    result = []
    for s in sorties:
        d = asdict(s)
        cells = await db.list_cells_by_sortie(s.id)
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
    # Include the orchestrator session if it exists
    if sortie.session_id:
        session = await db.get_session(sortie.session_id)
        if session:
            result["session"] = _session_dict(session)
    return result


@app.get("/sorties/{sortie_id}/sessions/{session_id}/xterm-state")
async def get_sortie_xterm_state(sortie_id: str, session_id: str):
    session = await db.get_session(session_id)
    if not session or session.sortie_id != sortie_id:
        raise HTTPException(status_code=404, detail="Session not found")
    xterm_state = pty_manager.get_raw_output(session_id) or session.xterm_state
    if not xterm_state:
        raise HTTPException(status_code=404, detail="No xterm state available")
    return Response(content=xterm_state, media_type="application/octet-stream")


@app.post("/sorties/{sortie_id}/sessions/{session_id}/resume")
async def resume_sortie_session(sortie_id: str, session_id: str):
    session = await db.get_session(session_id)
    if not session or session.sortie_id != sortie_id:
        raise HTTPException(status_code=404, detail="Session not found")

    if pty_manager.is_alive(session_id):
        raise HTTPException(status_code=400, detail="Session is already alive")

    exploration_dir = str(git.WORKTREE_ROOT / f"sortie-{sortie_id}")
    await db.update_session(session_id, ended_at=None)
    cmd, cwd = resume_cmd(session.id, exploration_dir)
    idle_timeout = (
        daemon.SESSION_IDLE_TIMEOUT if session.role == SessionRole.daemon else None
    )
    spawn_session(session.id, cmd, cwd, idle_timeout=idle_timeout)

    session.ended_at = None
    return _session_dict(session)


@app.post("/sorties/{sortie_id}/sessions/{session_id}/start")
async def start_sortie_session(sortie_id: str, session_id: str):
    session = await db.get_session(session_id)
    if not session or session.sortie_id != sortie_id:
        raise HTTPException(status_code=404, detail="Session not found")

    if pty_manager.is_alive(session_id):
        raise HTTPException(status_code=400, detail="Session is already alive")

    exploration_dir = str(git.WORKTREE_ROOT / f"sortie-{sortie_id}")
    repo_worktrees = _sortie_repo_worktrees(sortie_id)
    cmd, cwd = user_sortie_cmd(session.id, sortie_id, exploration_dir, repo_worktrees)
    spawn_session(session.id, cmd, cwd)

    return _session_dict(session)


@app.delete("/sorties/{sortie_id}")
async def delete_sortie(sortie_id: str):
    sortie = await db.get_sortie(sortie_id)
    if not sortie:
        raise HTTPException(status_code=404, detail="Sortie not found")

    # Kill orchestrator session PTY if alive
    if sortie.session_id and pty_manager.is_alive(sortie.session_id):
        await pty_manager.terminate(sortie.session_id)

    # Remove child cell worktrees
    cells = await db.list_cells_by_sortie(sortie_id)
    for cell in cells:
        try:
            await git.remove_worktree(cell.repo, cell.worktree_path)
        except Exception:
            pass

    # Remove sortie exploration worktrees
    try:
        await git.remove_sortie_worktrees(sortie_id)
    except Exception:
        pass

    await db.delete_sortie(sortie_id)
    return {"status": "deleted"}


@app.post("/sorties/{sortie_id}/vscode")
async def open_sortie_in_vscode(sortie_id: str):
    sortie = await db.get_sortie(sortie_id)
    if not sortie:
        raise HTTPException(status_code=404, detail="Sortie not found")
    exploration_dir = str(git.WORKTREE_ROOT / f"sortie-{sortie_id}")
    subprocess.Popen(["code", exploration_dir])
    return {"status": "opened"}


# --- Sortie hook endpoints ---


class CreateCellHook(BaseModel):
    repo: str


@app.post("/hooks/create-cell")
async def hook_create_cell(req: CreateCellHook):
    """Called by a cell session to create a new standalone cell in another repo."""
    try:
        config.get_repo(req.repo)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown repo: {req.repo!r}")

    cell = Cell(repo=req.repo, worktree_path="")
    cell.branch = f"cell/{cell.id[:8]}"

    try:
        cell.worktree_path = await git.create_worktree(req.repo, cell.branch, cell.id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.create_cell(cell)
    await daemon.notify("cell_updated", {"id": cell.id, "status": "open"})
    return {"cell_id": cell.id, "url": f"http://localhost:5173/cells/{cell.id}"}


class CreateSortieCellHook(BaseModel):
    repo: str


@app.post("/hooks/sorties/{sortie_id}/create-cell")
async def hook_create_sortie_cell(sortie_id: str, req: CreateSortieCellHook):
    sortie = await db.get_sortie(sortie_id)
    if not sortie:
        raise HTTPException(status_code=404, detail="Sortie not found")

    try:
        config.get_repo(req.repo)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown repo: {req.repo!r}")

    # Check for duplicate cell in this sortie
    existing = await db.list_cells_by_sortie(sortie_id)
    if any(c.repo == req.repo for c in existing):
        raise HTTPException(
            status_code=400,
            detail=f"A cell for {req.repo!r} already exists in this sortie",
        )

    branch = f"sortie/{sortie_id[:8]}/{req.repo}"
    cell = Cell(
        sortie_id=sortie_id,
        repo=req.repo,
        branch=branch,
        worktree_path="",
    )

    try:
        cell.worktree_path = await git.create_worktree(req.repo, branch, cell.id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.create_cell(cell)
    await daemon.notify("sortie_updated", {"id": sortie_id})
    await daemon.notify("cell_updated", {"id": cell.id, "status": "open"})
    return {"cell_id": cell.id, "worktree_path": cell.worktree_path, "branch": branch}
