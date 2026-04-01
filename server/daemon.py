from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from server import claude, db, git
from server.models import (
    Cell,
    CellStatus,
    CIStatus,
    RebaseStatus,
    Session,
    SessionRole,
    SessionStatus,
)

logger = logging.getLogger(__name__)

# Will be set by the API server to broadcast updates
notify_callback: asyncio.Queue | None = None

POLL_INTERVAL = 300  # seconds (5 minutes)


async def notify(event: str, data: dict) -> None:
    if notify_callback is not None:
        await notify_callback.put({"event": event, "data": data})


async def process_cell(cell: Cell) -> None:
    """Process a single cell: check rebase status, CI status."""
    try:
        # Fetch latest from origin
        await git.fetch_origin(cell.repo)

        # Check if behind main
        if await git.is_behind_main(cell.worktree_path):
            logger.info(
                f"Cell {cell.id} ({cell.repo}:{cell.branch}) is behind main, rebasing"
            )
            await db.update_cell(cell.id, rebase_status=RebaseStatus.rebasing)
            await notify("cell_updated", {"id": cell.id, "rebase_status": "rebasing"})

            success, output = await git.rebase_onto_main(cell.worktree_path)

            if success:
                # Clean rebase, force push
                push_ok, push_out = await git.force_push(
                    cell.worktree_path, cell.branch
                )
                if push_ok:
                    await db.update_cell(cell.id, rebase_status=RebaseStatus.current)
                    await notify(
                        "cell_updated", {"id": cell.id, "rebase_status": "current"}
                    )
                    logger.info(f"Cell {cell.id} rebased and pushed successfully")
                else:
                    await db.update_cell(cell.id, rebase_status=RebaseStatus.failed)
                    await notify(
                        "cell_updated", {"id": cell.id, "rebase_status": "failed"}
                    )
                    logger.error(f"Cell {cell.id} push failed: {push_out}")
            else:
                # Conflicts — use Claude to resolve
                logger.info(f"Cell {cell.id} has conflicts, invoking Claude")
                await db.update_cell(cell.id, rebase_status=RebaseStatus.conflict)
                await notify(
                    "cell_updated", {"id": cell.id, "rebase_status": "conflict"}
                )

                # Create a daemon session record
                session = Session(
                    cell_id=cell.id,
                    role=SessionRole.daemon,
                    trigger="rebase",
                    status=SessionStatus.running,
                )
                await db.create_session(session)

                claude_ok, claude_out = await claude.resolve_conflicts(
                    cell.worktree_path, cell.branch
                )

                if claude_ok:
                    push_ok, push_out = await git.force_push(
                        cell.worktree_path, cell.branch
                    )
                    if push_ok:
                        await db.update_cell(
                            cell.id, rebase_status=RebaseStatus.current
                        )
                        await notify(
                            "cell_updated", {"id": cell.id, "rebase_status": "current"}
                        )
                    else:
                        await db.update_cell(cell.id, rebase_status=RebaseStatus.failed)
                        await notify(
                            "cell_updated", {"id": cell.id, "rebase_status": "failed"}
                        )

                    await db.update_session(
                        session.id,
                        status=SessionStatus.completed.value,
                        transcript=claude_out,
                        ended_at=datetime.now(timezone.utc).isoformat(),
                    )
                else:
                    await db.update_cell(cell.id, rebase_status=RebaseStatus.failed)
                    await notify(
                        "cell_updated", {"id": cell.id, "rebase_status": "failed"}
                    )
                    await db.update_session(
                        session.id,
                        status=SessionStatus.failed.value,
                        transcript=claude_out,
                        ended_at=datetime.now(timezone.utc).isoformat(),
                    )
                    logger.error(
                        f"Claude failed to resolve conflicts for cell {cell.id}"
                    )

        # Check CI status if cell has a PR
        if cell.pr_number:
            ci = await git.get_ci_status(cell.repo, cell.pr_number)
            ci_status = CIStatus(ci)
            if ci_status != cell.ci_status:
                await db.update_cell(cell.id, ci_status=ci_status)
                await notify("cell_updated", {"id": cell.id, "ci_status": ci})

    except Exception:
        logger.exception(f"Error processing cell {cell.id}")


async def daemon_loop() -> None:
    """Main daemon loop. Runs forever, processing all active cells periodically."""
    logger.info("Daemon started")
    while True:
        try:
            cells = await db.list_cells(status=CellStatus.active)
            logger.info(f"Daemon tick: processing {len(cells)} active cells")

            for cell in cells:
                await process_cell(cell)

        except Exception:
            logger.exception("Daemon loop error")

        await asyncio.sleep(POLL_INTERVAL)
