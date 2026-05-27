"""One-shot migration: copy config.toml contents into plait.db.

Reads `config.toml` at the project root, populates the `repos` and
`settings` tables, and preserves the existing repo ordering from the
(now-defunct) `repo_order` table.

Idempotent against the `repos` table: if it already has rows, the
script exits without doing anything. Run once, then delete config.toml.

Usage:
    uv run python seed_db.py
"""

from __future__ import annotations

import asyncio
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from server import db
from server.models import Repo

CONFIG_PATH = Path(__file__).parent / "config.toml"


async def _existing_repo_order() -> list[str]:
    """Read the legacy `repo_order` table if it still exists.

    Returns repo IDs in ascending position order, or [] if the table is
    missing / empty.
    """
    conn = await db.get_db()
    try:
        cursor = await conn.execute(
            "SELECT repo_id FROM repo_order ORDER BY position ASC"
        )
        rows = await cursor.fetchall()
        return [row["repo_id"] for row in rows]
    except Exception:
        return []


async def seed() -> int:
    if not CONFIG_PATH.exists():
        print(f"No {CONFIG_PATH} found — nothing to seed.")
        return 0

    await db.init_db()

    repos_in_db = await db.list_repos()
    if repos_in_db:
        print(
            f"`repos` table already populated ({len(repos_in_db)} rows); "
            "skipping seed. Delete repos and rerun to re-seed."
        )
        return 0

    data = tomllib.loads(CONFIG_PATH.read_text())
    author = data.get("author", "")
    repos_section = data.get("repos", {})

    if not repos_section:
        print("config.toml has no `[repos.*]` entries; nothing to seed.")
        return 0

    # Honor the previously-saved repo ordering if it exists.
    legacy_order = await _existing_repo_order()
    seen: set[str] = set()
    ordered: list[str] = []
    for rid in legacy_order:
        if rid in repos_section and rid not in seen:
            ordered.append(rid)
            seen.add(rid)
    for rid in repos_section:
        if rid not in seen:
            ordered.append(rid)

    now = datetime.now(timezone.utc).isoformat()
    for position, repo_id in enumerate(ordered):
        info = repos_section[repo_id]
        kind = info.get("kind", "remote")
        if kind not in ("remote", "local"):
            print(
                f"Skipping {repo_id!r}: kind must be 'remote' or 'local' "
                f"(got {kind!r})"
            )
            continue
        upstream = info.get("upstream")
        if kind == "remote" and not upstream:
            print(f"Skipping {repo_id!r}: kind='remote' requires `upstream`")
            continue
        if kind == "local" and upstream:
            print(f"Skipping {repo_id!r}: kind='local' must not have `upstream`")
            continue
        repo = Repo(
            id=repo_id,
            path=Path(info["path"]),
            kind=kind,
            upstream=upstream,
            position=position,
            created_at=now,
        )
        await db.create_repo(repo)
        print(f"Seeded repo: {repo_id} ({kind})")

    if author:
        await db.set_setting("author", author)
        print(f"Seeded author: {author}")

    # Drop the legacy `repo_order` table — its data has been folded into
    # `repos.position` above.
    conn = await db.get_db()
    try:
        await conn.execute("DROP TABLE IF EXISTS repo_order")
        await conn.commit()
        print("Dropped legacy `repo_order` table.")
    except Exception as e:
        print(f"Could not drop `repo_order` table: {e}")

    print(
        "\nSeed complete. You can now delete config.toml — plait reads "
        "everything from the DB."
    )
    return 0


def main() -> int:
    try:
        return asyncio.run(seed())
    finally:
        # Make sure the connection is closed cleanly.
        asyncio.run(db.close_db())


if __name__ == "__main__":
    sys.exit(main())
