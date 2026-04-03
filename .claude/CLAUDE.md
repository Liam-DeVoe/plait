# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Orrery** is a local development tool for managing multiple PRs across repositories. It automatically keeps branches rebased on main (using Claude to resolve conflicts), monitors CI, and provides a web UI for managing cross-repo work.

## Glossary

- **Orrery** -- the tool itself
- **Sortie** -- a cross-repo initiative (e.g. "make this change across all repos"). Spawns one cell per repo.
- **Cell** -- a per-repo unit of work. Contains a git worktree, branch, PR link, CI status, and sessions. Lifecycle: open -> archived (on merge) -> optionally re-opened.
- **Session** -- a Claude conversation scoped to a cell's worktree. Created by the daemon (tend, sortie triggers) or by the user via the API.
- **Daemon** -- the background async task that polls every 5 minutes, processing all active cells: rebasing behind-main branches, checking CI status, and spawning Claude to fix failures.

## Build & Run

```bash
just server      # uvicorn with --reload (watches server/ and prompts.toml)
just web         # Vite dev server (cd web && npm run dev)
just serve       # both server + web in parallel
just test        # uv run pytest tests/ -n auto (parallel; accepts extra args: just test -k test_name)
just format      # uv run shed (Python formatting)
just install     # uv sync && cd web && npm install
```

## Code Style

After making changes to Python files, run `just format` to format them with `shed`.

## Configuration

`config.toml` at the project root defines the managed repos and author:

```toml
author = "github-username"

[repos.my-repo]
path = "/absolute/path/to/local/clone"
upstream = "owner/repo"
```

Each repo entry needs `path` (local clone) and `upstream` (GitHub `owner/repo` for `gh` CLI). The `author` field is used to detect PR comment reactions (thumbs-up acknowledgments).

## Architecture

### Data Flow

The FastAPI app (`server/api.py`) starts the daemon and a WebSocket broadcaster as background tasks via the lifespan handler. The daemon (`server/daemon.py`) polls active cells and pushes events through an `asyncio.Queue` that the broadcaster forwards to all connected WebSocket clients. The frontend connects via WebSocket and refreshes data on `cell_updated` events.

### Daemon Processing Pipeline

For each active cell, `daemon.process_cell()`:
1. `git.fetch_origin()` then `git.is_behind_main()` — if behind, merge
2. `git.merge_from_main()` — if clean, push (only if branch is published); if conflicts, mark conflict status
3. Auto-archive if PR is merged/closed
4. Check CI via `git.get_ci_status()` (uses `gh pr checks`), comment count, and reaction count
5. If anything changed (conflicts, CI failure, new comments/reactions) — spawn a `"tend"` session to fix issues

Tend sessions are gated by `_in_flight` (prevents duplicate daemon sessions for the same cell+trigger) and per-cell locks (prevents concurrent `process_cell` execution). PR activity has a 5-minute cooldown to let reviewers finish.

### Session Spawning (`server/sessions.py`)

Command factories (`tend_cmd`, `user_cell_cmd`, `user_sortie_cmd`, `resume_cmd`) define how each session type is invoked. `spawn_session()` handles the PTY mechanics common to all types.

Three modes:
- **Tend** (daemon): interactive Claude with restricted `--allowedTools` (defined in `daemon_config.py`), prompt sent as initial PTY input
- **User cell**: interactive Claude with `--dangerously-skip-permissions`
- **Sortie**: interactive Claude orchestrating cross-repo work

`_watch_pty()` runs alongside every session: flushes transcript to DB every 2 seconds (crash recovery), captures raw xterm state on exit for terminal replay. Daemon sessions have a 5-minute idle timeout.

### Hook Endpoints

Claude sessions call back to Orrery via HTTP hooks to report state changes:
- `POST /hooks/cells/{id}/branch-updated` — after renaming a branch
- `POST /hooks/cells/{id}/pr-created` — after creating a PR
- `POST /hooks/cells/{id}/ci-failure-expected` — suppresses CI-failure as a tend trigger until the branch HEAD changes
- `POST /hooks/sessions/{id}/done` — signals task completion, terminates the PTY
- `POST /hooks/sorties/{id}/create-cell` — sortie orchestrator creates a cell per repo

The prompts in `prompts.toml` instruct Claude on when/how to call these hooks.

### Prompt Templates (`server/claude.py` + `prompts.toml`)

`prompts.toml` contains all textual prompts sent to Claude (cell system prompt, sortie system prompt, tend prompt). `claude.py` loads these templates and formats them with context variables. Edit `prompts.toml` to change Claude's behavior — the server auto-reloads it.

### PTY Management (`server/pty.py`)

`PtyManager` singleton manages concurrent pseudoterminals via `os.openpty()`. Non-blocking master_fd registered with the event loop. Output buffered raw (for xterm.js replay) and ANSI-stripped (for transcript storage). WebSocket listeners receive bytes on arrival. Termination uses escalating signals: SIGHUP → SIGTERM → SIGKILL.

### Database

SQLite via `aiosqlite` (`server/db.py`). Each function opens its own connection (no connection pooling). The DB file is `orrery.db` at the project root. Schema is auto-created on every `get_db()` call via `CREATE TABLE IF NOT EXISTS`.

**NEVER delete or recreate the database.** When a schema change is needed, write a migration SQL script (e.g. `ALTER TABLE ... ADD COLUMN ...`) and run it against the production `orrery.db` via `sqlite3` to migrate in-place, preserving all existing data. Always ask before running the migration. Do not add migration logic to `init_db` or anywhere else — this is a single-user tool with one database.

Four tables: `cells`, `sorties`, `sessions`, `daemon_runs`. Session trigger types: `"tend"`, `"sortie"`, or `None` for user sessions. Session `role` is `"daemon"` or `"user"`. Note: the `trigger` field on the Session model maps to `trigger_name` in the DB column.

### Git/Worktree Layout

`server/git.py` manages worktrees. `WORKTREE_ROOT` is `./worktrees/`. Two worktree patterns:
- **Cell worktrees**: `worktrees/<cell-uuid>/` — created from an existing remote branch or a new branch off `origin/main`
- **Sortie exploration worktrees**: `worktrees/sortie-<sortie-id>/<repo-id>/` — read-only detached HEAD at `origin/main` for each configured repo

Repos are identified by their config key (e.g. `hegel-core`), and `config.get_repo()` resolves that to a `Repo` with a local path and GitHub upstream.

### Frontend

Single-page React app (`web/src/App.tsx`) with pages for Cells, Cell Detail, Sorties, and Sortie Detail. All API calls go through `web/src/api.ts`. The Vite dev server proxies API requests to the backend. No routing library — navigation is state-driven.

## Testing

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (all async test functions run automatically). Tests run in parallel via `pytest-xdist`.

Key test fixtures (in `tests/conftest.py`):
- `_use_memory_db` (autouse): redirects `db.DB_PATH` to a temp file per test
- `init_db`: initializes the schema (required by tests that touch the DB)
- `git_env`: creates a bare remote + clone in a temp dir, patches `git.WORKTREE_ROOT` and config. Provides a `GitEnv` helper with `add_commit()`, `push()`, `create_branch()` etc. Uses a session-scoped template for speed.
- `mock_gh` (autouse): intercepts `gh` CLI calls in `git.run()` with pattern-matched canned responses via `mock.set_response(pattern, rc, stdout)`, while letting real `git` commands through
- `mock_claude` (autouse): patches `daemon.spawn_session` with a `_SpawnSessionMock`. Set `mock.return_value = 0` for success or `1` for failure; set `mock.side_effect` for custom async behavior. Automatically finalizes the session in DB.
- `mock_pty`: patches `pty_manager` in both `api` and `sessions` modules to avoid real PTY spawning. Not autouse — only needed for tests that exercise API session endpoints.

API tests use `httpx.AsyncClient` with `ASGITransport` against the FastAPI app directly (no real server process).

## Design Principles

- Cells are independent — a cell can exist without a sortie
- The daemon operates on all active cells regardless of sortie membership
- Claude sessions are scoped to a cell's worktree directory
- Daemon sessions (tend, sortie) are automatic but visible in the cell's session history
- The web UI is the primary interface; "open in VS Code" drops you into the worktree
- No backwards compatibility for its own sake. When a new feature replaces old behavior, remove the old code paths. Keeping dead or redundant code around has a real cost; be aggressive about excising it.
