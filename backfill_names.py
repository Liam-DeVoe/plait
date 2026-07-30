"""One-shot backfill: auto-name every unnamed worktop, including archived ones.

The daemon only auto-names *open* worktops, so anything archived before
the naming feature landed would stay "untitled" forever. This script
runs the same naming path (server/naming.py) over every worktop with no
name. Archived worktops have no worktree on disk, so they're named from
session transcripts alone; worktops with no signal at all are skipped.

Idempotent: already-named worktops are never touched, so it's safe to
rerun.

Stop the plait server before running (both would open plait.db and
spawn naming calls).

Usage:
    uv run python backfill_names.py
"""

from __future__ import annotations

import asyncio
import sys

from server import config, db, naming


async def backfill() -> int:
    await db.init_db()
    # Prime the synchronous config cache — naming's git-signal path
    # resolves repos through it. Normally done by the FastAPI lifespan.
    await config.refresh()

    worktops = await db.list_worktops()
    unnamed = [w for w in worktops if w.name is None]
    print(f"{len(worktops)} worktops, {len(unnamed)} unnamed.")

    named = 0
    skipped = 0
    for worktop in unnamed:
        try:
            name = await naming.maybe_name_worktop(worktop)
        except Exception as e:
            print(f"  ERROR {worktop.id} ({worktop.branch}): {e}")
            continue
        if name is None:
            skipped += 1
            print(f"  skipped {worktop.id} ({worktop.branch}): no signal or bad output")
        else:
            named += 1
            print(f"  named {worktop.id} ({worktop.branch}): {name!r}")

    print(f"\nDone: {named} named, {skipped} skipped.")
    return 0


def main() -> int:
    try:
        return asyncio.run(backfill())
    finally:
        asyncio.run(db.close_db())


if __name__ == "__main__":
    sys.exit(main())
