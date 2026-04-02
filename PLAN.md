# Orrery — Implementation Plan

## Phase 1: Core (MVP)

The smallest thing that solves the immediate pain: auto-merge with conflict resolution, cell management, and a basic web UI.

### 1.1 Project scaffold
- [x] Python backend with FastAPI, SQLite
- [x] React + TypeScript + Tailwind frontend
- [x] `just` task runner for common commands

### 1.2 Data model & persistence
- [x] SQLite schema for cells, sorties, sessions
- [x] CRUD operations for each entity
- [x] Cell lifecycle management (active → archived)

### 1.3 Git/worktree operations
- [x] Create worktree for a cell (from existing PR or fresh branch)
- [x] Delete/clean up worktrees on archive
- [x] Merge origin/main into a worktree branch
- [x] Detect and report conflicts

### 1.4 Cell management API
- [x] `POST /cells` — create cell (from PR URL or repo + branch)
- [x] `GET /cells` — list all cells with status
- [x] `GET /cells/:id` — cell detail (sessions, CI status, sync status)
- [x] `POST /cells/:id/archive` — archive cell, clean up worktree
- [x] `POST /cells/:id/reopen` — re-open archived cell
- [x] `POST /cells/:id/sync` — manually trigger sync
- [x] `DELETE /cells/:id` — hard delete

### 1.5 Daemon (sync loop)
- [x] Background async task on a timer (configurable, default 5 min)
- [x] For each active cell: fetch, check if behind main, merge
- [x] On conflict: spawn `claude -p` in the worktree to resolve
- [x] Push after successful resolution
- [x] Record daemon sessions in the database

### 1.6 Web UI MVP
- [x] Cell list view with status indicators (CI, sync status)
- [x] Cell detail view showing sessions and metadata
- [x] "Open in VS Code" button (launches `code <worktree_path>`)
- [x] Manual sync trigger button
- [x] Create cell form (paste PR URL or pick repo + branch)
- [x] WebSocket for live status updates

## Phase 2: CI Monitoring & Sorties

### 2.1 CI monitoring
- [x] Daemon checks CI status via `gh` CLI for cells with open PRs
- [x] On failure: spawn Claude session to diagnose and fix
- [x] CI status shown in UI with link to GitHub checks

### 2.2 Sortie support
- [x] `POST /sorties` — create sortie with a prompt and list of repos
- [x] Spawns initial Claude session per repo to create cells
- [x] Sortie overview page in UI showing all child cells
- [x] Sortie status derived from child cell statuses

### 2.3 Session management
- [x] List sessions per cell
- [x] View session transcripts (daemon and user-initiated)
- [x] Launch new interactive session (embedded terminal running `claude` in worktree)

## Phase 3: Polish

### 3.2 Chrome extension
- [ ] "Open in Orrery" button on GitHub PR pages
- [ ] Creates cell if one doesn't exist, navigates to it in UI

### 3.3 Lifecycle & archival
- [ ] Auto-archive cells when PR merges (poll or webhook)
- [ ] Archive view in UI
- [x] Re-open archived cells (recreate worktree from branch)

### 3.4 Event-driven sync
- [ ] GitHub webhook listener for pushes to main
- [ ] Trigger merge immediately instead of waiting for poll
