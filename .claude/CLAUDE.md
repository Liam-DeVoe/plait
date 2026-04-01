# CLAUDE.md

## Project Overview

**Orrery** is a local development tool for managing multiple PRs across the hegel repositories. It automatically keeps branches rebased on main (using Claude to resolve conflicts), monitors CI, and provides a web UI for managing cross-repo work.

## Glossary

- **Orrery** — the tool itself
- **Sortie** — a cross-repo initiative (e.g. "make this change across hegel repos"). Spawns one cell per repo.
- **Cell** — a per-repo unit of work. Contains a git worktree, branch, PR link, CI status, and sessions. Lifecycle: active → archived (on merge) → optionally re-opened.
- **Session** — a Claude conversation scoped to a cell's worktree.
- **Daemon** — the background process that keeps cells rebased on main, resolves conflicts via Claude, and monitors CI.

## Tech Stack

- **Backend**: Python, FastAPI, SQLite, async
- **Frontend**: React, TypeScript, Tailwind CSS
- **Claude interaction**: Claude Code CLI as subprocess (`claude -p` for daemon tasks, embedded terminal for interactive sessions in MVP)
- **Git operations**: subprocess calls to `git` and `gh` CLI

## Repository Structure

```
server/              # Python FastAPI backend
  models.py          # SQLAlchemy/dataclass models
  db.py              # SQLite persistence
  api.py             # REST + WebSocket endpoints
  daemon.py          # Background rebase/CI loop
  claude.py          # Claude process management
  git.py             # Git/worktree operations
web/                 # React frontend
  src/
    components/      # UI components
    pages/           # Page-level components
    api/             # API client
PLAN.md              # Implementation plan
```

## Build & Run

```bash
# Backend
cd server
uv run uvicorn server.api:app --reload

# Frontend
cd web
npm install
npm run dev
```

## Code Style

After making changes to Python files, run `just format` to format them with `shed`.

## Design Principles

- Cells are independent — a cell can exist without a sortie
- The daemon operates on all active cells regardless of sortie membership
- Claude sessions are scoped to a cell's worktree directory
- Daemon sessions (rebase, CI fix) are automatic but visible in the cell's session history
- The web UI is the primary interface; "open in VS Code" drops you into the worktree
