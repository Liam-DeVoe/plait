# Plait — Implementation Plan

## Phase 1: Core (MVP)

The smallest thing that solves the immediate pain: auto-merge with conflict resolution, worktop management, and a basic web UI.

### 1.1 Project scaffold
- [x] Python backend with FastAPI, SQLite
- [x] React + TypeScript + Tailwind frontend
- [x] `just` task runner for common commands

### 1.2 Data model & persistence
- [x] SQLite schema for worktops, slates, sessions
- [x] CRUD operations for each entity
- [x] Worktop lifecycle management (active → archived)

### 1.3 Git/worktree operations
- [x] Create worktree for a worktop (from existing PR or fresh branch)
- [x] Delete/clean up worktrees on archive
- [x] Merge origin/main into a worktree branch
- [x] Detect and report conflicts

### 1.4 Worktop management API
- [x] `POST /worktops` — create worktop (from PR URL or repo + branch)
- [x] `GET /worktops` — list all worktops with status
- [x] `GET /worktops/:id` — worktop detail (sessions, CI status, sync status)
- [x] `POST /worktops/:id/archive` — archive worktop, clean up worktree
- [x] `POST /worktops/:id/reopen` — re-open archived worktop
- [x] `POST /worktops/:id/sync` — manually trigger sync
- [x] `DELETE /worktops/:id` — hard delete

### 1.5 Daemon (sync loop)
- [x] Background async task on a timer (configurable, default 5 min)
- [x] For each active worktop: fetch, check if behind main, merge
- [x] On conflict: spawn `claude -p` in the worktree to resolve
- [x] Push after successful resolution
- [x] Record daemon sessions in the database

### 1.6 Web UI MVP
- [x] Worktop list view with status indicators (CI, sync status)
- [x] Worktop detail view showing sessions and metadata
- [x] "Open in VS Code" button (launches `code <worktree_path>`)
- [x] Manual sync trigger button
- [x] Create worktop form (paste PR URL or pick repo + branch)
- [x] WebSocket for live status updates

## Phase 2: CI Monitoring & Slates

### 2.1 CI monitoring
- [x] Daemon checks CI status via `gh` CLI for worktops with open PRs
- [x] On failure: spawn Claude session to diagnose and fix
- [x] CI status shown in UI with link to GitHub checks

### 2.2 Slate support
- [x] `POST /slates` — create slate with a prompt and list of repos
- [x] Spawns initial Claude session per repo to create worktops
- [x] Slate overview page in UI showing all child worktops
- [x] Slate status derived from child worktop statuses

### 2.3 Session management
- [x] List sessions per worktop
- [x] View session transcripts (daemon and user-initiated)
- [x] Launch new interactive session (embedded terminal running `claude` in worktree)

## Phase 3: Polish

### 3.2 Chrome extension
- [ ] "Open in Plait" button on GitHub PR pages
- [ ] Creates worktop if one doesn't exist, navigates to it in UI

### 3.3 Lifecycle & archival
- [ ] Auto-archive worktops when PR merges (poll or webhook)
- [ ] Archive view in UI
- [x] Re-open archived worktops (recreate worktree from branch)

### 3.4 Event-driven sync
- [ ] GitHub webhook listener for pushes to main
- [ ] Trigger merge immediately instead of waiting for poll
