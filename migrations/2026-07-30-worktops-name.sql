-- Add per-worktop display name. NULL means unnamed; the daemon auto-names
-- unnamed worktops from session/commit signal, and the user can rename (or
-- clear, to trigger re-naming) via the UI.
--
-- Run against the production DB with:
--   sqlite3 plait.db < migrations/2026-07-30-worktops-name.sql
ALTER TABLE worktops ADD COLUMN name TEXT;
