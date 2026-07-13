-- Add per-repo copy_globs: JSON list of globs (relative to the repo's
-- canonical clone) of gitignored files to copy into every worktree plait
-- creates for the repo.
--
-- Run against the production DB with:
--   sqlite3 plait.db < migrations/2026-07-13-repos-copy-globs.sql
ALTER TABLE repos ADD COLUMN copy_globs TEXT NOT NULL DEFAULT '[]';
