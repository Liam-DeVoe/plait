-- Add per-repo metr flag: 1 if the repo's canonical clone participates in a
-- METR study (ccmetr tooling in its .claude/). Worktrees plait creates for a
-- metr repo have the study content stripped and get a /metr-repopulate skill
-- to opt back in per-worktree.
--
-- Run against the production DB with:
--   sqlite3 plait.db < migrations/2026-07-26-repos-metr.sql
ALTER TABLE repos ADD COLUMN metr INTEGER NOT NULL DEFAULT 0;
