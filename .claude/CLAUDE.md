# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Orrery** is a local development tool for managing multiple PRs across repositories. It automatically keeps branches rebased on main (using Claude to resolve conflicts), monitors CI, and provides a web UI for managing cross-repo work.

## Glossary

- **Orrery** -- the tool itself
- **Sortie** -- a cross-repo initiative (e.g. "make this change across all repos"). Spawns one cell per repo.
- **Cell** -- a per-repo unit of work. Contains a git worktree, branch, PR link, CI status, and sessions. Lifecycle: active -> archived (on merge) -> optionally re-opened.
- **Session** -- a Claude conversation scoped to a cell's worktree. Created by the daemon (tend, sortie triggers) or by the user via the API.
- **Daemon** -- the background async task that polls every 5 minutes, processing all active cells: rebasing behind-main branches, checking CI status, and spawning Claude to fix failures.

## Build & Run

```bash
just server      # cd server && uv run uvicorn server.api:app --reload --port 8000
just web         # cd web && npm run dev
just test        # uv run pytest tests/ -n auto (parallel; accepts extra args: just test -k test_name)
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
1. `git.fetch_origin()` then `git.is_behind_main()` — if behind, merge
2. `git.merge_from_main()` — if clean, push (only if branch is published); if conflicts, mark conflict status
3. Check CI via `git.get_ci_status()` (uses `gh pr checks`) and PR comment count
4. If anything changed (conflicts, CI status, new comments) — spawn a `"tend"` session via `spawn_session(print_mode=True)` to fix issues

All Claude interactions go through `server/sessions.py` via `spawn_session()`. The daemon creates Session records for every Claude invocation. Automatic retries are blocked if a session with the same trigger is already running (`_should_attempt()`); manual sync via the API bypasses this.

### Session Spawning (`server/sessions.py`)

Both the API (user sessions) and the daemon (headless sessions) use `spawn_session()` from `server/sessions.py`. It spawns a PTY-backed Claude process and returns an `asyncio.Task` for the exit code.

Three modes controlled by keyword args:
- **`print_mode=True`** (daemon): `claude -p <prompt> --session-id <id>` — headless, await the task for exit code
- **`resume=True`**: `claude --resume <id>` — continue a prior session
- **Default** (user): `claude --session-id <id>` — interactive, discard the task (fire-and-forget)

`_watch_pty()` runs alongside every session: flushes transcript to DB every 2 seconds (crash recovery), captures raw xterm state on exit for terminal replay.

### PTY Management (`server/pty.py`)

`PtyManager` singleton manages concurrent pseudoterminals via `os.openpty()`. Non-blocking master_fd registered with the event loop. Output buffered raw (for xterm.js replay) and ANSI-stripped (for transcript storage). WebSocket listeners receive bytes on arrival. Termination uses escalating signals: SIGHUP → SIGTERM → SIGKILL.

### Prompt Templates (`server/claude.py`)

`claude.py` generates system/user prompts by loading templates from `prompts.toml` and formatting them with cell/sortie context (cell_id, base_url, branch, etc.). No subprocess logic — that's in `sessions.py`.

### Database

SQLite via `aiosqlite` (`server/db.py`). Each function opens its own connection (no connection pooling). The DB file is `orrery.db` at the project root. Schema is auto-created on every `get_db()` call via `CREATE TABLE IF NOT EXISTS`.

**NEVER delete or recreate the database.** When a schema change is needed, write a migration SQL script (e.g. `ALTER TABLE ... ADD COLUMN ...`) and run it against the production `orrery.db` via `sqlite3` to migrate in-place, preserving all existing data. Always ask before running the migration. Do not add migration logic to `init_db` or anywhere else — this is a single-user tool with one database.

Three tables: `cells`, `sorties`, `sessions`. The `sorties.repos` column stores a JSON array. Session trigger types: `"tend"`, `"sortie"`, or `None` for user sessions. Session `role` is `"daemon"` or `"user"`.

### Git/Worktree Layout

`server/git.py` manages worktrees. `REPO_ROOT` is the parent of the coordination directory (where all repos live as siblings). `WORKTREE_ROOT` is `./worktrees/`. Each cell gets a worktree at `worktrees/<cell-uuid>/`, created from either an existing remote branch or a new branch off `origin/main`. Repos are identified by their GitHub `owner/repo` string (e.g. `acme/my-repo`), and `git.repo_path()` resolves that to the sibling directory name.

### Frontend

Single-page React app (`web/src/App.tsx`) with two views: Cells and Sorties. All API calls go through `web/src/api.ts`. The Vite dev server proxies API requests to the backend. No routing library -- navigation is state-driven.

## Testing

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (all async test functions run automatically).

Key test fixtures (in `tests/conftest.py`):
- `_use_memory_db` (autouse): redirects `db.DB_PATH` to a temp file per test
- `init_db`: initializes the schema (required by tests that touch the DB)
- `git_env`: creates a bare remote + clone in a temp dir, patches `git.WORKTREE_ROOT` and `git.REPO_ROOT`. Provides a `GitEnv` helper with `add_commit()`, `push()`, `create_branch()` etc.
- `mock_gh`: intercepts `gh` CLI calls in `git.run()` with pattern-matched canned responses, while letting real `git` commands through
- `mock_claude` (autouse): patches `daemon.spawn_session` with a `_SpawnSessionMock` that simulates the session lifecycle (DB finalization, exit code). Set `mock.return_value = (True, "output")` for success or `(False, "error")` for failure; set `mock.side_effect` for custom async behavior

API tests use `httpx.AsyncClient` with `ASGITransport` against the FastAPI app directly (no real server process).

## Design Principles

- Cells are independent -- a cell can exist without a sortie
- The daemon operates on all active cells regardless of sortie membership
- Claude sessions are scoped to a cell's worktree directory
- Daemon sessions (tend, sortie) are automatic but visible in the cell's session history
- The web UI is the primary interface; "open in VS Code" drops you into the worktree
- No backwards compatibility for its own sake. When a new feature replaces old behavior, remove the old code paths. Keeping dead or redundant code around has a real cost; be aggressive about excising it.
