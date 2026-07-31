from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import tempfile

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
    Repo,
    Session,
    SessionRole,
    Slate,
    View,
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


def _slate_repo_worktrees(slate_id: str, repo_ids: list[str]) -> dict[str, str]:
    """Reconstruct the repo_id -> worktree path mapping for a slate."""
    return {
        repo_id: str(git.WORKTREE_ROOT / f"slate-{slate_id}" / repo_id)
        for repo_id in repo_ids
    }


async def _slate_scope_repo_ids(slate: Slate) -> list[str]:
    """Return the repo_ids a slate is scoped to.

    The slate stores its own snapshotted scope; pre-views slates (or any
    slate created with an empty scope) fall back to every configured repo
    so the legacy "spans every repo" behavior still works for old data.
    """
    if slate.repo_ids:
        return slate.repo_ids
    return list(config.get_repos().keys())


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    # Prime the synchronous config cache from the DB. Everything downstream
    # (git, claude, daemon) reads repo metadata through `config.get_repo(...)`
    # without awaiting, so the cache MUST be populated before those modules
    # run.
    await config.refresh()
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


def _repo_dict(repo: Repo) -> dict:
    return {
        "id": repo.id,
        "path": str(repo.path),
        "upstream": repo.upstream,
        "kind": repo.kind,
        "position": repo.position,
        "copy_globs": repo.copy_globs,
        "metr": repo.metr,
    }


async def _validate_copy_globs(repo_id: str, path: Path, globs: list[str]) -> None:
    """400 if any copy glob doesn't resolve to gitignored files right now.

    Validating at save time means a typo'd or stale glob fails when you
    write the config, not at the next worktop creation.
    """
    try:
        await git.resolve_copy_globs(repo_id, path, globs)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/repos")
async def list_repos():
    repos = config.get_repos()
    return [_repo_dict(repos[rid]) for rid in repos]


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
    await db.set_repo_positions(req.order)
    await config.refresh()
    return {"status": "ok"}


class CreateRepoRequest(BaseModel):
    id: str
    path: str
    kind: str = "remote"
    upstream: str | None = None
    copy_globs: list[str] = []
    metr: bool = False


@app.post("/repos")
async def create_repo(req: CreateRepoRequest):
    rid = req.id.strip()
    if not rid:
        raise HTTPException(status_code=400, detail="id is required")
    if req.kind not in ("remote", "local"):
        raise HTTPException(status_code=400, detail="kind must be 'remote' or 'local'")
    if req.kind == "remote" and not req.upstream:
        raise HTTPException(status_code=400, detail="kind='remote' requires upstream")
    if req.kind == "local" and req.upstream:
        raise HTTPException(
            status_code=400, detail="kind='local' must not have upstream"
        )
    existing = await db.get_repo(rid)
    if existing is not None:
        raise HTTPException(status_code=400, detail=f"Repo {rid!r} already exists")
    await _validate_copy_globs(rid, Path(req.path), req.copy_globs)

    existing_repos = await db.list_repos()
    position = len(existing_repos)
    repo = Repo(
        id=rid,
        path=Path(req.path),
        kind=req.kind,
        upstream=req.upstream,
        position=position,
        copy_globs=req.copy_globs,
        metr=req.metr,
    )
    await db.create_repo(repo)
    await config.refresh()
    # Validate the upstream remote eagerly for remote repos. We don't block
    # creation on failure (the user may be wiring this up in stages); just
    # surface the issue in the response.
    warning: str | None = None
    if repo.kind == "remote":
        try:
            await git.upstream_remote(rid)
        except Exception as e:  # pragma: no cover - defensive
            warning = str(e)
    response = _repo_dict(repo)
    if warning is not None:
        response["warning"] = warning
    return response


class UpdateRepoRequest(BaseModel):
    path: str | None = None
    upstream: str | None = None
    kind: str | None = None
    copy_globs: list[str] | None = None
    metr: bool | None = None


@app.put("/repos/{repo_id}")
async def update_repo(repo_id: str, req: UpdateRepoRequest):
    repo = await db.get_repo(repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repo not found")

    updates: dict[str, object] = {}
    new_kind = req.kind if req.kind is not None else repo.kind
    new_upstream = req.upstream if req.upstream is not None else repo.upstream
    if new_kind not in ("remote", "local"):
        raise HTTPException(status_code=400, detail="kind must be 'remote' or 'local'")
    if new_kind == "remote" and not new_upstream:
        raise HTTPException(status_code=400, detail="kind='remote' requires upstream")
    if new_kind == "local" and new_upstream:
        raise HTTPException(
            status_code=400, detail="kind='local' must not have upstream"
        )

    new_path = Path(req.path) if req.path is not None else repo.path
    new_globs = req.copy_globs if req.copy_globs is not None else repo.copy_globs
    if req.copy_globs is not None or req.path is not None:
        # Re-validate whenever the globs change, and also when the path
        # changes (the same globs may not resolve in the new clone).
        await _validate_copy_globs(repo_id, new_path, new_globs)

    if req.path is not None:
        updates["path"] = req.path
    if req.kind is not None:
        updates["kind"] = req.kind
    if req.upstream is not None or (req.kind == "local"):
        # If switching to local, clear any stale upstream value.
        updates["upstream"] = new_upstream
    if req.copy_globs is not None:
        updates["copy_globs"] = req.copy_globs
    if req.metr is not None:
        updates["metr"] = int(req.metr)

    if not updates:
        return _repo_dict(repo)

    updated = await db.update_repo(repo_id, **updates)
    assert updated is not None
    # Invalidate per-repo caches that key off path/upstream.
    git._main_branch_cache.pop(repo_id, None)
    git._upstream_remote_cache.pop(repo_id, None)
    await config.refresh()
    return _repo_dict(updated)


@app.delete("/repos/{repo_id}")
async def delete_repo(repo_id: str):
    """Delete a repo and cascade to every dependent on-disk + DB record.

    SQL FK cascade isn't enough here: deleting a repo means killing live
    PTYs, removing git worktrees from disk, and rewriting JSON arrays in
    `views.repo_ids`. We do this in application code so each side effect
    runs in the right order.
    """
    repo = await db.get_repo(repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repo not found")

    # 1. For every worktop in this repo: kill running sessions, remove
    #    the on-disk worktree, delete the row.
    worktops = await db.list_worktops()
    repo_worktops = [w for w in worktops if w.repo == repo_id]
    for worktop in repo_worktops:
        sessions = await db.list_sessions(worktop.id)
        for session in sessions:
            if pty_manager.is_alive(session.id):
                await pty_manager.terminate(session.id)
        try:
            await git.remove_worktree(worktop.repo, worktop.worktree_path)
        except Exception:
            logger.warning(
                f"Failed to remove worktree for worktop {worktop.id}; "
                "deleting DB row anyway."
            )
        await db.delete_worktop(worktop.id)

    # 2. Strip the repo from every view's repo_ids array.
    await db.remove_repo_from_views(repo_id)

    # 3. Drop the repo itself + invalidate caches.
    await db.delete_repo(repo_id)
    git._main_branch_cache.pop(repo_id, None)
    git._upstream_remote_cache.pop(repo_id, None)
    await config.refresh()

    # Notify clients to revalidate.
    await daemon.notify("repo_deleted", {"id": repo_id})
    return {
        "status": "deleted",
        "worktops_deleted": len(repo_worktops),
    }


# --- View endpoints ---


def _view_dict(view: View) -> dict:
    return {
        "id": view.id,
        "name": view.name,
        "repo_ids": view.repo_ids,
        "position": view.position,
        "created_at": view.created_at,
    }


def _validate_view_repo_ids(repo_ids: list[str]) -> None:
    known = set(config.get_repos())
    unknown = [r for r in repo_ids if r not in known]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown repo(s) in view: {', '.join(unknown)}",
        )
    if len(set(repo_ids)) != len(repo_ids):
        raise HTTPException(status_code=400, detail="Duplicate repo ids in view")


@app.get("/views")
async def list_views():
    views = await db.list_views()
    return [_view_dict(v) for v in views]


class CreateViewRequest(BaseModel):
    name: str
    repo_ids: list[str] = []


@app.post("/views")
async def create_view(req: CreateViewRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if name.lower() == "all":
        raise HTTPException(
            status_code=400,
            detail="'All' is reserved — it's the implicit default view",
        )
    _validate_view_repo_ids(req.repo_ids)
    existing = await db.list_views()
    if any(v.name == name for v in existing):
        raise HTTPException(
            status_code=400, detail=f"A view named {name!r} already exists"
        )
    view = View(name=name, repo_ids=req.repo_ids, position=len(existing))
    await db.create_view(view)
    await daemon.notify("view_updated", {"id": view.id})
    return _view_dict(view)


class UpdateViewRequest(BaseModel):
    name: str | None = None
    repo_ids: list[str] | None = None
    position: int | None = None


@app.put("/views/{view_id}")
async def update_view(view_id: str, req: UpdateViewRequest):
    view = await db.get_view(view_id)
    if view is None:
        raise HTTPException(status_code=404, detail="View not found")

    updates: dict[str, object] = {}
    if req.name is not None:
        name = req.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        if name.lower() == "all":
            raise HTTPException(
                status_code=400,
                detail="'All' is reserved — it's the implicit default view",
            )
        if name != view.name:
            existing = await db.list_views()
            if any(v.name == name and v.id != view_id for v in existing):
                raise HTTPException(
                    status_code=400, detail=f"A view named {name!r} already exists"
                )
        updates["name"] = name
    if req.repo_ids is not None:
        _validate_view_repo_ids(req.repo_ids)
        updates["repo_ids"] = req.repo_ids
    if req.position is not None:
        updates["position"] = req.position

    if not updates:
        return _view_dict(view)

    updated = await db.update_view(view_id, **updates)
    assert updated is not None
    await daemon.notify("view_updated", {"id": view_id})
    return _view_dict(updated)


@app.delete("/views/{view_id}")
async def delete_view(view_id: str):
    view = await db.get_view(view_id)
    if view is None:
        raise HTTPException(status_code=404, detail="View not found")
    # `slates.view_id` is required — deleting the view would leave its
    # slates dangling. Block until the user moves or deletes those slates
    # first. (Archived slates count too; treat them all the same.)
    attached = await db.count_slates_in_view(view_id)
    if attached > 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"View {view.name!r} has {attached} slate"
                f"{'s' if attached != 1 else ''} attached. "
                "Move or delete them before deleting this view."
            ),
        )
    await db.delete_view(view_id)
    await daemon.notify("view_updated", {"id": view_id, "deleted": True})
    return {"status": "deleted"}


class ViewOrderRequest(BaseModel):
    order: list[str]


@app.put("/views/order")
async def set_view_order(req: ViewOrderRequest):
    views = await db.list_views()
    known = {v.id for v in views}
    unknown = [vid for vid in req.order if vid not in known]
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Unknown view(s): {', '.join(unknown)}"
        )
    if len(set(req.order)) != len(req.order):
        raise HTTPException(status_code=400, detail="Duplicate view ids in order")
    for position, view_id in enumerate(req.order):
        await db.update_view(view_id, position=position)
    return {"status": "ok"}


# --- Settings endpoints ---


@app.get("/settings")
async def get_settings():
    settings = await db.list_settings()
    return {"author": settings.get("author", "")}


class UpdateSettingsRequest(BaseModel):
    author: str | None = None


@app.put("/settings")
async def update_settings(req: UpdateSettingsRequest):
    if req.author is not None:
        await db.set_setting("author", req.author.strip())
    await config.refresh()
    settings = await db.list_settings()
    return {"author": settings.get("author", "")}


# --- Daemon endpoints ---


@app.get("/daemon/runs")
async def list_daemon_runs(limit: int = 20):
    return await db.list_daemon_runs(limit)


@app.post("/daemon/runs")
async def trigger_daemon_run():
    asyncio.create_task(daemon.run_once())
    return {"status": "started"}


# --- Worktop endpoints ---


async def _install_worktop_files(worktop: Worktop) -> None:
    """Install plait's per-worktop files into a freshly created worktree.

    Wraps claude.write_worktop_claude_md so copy_globs drift (a configured
    glob that no longer resolves to gitignored files) fails the request
    with a 400 instead of a 500 — and rolls back the just-created worktree
    (and its branch) so fixing the config and retrying works cleanly.
    """
    try:
        await claude.write_worktop_claude_md(
            worktop.worktree_path, worktop.id, worktop.repo
        )
    except RuntimeError as e:
        try:
            await git.remove_worktree(worktop.repo, worktop.worktree_path)
            repo = config.get_repo(worktop.repo)
            await git.run("git", "branch", "-D", worktop.branch, cwd=repo.path)
        except Exception:
            logger.warning(
                f"Failed to clean up worktree for worktop {worktop.id} "
                "after copy_globs error"
            )
        raise HTTPException(status_code=400, detail=str(e))


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
        worktop.branch = f"plait/worktop-{worktop.id[:8]}"
    else:
        raise HTTPException(status_code=400, detail="Either pr_url or repo is required")

    try:
        worktop.worktree_path = await git.create_worktree(
            worktop.repo, worktop.branch, worktop.id
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await _install_worktop_files(worktop)

    if worktop.pr_number:
        ci = await git.get_ci_status(worktop.repo, worktop.pr_number)
        worktop.ci_status = CIStatus(ci)
        worktop.pr_comment_count = (
            await git.get_pr_comment_count(worktop.repo, worktop.pr_number)
        ) or 0

    await db.create_worktop(worktop)
    return await _worktop_dict(worktop)


class ReviewPRRequest(BaseModel):
    pr_url: str


@app.post("/review-pr")
async def review_pr(req: ReviewPRRequest):
    """Check out a PR in a throwaway worktree, open it in VS Code, and prime a
    Claude session to help review it.

    Called by the "Review locally" browser extension. Reviews are lightweight:
    no worktop, no DB record, no daemon involvement — just a worktree on disk
    and a Claude session in VS Code. The worktree is a branch wired to push
    back to the PR's head repo (see git.create_review_worktree), so the reviewer
    can commit and push fixes. The worktree persists on disk until removed by
    hand.
    """
    try:
        repo_id, pr_number = git.parse_pr_url(req.pr_url)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Re-click fast path: the worktree is persistent and keyed to the PR, so if
    # it already exists we reopen it without `gh pr view` or any fetch — the
    # branch and head URL are only needed to *build* the worktree the first time.
    worktree_path = git.existing_review_worktree(repo_id, pr_number)
    if worktree_path is None:
        try:
            pr_info = await git.get_pr_info_from_url(req.pr_url)
            worktree_path = await git.create_review_worktree(
                repo_id, pr_number, pr_info["branch"], pr_info["head_url"]
            )
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Make plait's bundled skills/agents (e.g. code-reviewer) available, and
    # write the review brief to a temp file (kept out of the PR checkout so the
    # review diff stays clean). The brief contains backticks, `$(...)`, and
    # apostrophes, so it can't be passed on a command line — instead we launch
    # claude with a short, shell-safe pointer to its absolute path. Single
    # quotes (not double) so the arg survives AppleScript's own quoting; the
    # temp path is alphanumeric, so single-quoting it is safe.
    try:
        await git.copy_local_files(repo_id, worktree_path)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    claude.install_claude_files(worktree_path)
    # copy_local_files stripped the study content from metr repos; give the
    # reviewer the opt-back-in skill. No worktop id — reviews have no tends.
    if config.get_repo(repo_id).metr:
        claude.install_metr_claude_files(worktree_path, None, repo_id)
    main_branch = await git.main_branch(repo_id)
    main_ref = await git.main_ref(repo_id)
    prompt = claude.review_prompt(req.pr_url, pr_number, main_branch, main_ref)
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="plait-review-", suffix=".md", delete=False
    ) as f:
        f.write(prompt)
        brief_path = f.name

    _open_vscode_terminal(
        worktree_path,
        f"claude 'Read {brief_path} and follow its instructions.'",
        compare_ref=main_ref,
    )

    return {"status": "opened", "worktree_path": worktree_path}


class OpenIssueRequest(BaseModel):
    issue_url: str


@app.post("/open-issue")
async def open_issue(req: OpenIssueRequest):
    """Open (or create) the worktop for a GitHub issue.

    Called by the "Open in plait" browser extension button on issue pages.
    Unlike /review-pr — which makes an ephemeral, daemon-invisible worktree —
    this creates a full worktop: DB record, daemon tends, the works. If an
    open worktop already tracks the issue, return it; otherwise create one
    and seed a session investigating the issue. Archived worktops never
    match — a click after the fix merged means a fresh round of work.
    """
    try:
        repo_id, issue_number = git.parse_issue_url(req.issue_url)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Rebuild the URL from the configured upstream so lookups are exact:
    # trailing fragments (#issuecomment-...) and owner/repo casing in the
    # clicked URL all key to the same worktop.
    upstream = config.get_repo(repo_id).upstream
    issue_url = f"https://github.com/{upstream}/issues/{issue_number}"

    existing = await db.get_open_worktop_by_issue(issue_url)
    if existing:
        return {
            "worktop_id": existing.id,
            "url": f"http://localhost:5173/worktops/{existing.id}",
            "created": False,
        }

    worktop = Worktop(repo=repo_id, issue_url=issue_url)
    worktop.branch = f"plait/worktop-{worktop.id[:8]}"
    try:
        worktop.worktree_path = await git.create_worktree(
            repo_id, worktop.branch, worktop.id
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await _install_worktop_files(worktop)
    await db.create_worktop(worktop)

    session = Session(worktop_id=worktop.id, role=SessionRole.user)
    await db.create_session(session)
    cmd, cwd = user_worktop_cmd(session.id, worktop)
    spawn_session(session.id, cmd, cwd, initial_input=f"investigate {issue_url}")

    await daemon.notify("worktop_updated", {"id": worktop.id, "status": "open"})
    return {
        "worktop_id": worktop.id,
        "url": f"http://localhost:5173/worktops/{worktop.id}",
        "created": True,
    }


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
    if worktop.status is not WorktopStatus.archived:
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


@app.post("/worktops/{worktop_id}/tends-enabled")
async def set_tends_enabled(worktop_id: str, body: dict):
    """Toggle whether the daemon auto-spawns tend sessions for this worktop.

    Manual tends (POST /worktops/{id}/sync) bypass this gate and always
    run. Persists to the DB so the setting survives server restarts.
    """
    enabled = bool(body.get("enabled"))
    updated = await db.update_worktop(worktop_id, tends_enabled=int(enabled))
    if updated is None:
        raise HTTPException(status_code=404, detail="Worktop not found")
    await daemon.notify("worktop_updated", {"id": worktop_id, "tends_enabled": enabled})
    return await _worktop_dict(updated)


class RenameWorktopRequest(BaseModel):
    name: str | None


@app.put("/worktops/{worktop_id}/name")
async def rename_worktop(worktop_id: str, req: RenameWorktopRequest):
    """Set or clear the worktop's display name.

    Clearing (null or blank) marks the worktop unnamed, so the daemon
    auto-names it again on its next tick.
    """
    name = req.name.strip() if req.name else None
    updated = await db.update_worktop(worktop_id, name=name or None)
    if updated is None:
        raise HTTPException(status_code=404, detail="Worktop not found")
    await daemon.notify("worktop_updated", {"id": worktop_id, "name": updated.name})
    return await _worktop_dict(updated)


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

    _open_vscode_terminal(worktop.worktree_path, f"claude --resume {session_id}")
    return {"status": "opened"}


def _vscode_applescript(terminal_cmd: str, compare_ref: str | None) -> str:
    """Build the AppleScript that puppets VS Code after `code <path>` launches.

    Always focuses an integrated terminal (⌘;) and types `terminal_cmd`. When
    `compare_ref` is given, additionally opens the command palette and drives
    GitLens' "Compare Working Tree with…" against that ref, so the PR diff is
    waiting in the Search & Compare view. The ref picker fuzzy-matches, so a
    fully-qualified ref (e.g. `upstream/main`) lands as the top hit.

    `terminal_cmd` and `compare_ref` are typed verbatim, so neither may contain
    a literal double quote — that would close AppleScript's string. Shell single
    quotes are fine (used to pass an initial prompt arg to `claude`); remote refs
    like `upstream/main` are safe.
    """
    script = f"""delay 3
tell application "Visual Studio Code" to activate
delay 0.3
tell application "System Events"
    tell process "Code"
        keystroke ";" using {{command down}}
        delay 0.3
        keystroke "{terminal_cmd}"
        keystroke return"""
    if compare_ref is not None:
        script += f"""
        delay 0.5
        keystroke "p" using {{command down, shift down}}
        delay 0.5
        keystroke "GitLens: Compare Working Tree with"
        delay 0.6
        keystroke return
        delay 1.0
        keystroke "{compare_ref}"
        delay 0.6
        keystroke return"""
    script += """
    end tell
end tell"""
    return script


def _open_vscode_terminal(
    worktree_path: str, terminal_cmd: str, compare_ref: str | None = None
) -> None:
    """Open VS Code at a worktree and run `terminal_cmd` in an integrated
    terminal; optionally also open a GitLens diff against `compare_ref`.

    macOS-only. After launching `code`, drives the VS Code UI via AppleScript
    (see `_vscode_applescript`). Both subprocesses are fire-and-forget; if VS
    Code is slow to launch or the host process lacks Accessibility permission,
    the keystrokes silently no-op.
    """
    subprocess.Popen(["code", worktree_path])
    subprocess.Popen(
        ["osascript", "-e", _vscode_applescript(terminal_cmd, compare_ref)]
    )


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
        daemon.SESSION_IDLE_TIMEOUT if session.role is SessionRole.daemon else None
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
        c.status is WorktopStatus.archived for c in worktops
    )
    d["is_archived"] = slate.archived or all_worktops_archived
    return d


class CreateSlateRequest(BaseModel):
    view_id: str
    # Optional override of the view's repo_ids snapshot. If omitted, the
    # slate's scope is exactly the view's `repo_ids` at this moment.
    repo_ids: list[str] | None = None


@app.post("/slates")
async def create_slate(req: CreateSlateRequest):
    """Create a slate scoped to a view's repos.

    Every slate belongs to exactly one view (`view_id` is required). The
    slate's `repo_ids` is snapshotted at creation — defaulting to the
    view's repo set, or to an explicit override if `repo_ids` is given.
    Later edits to the view don't change the slate's scope.
    """
    view = await db.get_view(req.view_id)
    if view is None:
        raise HTTPException(status_code=404, detail="View not found")

    known = set(config.get_repos())
    if req.repo_ids is not None:
        repo_ids = list(req.repo_ids)
    else:
        repo_ids = list(view.repo_ids)

    # Drop any unknown repos so we don't try to create a worktree from
    # config we no longer have. Preserve order otherwise.
    repo_ids = [r for r in repo_ids if r in known]
    if not repo_ids:
        raise HTTPException(
            status_code=400,
            detail="Slate scope is empty — no repos to operate on.",
        )

    slate = Slate(repo_ids=repo_ids, view_id=view.id)
    await db.create_slate(slate)

    try:
        await git.create_slate_worktrees(slate.id, slate.repo_ids)
    except RuntimeError as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to create slate worktrees: {e}"
        )

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

    slate = await db.get_slate(slate_id)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")
    scope_ids = await _slate_scope_repo_ids(slate)

    exploration_dir = str(git.WORKTREE_ROOT / f"slate-{slate_id}")
    await db.update_session(session_id, ended_at=None)
    cmd, cwd = resume_cmd(
        session.id,
        exploration_dir,
        claude.slate_system_prompt(
            slate_id, exploration_dir, _slate_repo_worktrees(slate_id, scope_ids)
        ),
    )
    idle_timeout = (
        daemon.SESSION_IDLE_TIMEOUT if session.role is SessionRole.daemon else None
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

    slate = await db.get_slate(slate_id)
    if not slate:
        raise HTTPException(status_code=404, detail="Slate not found")
    scope_ids = await _slate_scope_repo_ids(slate)

    exploration_dir = str(git.WORKTREE_ROOT / f"slate-{slate_id}")
    repo_worktrees = _slate_repo_worktrees(slate_id, scope_ids)
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

    # Remove slate exploration worktrees from the snapshotted scope.
    scope_ids = await _slate_scope_repo_ids(slate)
    try:
        await git.remove_slate_worktrees(slate_id, scope_ids)
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
    worktop.branch = f"plait/worktop-{worktop.id[:8]}"

    try:
        worktop.worktree_path = await git.create_worktree(
            req.repo, worktop.branch, worktop.id
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await _install_worktop_files(worktop)

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

    await _install_worktop_files(worktop)

    await db.create_worktop(worktop)
    await daemon.notify("slate_updated", {"id": slate_id})
    await daemon.notify("worktop_updated", {"id": worktop.id, "status": "open"})
    return {
        "worktop_id": worktop.id,
        "worktree_path": worktop.worktree_path,
        "branch": branch,
    }
