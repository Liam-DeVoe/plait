from __future__ import annotations

import asyncio
import json
import logging
import subprocess

logging.basicConfig(level=logging.INFO)
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from server import claude, config, daemon, db, git
from server.models import (
    CIStatus,
    Session,
    SessionRole,
    Slate,
    Worktop,
    WorktopStatus,
)
from server.pty import pty_manager
from server.sessions import (
    fork_cmd,
    resume_cmd,
    spawn_session,
    user_slate_cmd,
    user_worktop_cmd,
)

logger = logging.getLogger(__name__)


def _slate_repo_worktrees(slate_id: str) -> dict[str, str]:
    """Reconstruct the repo_id -> worktree path mapping for a slate."""
    return {
        repo_id: str(git.WORKTREE_ROOT / f"slate-{slate_id}" / repo_id)
        for repo_id in config.get_repos()
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    # Resolve every repo's upstream remote up front so config typos / missing
    # `git remote add` calls fail loudly at startup rather than silently
    # mis-tracking main forever.
    await git.validate_upstream_remotes()
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
    await db.close_db()


app = FastAPI(title="Plait", lifespan=lifespan)
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
    order = await db.get_repo_order()
    known = set(repos)
    seen: set[str] = set()
    ordered_ids: list[str] = []
    for repo_id in order:
        if repo_id in known and repo_id not in seen:
            ordered_ids.append(repo_id)
            seen.add(repo_id)
    for repo_id in repos:
        if repo_id not in seen:
            ordered_ids.append(repo_id)
    return [
        {
            "id": repos[repo_id].id,
            "path": str(repos[repo_id].path),
            "upstream": repos[repo_id].upstream,
            "kind": repos[repo_id].kind,
        }
        for repo_id in ordered_ids
    ]


class RepoOrderRequest(BaseModel):
    order: list[str]


@app.put("/repos/order")
async def set_repo_order(req: RepoOrderRequest):
    repos = config.get_repos()
    unknown = [r for r in req.order if r not in repos]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown repo(s): {', '.join(unknown)}",
        )
    if len(set(req.order)) != len(req.order):
        raise HTTPException(status_code=400, detail="Duplicate repo ids in order")
    await db.set_repo_order(req.order)
    return {"status": "ok"}


# --- Daemon endpoints ---


@app.get("/daemon/runs")
async def list_daemon_runs(limit: int = 20):
    return await db.list_daemon_runs(limit)


@app.post("/daemon/runs")
async def trigger_daemon_run():
    asyncio.create_task(daemon.run_once())
    return {"status": "started"}


# --- Worktop endpoints ---


class CreateWorktopRequest(BaseModel):
    pr_url: str | None = None
    repo: str | None = None


@app.post("/worktops")
async def create_worktop(req: CreateWorktopRequest):
    if req.pr_url:
        # Import from existing PR. Local-only repos are unreachable here
        # because get_pr_info_from_url maps URLs to repo_id via upstream,
        # and local repos have no upstream.
        try:
            pr_info = await git.get_pr_info_from_url(req.pr_url)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))

        worktop = Worktop(
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
        # Create local worktop with generic branch name
        worktop = Worktop(
            repo=req.repo,
            worktree_path="",
        )
        worktop.branch = f"worktop/{worktop.id[:8]}"
    else:
        raise HTTPException(status_code=400, detail="Either pr_url or repo is required")

    try:
        worktop.worktree_path = await git.create_worktree(
            worktop.repo, worktop.branch, worktop.id
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await claude.write_worktop_claude_md(
        worktop.worktree_path, worktop.id, worktop.repo
    )

    if worktop.pr_number:
        ci = await git.get_ci_status(worktop.repo, worktop.pr_number)
        worktop.ci_status = CIStatus(ci)
        worktop.pr_comment_count = (
            await git.get_pr_comment_count(worktop.repo, worktop.pr_number)
        ) or 0

    await db.create_worktop(worktop)
    return await _worktop_dict(worktop)


async def _tend_status(worktop_id: str) -> str:
    """Derive tend status from sessions: 'running' if a tend session is active."""
    sessions = await db.list_sessions(worktop_id)
    for s in sessions:
        if s.trigger == "tend" and s.ended_at is None:
            return "running"
    return "current"


async def _worktop_dict(worktop: Worktop) -> dict:
    """Serialize a worktop with derived tend_status."""
    result = asdict(worktop)
    result["tend_status"] = await _tend_status(worktop.id)
    return result


@app.get("/worktops")
async def list_worktops(status: str | None = None):
    worktop_status = WorktopStatus(status) if status else None
    worktops = await db.list_worktops(worktop_status)
    # Batch-fetch tend status for all worktops in one query (avoids N+1).
    running_ids = await db.list_running_tend_worktop_ids()
    result = []
    for w in worktops:
        d = asdict(w)
        d["tend_status"] = "running" if w.id in running_ids else "current"
        result.append(d)
    return result


@app.get("/worktops/{worktop_id}")
async def get_worktop(worktop_id: str):
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")
    sessions = await db.list_sessions(worktop_id)
    result = await _worktop_dict(worktop)
    result["sessions"] = [_session_dict(s) for s in sessions]
    return result


@app.post("/worktops/{worktop_id}/archive")
async def archive_worktop(worktop_id: str):
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")

    # Remove worktree
    try:
        await git.remove_worktree(worktop.repo, worktop.worktree_path)
    except Exception:
        logger.warning(f"Failed to remove worktree for worktop {worktop_id}")

    updated = await db.update_worktop(
        worktop_id,
        status=WorktopStatus.archived,
        archived_at=datetime.now(timezone.utc).isoformat(),
    )
    assert updated is not None
    await daemon.notify("worktop_updated", {"id": worktop_id, "status": "archived"})
    return await _worktop_dict(updated)


@app.post("/worktops/{worktop_id}/reopen")
async def reopen_worktop(worktop_id: str):
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")
    if worktop.status != WorktopStatus.archived:
        raise HTTPException(status_code=400, detail="Worktop is not archived")

    # Recreate worktree
    try:
        worktree_path = await git.create_worktree(
            worktop.repo, worktop.branch, worktop.id
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    updated = await db.update_worktop(
        worktop_id,
        status=WorktopStatus.open,
        worktree_path=worktree_path,
        archived_at=None,
        archive_reason=None,
    )
    assert updated is not None
    await daemon.notify("worktop_updated", {"id": worktop_id, "status": "open"})
    return await _worktop_dict(updated)


@app.post("/worktops/{worktop_id}/sync")
async def trigger_sync(worktop_id: str):
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")

    asyncio.create_task(daemon.tend_worktop(worktop))
    return {"status": "sync triggered"}


@app.delete("/worktops/{worktop_id}")
async def delete_worktop(worktop_id: str):
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")

    try:
        await git.remove_worktree(worktop.repo, worktop.worktree_path)
    except Exception:
        pass

    await db.delete_worktop(worktop_id)
    return {"status": "deleted"}


@app.post("/worktops/{worktop_id}/vscode")
async def open_in_vscode(worktop_id: str):
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")
    subprocess.Popen(["code", worktop.worktree_path])
    return {"status": "opened"}


@app.post("/worktops/{worktop_id}/sessions/{session_id}/vscode")
async def open_session_in_vscode(worktop_id: str, session_id: str):
    """Stop the web PTY session (if alive) and open VS Code + a terminal
    that resumes the Claude Code session in the worktop's worktree."""
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")

    sessions = await db.list_sessions(worktop_id)
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
    subprocess.Popen(["code", worktop.worktree_path])

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


@app.post("/hooks/worktops/{worktop_id}/branch-updated")
async def hook_branch_updated(worktop_id: str, req: BranchUpdatedHook):
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")
    await db.update_worktop(worktop_id, branch=req.branch)
    await daemon.notify("worktop_updated", {"id": worktop_id, "branch": req.branch})
    return {"status": "ok"}


class PRCreatedHook(BaseModel):
    pr_url: str
    pr_number: int


@app.post("/hooks/worktops/{worktop_id}/pr-created")
async def hook_pr_created(worktop_id: str, req: PRCreatedHook):
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")
    if config.is_local(worktop.repo):
        raise HTTPException(
            status_code=400,
            detail=f"Worktop {worktop_id} is in a local-only repo — no PR exists",
        )
    await db.update_worktop(worktop_id, pr_number=req.pr_number, pr_url=req.pr_url)
    await daemon.notify(
        "worktop_updated",
        {"id": worktop_id, "pr_number": req.pr_number, "pr_url": req.pr_url},
    )
    return {"status": "ok"}


@app.post("/hooks/worktops/{worktop_id}/ci-failure-expected")
async def hook_ci_failure_expected(worktop_id: str):
    """Called by a tend session when it determines CI failures are expected
    (e.g. the PR depends on another unmerged PR). Suppresses CI-failure
    as a tend trigger until the branch HEAD changes."""
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")
    if config.is_local(worktop.repo):
        raise HTTPException(
            status_code=400,
            detail=f"Worktop {worktop_id} is in a local-only repo — no CI exists",
        )
    rc, sha, _ = await git.run("git", "rev-parse", "HEAD", cwd=worktop.worktree_path)
    if rc != 0:
        raise HTTPException(status_code=500, detail="Could not read HEAD")
    sha = sha.strip()
    await db.update_worktop(worktop_id, ci_failure_expected_sha=sha)
    await daemon.notify(
        "worktop_updated", {"id": worktop_id, "ci_failure_expected_sha": sha}
    )
    return {"status": "ok"}


DONE_HOOK_GRACE_SECONDS = 10


async def _delayed_terminate(session_id: str, expected_pid: int) -> None:
    await asyncio.sleep(DONE_HOOK_GRACE_SECONDS)
    pty_session = pty_manager.get(session_id)
    if pty_session is None or pty_session.pid != expected_pid:
        return
    if pty_manager.is_alive(session_id):
        await pty_manager.terminate(session_id)


@app.post("/hooks/sessions/{session_id}/done")
async def hook_session_done(session_id: str):
    """Called by a session when it has finished its work.

    Schedules termination after a short grace period so any final assistant
    turn Claude is streaming after the curl call has time to complete before
    SIGHUP arrives. The watcher task finalizes the transcript and xterm state
    as usual; the session remains viewable and resumable.
    """
    pty_session = pty_manager.get(session_id)
    if pty_session is None or not pty_manager.is_alive(session_id):
        raise HTTPException(status_code=404, detail="Session not alive")
    asyncio.create_task(_delayed_terminate(session_id, pty_session.pid))
    return {"status": "ok"}


# --- Session endpoints ---


def _session_dict(s: Session) -> dict:
    """Serialize a session, adding runtime 'alive' field."""
    d = asdict(s)
    d["alive"] = pty_manager.is_alive(s.id)
    d.pop("xterm_state", None)
    return d


@app.get("/worktops/{worktop_id}/sessions")
async def list_sessions(worktop_id: str):
    sessions = await db.list_sessions(worktop_id)
    return [_session_dict(s) for s in sessions]


class CreateSessionRequest(BaseModel):
    prompt: str = ""
    prompt_file: str = ""


@app.post("/worktops/{worktop_id}/sessions")
async def create_session_endpoint(worktop_id: str, req: CreateSessionRequest):
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")

    prompt = req.prompt
    if req.prompt_file:
        try:
            prompt = Path(req.prompt_file).read_text()
        except FileNotFoundError:
            raise HTTPException(
                status_code=400, detail=f"Prompt file not found: {req.prompt_file}"
            )

    session = Session(
        worktop_id=worktop.id,
        role=SessionRole.user,
    )
    await db.create_session(session)

    cmd, cwd = user_worktop_cmd(session.id, worktop)
    spawn_session(session.id, cmd, cwd, initial_input=prompt)

    return _session_dict(session)


@app.post("/worktops/{worktop_id}/sessions/{session_id}/fork")
async def fork_session(worktop_id: str, session_id: str):
    """Fork a session into a new, independent one.

    The new session starts with the source's full conversation history as
    of right now (i.e. whatever the source has flushed to its on-disk
    transcript). The source is unaffected — it keeps running, and its
    transcript file is read-only from the fork's perspective. The fork
    becomes a fresh user session that the user can drive on its own.
    """
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")

    session_list = await db.list_sessions(worktop_id)
    source = next((s for s in session_list if s.id == session_id), None)
    if not source:
        raise HTTPException(status_code=404, detail="Session not found")

    # The fork is always a plain user session regardless of the source's role.
    # Once forked, the daemon is no longer responsible for it.
    fork = Session(
        worktop_id=worktop.id,
        role=SessionRole.user,
        parent_session_id=source.id,
    )
    await db.create_session(fork)

    cmd, cwd = fork_cmd(
        fork.id,
        source.id,
        worktop.worktree_path,
        claude.plait_system_prompt(),
    )
    spawn_session(fork.id, cmd, cwd)

    await daemon.notify("worktop_updated", {"id": worktop_id})
    return _session_dict(fork)


@app.post("/worktops/{worktop_id}/sessions/{session_id}/resume")
async def resume_session(worktop_id: str, session_id: str):
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")

    session_list = await db.list_sessions(worktop_id)
    session = next((s for s in session_list if s.id == session_id), None)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if pty_manager.is_alive(session_id):
        raise HTTPException(status_code=400, detail="Session is already alive")

    # Reset ended_at so daemon sees it as active
    await db.update_session(session_id, ended_at=None)

    cmd, cwd = resume_cmd(
        session.id,
        worktop.worktree_path,
        claude.plait_system_prompt(),
    )
    idle_timeout = (
        daemon.SESSION_IDLE_TIMEOUT if session.role == SessionRole.daemon else None
    )
    spawn_session(session.id, cmd, cwd, idle_timeout=idle_timeout)

    session.ended_at = None
    return _session_dict(session)


@app.delete("/worktops/{worktop_id}/sessions/{session_id}")
async def delete_session(worktop_id: str, session_id: str):
    worktop = await db.get_worktop(worktop_id)
    if not worktop:
        raise HTTPException(status_code=404, detail="Worktop not found")

    session_list = await db.list_sessions(worktop_id)
    session = next((s for s in session_list if s.id == session_id), None)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Kill PTY if still running
    if pty_manager.is_alive(session_id):
        await pty_manager.terminate(session_id)
    elif pty_manager.get(session_id):
        pty_manager.remove(session_id)

    await db.delete_session(session_id)
    await daemon.notify("worktop_updated", {"id": worktop_id})


@app.get("/worktops/{worktop_id}/sessions/{session_id}/xterm-state")
async def get_xterm_state(worktop_id: str, session_id: str):
    sessions = await db.list_sessions(worktop_id)
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
    await conn.execute(
        "UPDATE sessions SET ended_at = ? WHERE ended_at IS NULL",
        (datetime.now(timezone.utc).isoformat(),),
    )
    await conn.commit()


# --- Slate endpoints ---


def _slate_dict(slate: Slate, worktops: list[Worktop]) -> dict:
    """Serialize a slate with derived is_archived field."""
    d = asdict(slate)
    all_worktops_archived = len(worktops) > 0 and all(
        c.status == WorktopStatus.archived for c in worktops
    )
    d["is_archived"] = slate.archived or all_worktops_archived
    return d


@app.post("/slates")
async def create_slate():
    slate = Slate()
    await db.create_slate(slate)

    try:
        await git.create_slate_worktrees(slate.id)
    except RuntimeError:
        raise HTTPException(status_code=500, detail="Failed to create slate worktrees")

    claude.install_claude_files(str(git.WORKTREE_ROOT / f"slate-{slate.id}"))

    session = Session(
        slate_id=slate.id,
        role=SessionRole.user,
        trigger="slate",
    )
    await db.create_session(session)
    await db.update_slate(slate.id, session_id=session.id)

    result = asdict(slate)
    result["session_id"] = session.id
    return result


@app.get("/slates")
async def list_slates():
    slates = await db.list_slates()
    result = []
    for s in slates:
        worktops = await db.list_worktops_by_slate(s.id)
        d = _slate_dict(s, worktops)
        d["worktop_count"] = len(worktops)
        result.append(d)
    return result


@app.get("/slates/{slate_id}")
async def get_slate(slate_id: str):
    slate = await db.get_slate(slate_id)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")
    child_worktops = await db.list_worktops_by_slate(slate_id)
    result = _slate_dict(slate, child_worktops)
    result["worktops"] = [asdict(c) for c in child_worktops]
    # Include the orchestrator session if it exists
    if slate.session_id:
        session = await db.get_session(slate.session_id)
        if session:
            result["session"] = _session_dict(session)
    return result


@app.get("/slates/{slate_id}/sessions/{session_id}/xterm-state")
async def get_slate_xterm_state(slate_id: str, session_id: str):
    session = await db.get_session(session_id)
    if not session or session.slate_id != slate_id:
        raise HTTPException(status_code=404, detail="Session not found")
    xterm_state = pty_manager.get_raw_output(session_id) or session.xterm_state
    if not xterm_state:
        raise HTTPException(status_code=404, detail="No xterm state available")
    return Response(content=xterm_state, media_type="application/octet-stream")


@app.post("/slates/{slate_id}/sessions/{session_id}/resume")
async def resume_slate_session(slate_id: str, session_id: str):
    session = await db.get_session(session_id)
    if not session or session.slate_id != slate_id:
        raise HTTPException(status_code=404, detail="Session not found")

    if pty_manager.is_alive(session_id):
        raise HTTPException(status_code=400, detail="Session is already alive")

    exploration_dir = str(git.WORKTREE_ROOT / f"slate-{slate_id}")
    await db.update_session(session_id, ended_at=None)
    cmd, cwd = resume_cmd(
        session.id,
        exploration_dir,
        claude.slate_system_prompt(
            slate_id, exploration_dir, _slate_repo_worktrees(slate_id)
        ),
    )
    idle_timeout = (
        daemon.SESSION_IDLE_TIMEOUT if session.role == SessionRole.daemon else None
    )
    spawn_session(session.id, cmd, cwd, idle_timeout=idle_timeout)

    session.ended_at = None
    return _session_dict(session)


@app.post("/slates/{slate_id}/sessions/{session_id}/start")
async def start_slate_session(slate_id: str, session_id: str):
    session = await db.get_session(session_id)
    if not session or session.slate_id != slate_id:
        raise HTTPException(status_code=404, detail="Session not found")

    if pty_manager.is_alive(session_id):
        raise HTTPException(status_code=400, detail="Session is already alive")

    exploration_dir = str(git.WORKTREE_ROOT / f"slate-{slate_id}")
    repo_worktrees = _slate_repo_worktrees(slate_id)
    cmd, cwd = user_slate_cmd(session.id, slate_id, exploration_dir, repo_worktrees)
    spawn_session(session.id, cmd, cwd)

    return _session_dict(session)


@app.post("/slates/{slate_id}/archive")
async def archive_slate(slate_id: str):
    slate = await db.get_slate(slate_id)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")
    updated = await db.update_slate(slate_id, archived=True)
    assert updated is not None
    await daemon.notify("slate_updated", {"id": slate_id})
    return _slate_dict(updated, await db.list_worktops_by_slate(slate_id))


@app.post("/slates/{slate_id}/unarchive")
async def unarchive_slate(slate_id: str):
    slate = await db.get_slate(slate_id)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")
    updated = await db.update_slate(slate_id, archived=False)
    assert updated is not None
    await daemon.notify("slate_updated", {"id": slate_id})
    return _slate_dict(updated, await db.list_worktops_by_slate(slate_id))


@app.delete("/slates/{slate_id}")
async def delete_slate(slate_id: str):
    slate = await db.get_slate(slate_id)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")

    # Kill orchestrator session PTY if alive
    if slate.session_id and pty_manager.is_alive(slate.session_id):
        await pty_manager.terminate(slate.session_id)

    # Remove slate exploration worktrees
    try:
        await git.remove_slate_worktrees(slate_id)
    except Exception:
        pass

    await db.delete_slate(slate_id)
    return {"status": "deleted"}


@app.post("/slates/{slate_id}/vscode")
async def open_slate_in_vscode(slate_id: str):
    slate = await db.get_slate(slate_id)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")
    exploration_dir = str(git.WORKTREE_ROOT / f"slate-{slate_id}")
    subprocess.Popen(["code", exploration_dir])
    return {"status": "opened"}


# --- Slate hook endpoints ---


class CreateWorktopHook(BaseModel):
    repo: str


@app.post("/hooks/create-worktop")
async def hook_create_worktop(req: CreateWorktopHook):
    """Called by a worktop session to create a new standalone worktop in another repo."""
    try:
        config.get_repo(req.repo)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown repo: {req.repo!r}")

    worktop = Worktop(repo=req.repo, worktree_path="")
    worktop.branch = f"worktop/{worktop.id[:8]}"

    try:
        worktop.worktree_path = await git.create_worktree(
            req.repo, worktop.branch, worktop.id
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await claude.write_worktop_claude_md(
        worktop.worktree_path, worktop.id, worktop.repo
    )

    await db.create_worktop(worktop)
    await daemon.notify("worktop_updated", {"id": worktop.id, "status": "open"})
    return {
        "worktop_id": worktop.id,
        "url": f"http://localhost:5173/worktops/{worktop.id}",
    }


class SetSlateNameHook(BaseModel):
    name: str


@app.post("/hooks/slates/{slate_id}/set-name")
async def hook_set_slate_name(slate_id: str, req: SetSlateNameHook):
    slate = await db.get_slate(slate_id)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")
    await db.update_slate(slate_id, name=req.name)
    await daemon.notify("slate_updated", {"id": slate_id, "name": req.name})
    return {"status": "ok"}


class CreateSlateWorktopHook(BaseModel):
    repo: str


@app.post("/hooks/slates/{slate_id}/create-worktop")
async def hook_create_slate_worktop(slate_id: str, req: CreateSlateWorktopHook):
    slate = await db.get_slate(slate_id)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")

    try:
        config.get_repo(req.repo)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown repo: {req.repo!r}")

    # Check for duplicate worktop in this slate
    existing = await db.list_worktops_by_slate(slate_id)
    if any(c.repo == req.repo for c in existing):
        raise HTTPException(
            status_code=400,
            detail=f"A worktop for {req.repo!r} already exists in this slate",
        )

    branch = f"slate/{slate_id[:8]}/{req.repo}"
    worktop = Worktop(
        slate_id=slate_id,
        repo=req.repo,
        branch=branch,
        worktree_path="",
    )

    try:
        worktop.worktree_path = await git.create_worktree(req.repo, branch, worktop.id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await claude.write_worktop_claude_md(
        worktop.worktree_path, worktop.id, worktop.repo
    )

    await db.create_worktop(worktop)
    await daemon.notify("slate_updated", {"id": slate_id})
    await daemon.notify("worktop_updated", {"id": worktop.id, "status": "open"})
    return {
        "worktop_id": worktop.id,
        "worktree_path": worktop.worktree_path,
        "branch": branch,
    }
