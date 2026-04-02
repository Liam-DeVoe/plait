"""Shared session spawning and lifecycle management.

Both the API (user sessions) and the daemon (headless sessions) use
spawn_session() to create PTY-backed Claude processes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from server import db
from server.pty import pty_manager


def spawn_session(
    session_id: str,
    cwd: str,
    *,
    prompt: str = "",
    system_prompt: str | None = None,
    resume: bool = False,
    print_mode: bool = False,
    allowed_tools: list[str] | None = None,
) -> asyncio.Task[int | None]:
    """Spawn a PTY session and start watching it.

    Returns the watcher task. Await it to block until the session ends
    and get the exit code (daemon use). Discard it for fire-and-forget
    (interactive user sessions).

    Modes:
      - resume=True: ``claude --resume <session_id>``
      - print_mode=True: ``claude -p <prompt> --session-id <session_id>``
        (headless, for daemon use)
      - default: ``claude --session-id <session_id>`` (interactive)
    """
    if resume:
        cmd = ["claude", "--verbose", "--resume", session_id]
    elif print_mode:
        cmd = [
            "claude",
            "-p",
            prompt,
            "--verbose",
            "--session-id",
            session_id,
        ]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        if allowed_tools:
            cmd.extend(["--allowedTools", *allowed_tools])
    else:
        cmd = ["claude", "--verbose", "--session-id", session_id]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

    pty_manager.spawn(session_id, cwd=cwd, cmd=cmd)

    # For interactive sessions, send prompt as initial input after startup
    if not print_mode and not resume and prompt.strip():

        async def _send_initial_prompt():
            await asyncio.sleep(1.0)
            pty_manager.write(session_id, (prompt + "\n").encode())

        asyncio.create_task(_send_initial_prompt())

    return asyncio.create_task(_watch_pty(session_id))


async def _watch_pty(session_id: str) -> int | None:
    """Watch a PTY process: flush transcript periodically, finalize on exit.

    Returns the process exit code (0 = success).
    """
    from server.daemon import notify

    flush_interval = 2.0
    while pty_manager.is_alive(session_id):
        transcript = pty_manager.get_transcript(session_id)
        if transcript:
            await db.update_session(session_id, transcript=transcript)
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
