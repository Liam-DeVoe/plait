"""Shared session spawning and lifecycle management.

Command factories (*_cmd) define how each session type is invoked.
spawn_session() handles the PTY mechanics common to all types.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from server import claude, config, db, git
from server.models import Worktop
from server.pty import pty_manager

logger = logging.getLogger(__name__)

# --- Command factories ---
# Each returns (cmd, cwd) for a specific session type.


async def tend_cmd(
    session_id: str, worktop: Worktop, has_conflict: bool
) -> tuple[list[str], str, str]:
    """Interactive daemon session to fix merge conflicts / CI failures.

    `has_conflict` tells the prompt whether to instruct Claude to merge
    from main (and resolve conflicts) or to leave the branch alone.

    Returns (cmd, cwd, prompt). The prompt should be passed as initial_input
    to spawn_session rather than baked into the command.
    """
    mb = await git.main_branch(worktop.repo)
    is_local = config.is_local(worktop.repo)
    upstream_remote = None if is_local else await git.upstream_remote(worktop.repo)
    prompt = claude.tend_prompt(
        worktop.branch,
        worktop.id,
        session_id,
        mb,
        has_conflict,
        is_local=is_local,
        upstream_remote=upstream_remote,
    )
    return (
        [
            "claude",
            "--verbose",
            "--dangerously-skip-permissions",
            "--session-id",
            session_id,
            "--system-prompt",
            claude.plait_system_prompt(),
        ],
        worktop.worktree_path,
        prompt,
    )


def user_worktop_cmd(session_id: str, worktop: Worktop) -> tuple[list[str], str]:
    """Interactive user session in a worktop worktree."""
    return [
        "claude",
        "--verbose",
        "--dangerously-skip-permissions",
        "--session-id",
        session_id,
        "--system-prompt",
        claude.plait_system_prompt(),
    ], worktop.worktree_path


def user_slate_cmd(
    session_id: str,
    slate_id: str,
    exploration_dir: str,
    repo_worktrees: dict[str, str],
) -> tuple[list[str], str]:
    """Interactive user session to orchestrate a slate."""
    system_prompt = claude.slate_system_prompt(
        slate_id, exploration_dir, repo_worktrees
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


def resume_cmd(session_id: str, cwd: str, system_prompt: str) -> tuple[list[str], str]:
    """Resume a previously ended session.

    `claude --resume` does not preserve the original `--system-prompt`, so
    callers must re-pass it to keep the session's role-specific prompt
    (worktop hooks, slate orchestration, etc.) in effect after resume.
    """
    return [
        "claude",
        "--verbose",
        "--dangerously-skip-permissions",
        "--resume",
        session_id,
        "--system-prompt",
        system_prompt,
    ], cwd


def fork_cmd(
    new_session_id: str,
    source_session_id: str,
    cwd: str,
    system_prompt: str,
) -> tuple[list[str], str]:
    """Fork an existing session into a new, independent one.

    Combines `--resume <source>` (load the source's conversation) with
    `--fork-session` (write a new transcript instead of mutating the source's)
    and `--session-id <new>` (pin the new id to one plait pre-allocated).

    The source session is never touched: its on-disk jsonl is read-only here,
    and if its PTY is alive it keeps appending to its own file independently.
    See claude code's `--fork-session` flag for the underlying mechanism.

    As with `resume_cmd`, `--system-prompt` must be re-passed — `--resume`
    does not preserve the original.
    """
    return [
        "claude",
        "--verbose",
        "--dangerously-skip-permissions",
        "--resume",
        source_session_id,
        "--fork-session",
        "--session-id",
        new_session_id,
        "--system-prompt",
        system_prompt,
    ], cwd


# --- Spawn mechanics ---

# Claude Code's TUI writes DECSET 2004 (enable bracketed paste) to the
# terminal when its input handler mounts. Input pasted before that point is
# parsed as stray escape-key presses and silently dropped, so we use the
# marker as a readiness gate before delivering the initial prompt.
TUI_READY_MARKER = b"\x1b[?2004h"
TUI_READY_TIMEOUT = 15.0
TUI_READY_POLL_INTERVAL = 0.1
# Marker appears when the input handler mounts, not when it's fully settled;
# give the TUI a beat before pasting.
TUI_READY_SETTLE_DELAY = 0.5
# Claude needs time to process the paste end marker before accepting Enter.
SUBMIT_DELAY = 1.0


async def _wait_for_tui_ready(session_id: str) -> bool:
    """Poll the session's raw PTY output until the TUI is ready for input.

    Returns True once TUI_READY_MARKER appears, or after TUI_READY_TIMEOUT
    as a fallback — the marker is a heuristic based on an implementation
    detail of Claude Code's TUI, and proceeding after the timeout preserves
    the old fixed-delay behavior as the worst case.

    Returns False if the session died or was removed while waiting.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + TUI_READY_TIMEOUT
    while True:
        pty_session = pty_manager.get(session_id)
        if pty_session is None or pty_session.master_fd == -1:
            return False
        if TUI_READY_MARKER in pty_session.output_buffer:
            return True
        if loop.time() >= deadline:
            logger.warning(
                f"Session {session_id}: TUI readiness marker not seen after "
                f"{TUI_READY_TIMEOUT}s, pasting initial input anyway"
            )
            return True
        await asyncio.sleep(TUI_READY_POLL_INTERVAL)


async def _send_initial_input(session_id: str, initial_input: str) -> None:
    """Paste the initial prompt into the session's PTY once the TUI is ready."""
    if not await _wait_for_tui_ready(session_id):
        return
    await asyncio.sleep(TUI_READY_SETTLE_DELAY)
    # Wrap in bracketed paste markers so the terminal treats multi-line
    # text as a single paste.
    paste_start = "\x1b[200~"
    paste_end = "\x1b[201~"
    pty_manager.write(session_id, f"{paste_start}{initial_input}{paste_end}".encode())
    # Delay then Enter to submit the pasted text.
    await asyncio.sleep(SUBMIT_DELAY)
    pty_manager.write(session_id, b"\r")


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
        asyncio.create_task(_send_initial_input(session_id, initial_input))

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
    cursor = await conn.execute(
        "SELECT worktop_id, slate_id FROM sessions WHERE id = ?", (session_id,)
    )
    row = await cursor.fetchone()
    if row and row["worktop_id"]:
        await notify("worktop_updated", {"id": row["worktop_id"]})
    if row and row["slate_id"]:
        await notify("slate_updated", {"id": row["slate_id"]})

    return exit_code
