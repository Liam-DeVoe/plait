# Orrery — Implementation Plan

## Phase 1: Core (MVP)

The smallest thing that solves the immediate pain: auto-rebase with conflict resolution, cell management, and a basic web UI.

### 1.1 Project scaffold
- Python backend with FastAPI, SQLite
- React + TypeScript + Tailwind frontend
- `just` task runner for common commands

### 1.2 Data model & persistence
- SQLite schema for cells, sorties, sessions
- CRUD operations for each entity
- Cell lifecycle management (active → archived)

### 1.3 Git/worktree operations
- Create worktree for a cell (from existing PR or fresh branch)
- Delete/clean up worktrees on archive
- Rebase a worktree branch onto origin/main
- Detect and report conflicts

### 1.4 Cell management API
- `POST /cells` — create cell (from PR URL or repo + branch)
- `GET /cells` — list all cells with status
- `GET /cells/:id` — cell detail (sessions, CI status, rebase status)
- `POST /cells/:id/archive` — archive cell, clean up worktree
- `POST /cells/:id/reopen` — re-open archived cell
- `POST /cells/:id/rebase` — manually trigger rebase
- `DELETE /cells/:id` — hard delete

### 1.5 Daemon (rebase loop)
- Background async task on a timer (configurable, default 5 min)
- For each active cell: fetch, check if behind main, rebase
- On conflict: spawn `claude -p` in the worktree to resolve
- Force-push after successful resolution
- Record daemon sessions in the database

### 1.6 Web UI MVP
- Cell list view with status indicators (CI, rebase status)
- Cell detail view showing sessions and metadata
- "Open in VS Code" button (launches `code <worktree_path>`)
- Manual rebase trigger button
- Create cell form (paste PR URL or pick repo + branch)
- WebSocket for live status updates

## Phase 2: CI Monitoring & Sorties

### 2.1 CI monitoring
- Daemon checks CI status via `gh` CLI for cells with open PRs
- On failure: spawn Claude session to diagnose and fix
- CI status shown in UI with link to GitHub checks

### 2.2 Sortie support
- `POST /sorties` — create sortie with a prompt and list of repos
- Spawns initial Claude session per repo to create cells
- Sortie overview page in UI showing all child cells
- Sortie status derived from child cell statuses

### 2.3 Session management
- List sessions per cell
- View session transcripts (daemon and user-initiated)
- Launch new interactive session (embedded terminal running `claude` in worktree)

## Phase 3: Polish

### 3.1 Interactive sessions upgrade
- Investigate Claude Code SDK/headless mode
- Build native chat UI if feasible, replacing embedded terminal

### 3.2 Chrome extension
- "Open in Orrery" button on GitHub PR pages
- Creates cell if one doesn't exist, navigates to it in UI

### 3.3 Lifecycle & archival
- Auto-archive cells when PR merges (poll or webhook)
- Archive view in UI
- Re-open archived cells (recreate worktree from branch)

### 3.4 Event-driven rebase
- GitHub webhook listener for pushes to main
- Trigger rebase immediately instead of waiting for poll
