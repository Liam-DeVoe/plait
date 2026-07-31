-- Link worktops to the GitHub issue they were opened from, set by the
-- browser extension's "Open in plait" button. Later clicks on the same
-- issue reuse the open worktop instead of creating another.
--
-- Run against the production DB with:
--   sqlite3 plait.db < migrations/2026-07-30-worktops-issue-url.sql
ALTER TABLE worktops ADD COLUMN issue_url TEXT;
