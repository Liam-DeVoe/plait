# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Orrery** is a local development tool for managing multiple PRs across the hegel repositories. It automatically keeps branches rebased on main (using Claude to resolve conflicts), monitors CI, and provides a web UI for managing cross-repo work.

## Glossary

- **Orrery** -- the tool itself
- **Sortie** -- a cross-repo initiative (e.g. "make this change across hegel repos"). Spawns one cell per repo.
- **Cell** -- a per-repo unit of work. Contains a git worktree, branch, PR link, CI status, and sessions. Lifecycle: active -> archived (on merge) -> optionally re-opened.
- **Session** -- a Claude conversation scoped to a cell's worktree. Created by the daemon (rebase, ci_fix, sortie triggers) or by the user via the API.
- **Daemon** -- the background async task that polls every 5 minutes, processing all active cells: rebasing behind-main branches, checking CI status, and spawning Claude to fix failures.

## Build & Run

```bash
just server      # cd server && uv run uvicorn server.api:app --reload --port 8000
just web         # cd web && npm run dev
just test        # uv run pytest tests/ (accepts extra args: just test -k test_name)
just format      # uv run shed (Python formatting)
just install     # uv sync && cd web && npm install
```

## Code Style

After making changes to Python files, run `just format` to format them with `shed`.

## Architecture

### Data Flow

The FastAPI app (`server/api.py`) starts the daemon and a WebSocket broadcaster as background tasks via the lifespan handler. The daemon (`server/daemon.py`) polls active cells and pushes events through an `asyncio.Queue` that the broadcaster forwards to all connected WebSocket clients. The frontend connects via WebSocket and refreshes data on `cell_updated` events.

### Daemon Processing Pipeline

For each active cell, `daemon.process_cell()`:
1. `git.fetch_origin()` then `git.is_behind_main()` -- if behind, rebase
2. `git.rebase_onto_main()` -- if clean, force-push; if conflicts, abort rebase then invoke `claude.resolve_conflicts()` (which runs `claude -p` in the worktree)
3. Check CI via `git.get_ci_status()` (uses `gh pr checks`) -- if newly failing, invoke `claude.fix_ci()` with the failure logs

All Claude interactions go through `server/claude.py`, which shells out to `claude -p <prompt>` in the cell's worktree directory. The daemon creates Session records for every Claude invocation.

### Database

SQLite via `aiosqlite` (`server/db.py`). Each function opens its own connection (no connection pooling). The DB file is `orrery.db` at the project root. Schema is auto-created on every `get_db()` call via `CREATE TABLE IF NOT EXISTS`.

Three tables: `cells`, `sorties`, `sessions`. The `sorties.repos` column stores a JSON array. Session trigger types: `"rebase"`, `"ci_fix"`, `"sortie"`, or `None` for user sessions.

### Git/Worktree Layout

`server/git.py` manages worktrees. `REPO_ROOT` is the parent of the coordination directory (where all hegel repos live as siblings). `WORKTREE_ROOT` is `./worktrees/`. Each cell gets a worktree at `worktrees/<cell-uuid>/`, created from either an existing remote branch or a new branch off `origin/main`. Repos are identified by their GitHub `owner/repo` string (e.g. `hegeldev/hegel-rust`), and `git.repo_path()` resolves that to the sibling directory name.

### Frontend

Single-page React app (`web/src/App.tsx`) with two views: Cells and Sorties. All API calls go through `web/src/api.ts`. The Vite dev server proxies API requests to the backend. No routing library -- navigation is state-driven.

## Testing

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (all async test functions run automatically).

Key test fixtures (in `tests/conftest.py`):
- `_use_memory_db` (autouse): redirects `db.DB_PATH` to a temp file per test
- `init_db`: initializes the schema (required by tests that touch the DB)
- `git_env`: creates a bare remote + clone in a temp dir, patches `git.WORKTREE_ROOT` and `git.REPO_ROOT`. Provides a `GitEnv` helper with `add_commit()`, `push()`, `create_branch()` etc.
- `mock_gh`: intercepts `gh` CLI calls in `git.run()` with pattern-matched canned responses, while letting real `git` commands through
- `mock_claude`: patches `claude.run_claude_headless` with an `AsyncMock`

API tests use `httpx.AsyncClient` with `ASGITransport` against the FastAPI app directly (no real server process).

## Design Principles

- Cells are independent -- a cell can exist without a sortie
- The daemon operates on all active cells regardless of sortie membership
- Claude sessions are scoped to a cell's worktree directory
- Daemon sessions (rebase, CI fix) are automatic but visible in the cell's session history
- The web UI is the primary interface; "open in VS Code" drops you into the worktree
