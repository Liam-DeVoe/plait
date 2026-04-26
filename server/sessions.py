"""Shared session spawning and lifecycle management.

Command factories (*_cmd) define how each session type is invoked.
spawn_session() handles the PTY mechanics common to all types.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from server import claude, db, git
from server.models import Cell
from server.pty import pty_manager

logger = logging.getLogger(__name__)

# --- Command factories ---
# Each returns (cmd, cwd) for a specific session type.


async def tend_cmd(
    session_id: str, cell: Cell, has_conflict: bool
) -> tuple[list[str], str, str]:
    """Interactive daemon session to fix merge conflicts / CI failures.

    `has_conflict` tells the prompt whether to instruct Claude to merge
    from main (and resolve conflicts) or to leave the branch alone.

    Returns (cmd, cwd, prompt). The prompt should be passed as initial_input
    to spawn_session rather than baked into the command.
    """
    mb = await git.main_branch(cell.repo)
    prompt = claude.tend_prompt(cell.branch, cell.id, mb, has_conflict)
    return (
        [
            "claude",
            "--verbose",
            "--dangerously-skip-permissions",
            "--session-id",
            session_id,
            "--system-prompt",
            claude.orrery_system_prompt(session_id),
        ],
        cell.worktree_path,
        prompt,
    )


def user_cell_cmd(session_id: str, cell: Cell) -> tuple[list[str], str]:
    """Interactive user session in a cell worktree."""
    return [
        "claude",
        "--verbose",
        "--dangerously-skip-permissions",
        "--session-id",
        session_id,
        "--system-prompt",
        claude.orrery_system_prompt(session_id),
    ], cell.worktree_path


def user_sortie_cmd(
    session_id: str,
    sortie_id: str,
    exploration_dir: str,
    repo_worktrees: dict[str, str],
) -> tuple[list[str], str]:
    """Interactive user session to orchestrate a sortie."""
    system_prompt = claude.sortie_system_prompt(
        sortie_id, exploration_dir, repo_worktrees
    )
    return [
        "claude",
        "--verbose",
        "--dangerously-skip-permissions",
        "--session-id",
        session_id,
        "--system-prompt",
        system_prompt,
    ], exploration_dir


def resume_cmd(session_id: str, cwd: str) -> tuple[list[str], str]:
    """Resume a previously ended session."""
    return [
        "claude",
        "--verbose",
        "--dangerously-skip-permissions",
        "--resume",
        session_id,
    ], cwd


# --- Spawn mechanics ---


def spawn_session(
    session_id: str,
    cmd: list[str],
    cwd: str,
    *,
    initial_input: str = "",
    idle_timeout: float | None = None,
) -> asyncio.Task[int | None]:
    """Spawn a PTY session and start watching it.

    Returns the watcher task. Await it for the exit code (daemon use),
    or discard it for fire-and-forget (interactive sessions).

    Use a *_cmd() factory above to build the cmd list.

    idle_timeout: if set, terminate the session after this many seconds
    of no PTY output (i.e. Claude is sitting idle at the prompt).
    """
    pty_manager.spawn(session_id, cwd=cwd, cmd=cmd)

    if initial_input.strip():

        async def _send_initial_input():
            await asyncio.sleep(1.0)
            # Wrap in bracketed paste markers so the terminal treats multi-line
            # text as a single paste.
            paste_start = "\x1b[200~"
            paste_end = "\x1b[201~"
            pty_manager.write(
                session_id, f"{paste_start}{initial_input}{paste_end}".encode()
            )
            # Delay then Enter to submit the pasted text. Claude needs time
            # to process the paste end marker before accepting Enter.
            await asyncio.sleep(1.0)
            pty_manager.write(session_id, b"\r")

        asyncio.create_task(_send_initial_input())

    return asyncio.create_task(_watch_pty(session_id, idle_timeout=idle_timeout))


async def _watch_pty(
    session_id: str, *, idle_timeout: float | None = None
) -> int | None:
    """Watch a PTY process: flush transcript periodically, finalize on exit.

    Returns the process exit code (0 = success).
    """
    import time

    from server.daemon import notify

    flush_interval = 2.0
    while pty_manager.is_alive(session_id):
        transcript = pty_manager.get_transcript(session_id)
        if transcript:
            await db.update_session(session_id, transcript=transcript)

        if idle_timeout is not None:
            pty_session = pty_manager.get(session_id)
            if (
                pty_session is not None
                and time.monotonic() - pty_session.last_output_at > idle_timeout
            ):
                logger.info(
                    f"Session {session_id} idle for {idle_timeout}s, terminating"
                )
                await pty_manager.terminate(session_id)
                break

        await asyncio.sleep(flush_interval)

    pty_session = pty_manager.get(session_id)
    if pty_session is None:
        return None

    exit_code = pty_session.exit_code
    transcript = pty_manager.get_transcript(session_id)
    xterm_state = pty_manager.get_raw_output(session_id)
    pty_manager.remove(session_id)

    await db.update_session(
        session_id,
        transcript=transcript,
        xterm_state=xterm_state,
        ended_at=datetime.now(timezone.utc).isoformat(),
    )

    conn = await db.get_db()
    try:
        cursor = await conn.execute(
            "SELECT cell_id, sortie_id FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if row and row["cell_id"]:
            await notify("cell_updated", {"id": row["cell_id"]})
        if row and row["sortie_id"]:
            await notify("sortie_updated", {"id": row["sortie_id"]})
    finally:
        await conn.close()

    return exit_code
