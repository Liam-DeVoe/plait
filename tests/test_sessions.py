"""Tests for initial prompt delivery in server.sessions.

The initial prompt is pasted into the spawned Claude Code PTY only after the
TUI signals readiness by enabling bracketed paste (TUI_READY_MARKER in the
raw output buffer). These tests exercise the readiness gate directly via
_send_initial_input, with the delays patched down so tests stay fast.
"""

from __future__ import annotations

import asyncio

import pytest

import server.sessions as sessions
from server.pty import PtySession
from server.sessions import TUI_READY_MARKER, _send_initial_input, spawn_session

PASTED = b"\x1b[200~hello\x1b[201~"


@pytest.fixture(autouse=True)
def _fast_delays(monkeypatch):
    """Shrink the post-readiness delays so tests don't sleep for real."""
    monkeypatch.setattr(sessions, "TUI_READY_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(sessions, "TUI_READY_SETTLE_DELAY", 0.01)
    monkeypatch.setattr(sessions, "SUBMIT_DELAY", 0.01)


@pytest.fixture
def pty_env(mock_pty):
    """A live fake PtySession wired into mock_pty, with writes recorded."""
    pty_session = PtySession(session_id="s1", master_fd=99, pid=0)
    mock_pty.get.side_effect = lambda sid: pty_session
    writes: list[bytes] = []
    mock_pty.write.side_effect = lambda sid, data: writes.append(data)
    return pty_session, writes


async def test_paste_waits_for_ready_marker(pty_env):
    pty_session, writes = pty_env

    task = asyncio.create_task(_send_initial_input("s1", "hello"))
    # Give the poll loop plenty of iterations: nothing may be written before
    # the readiness marker appears.
    await asyncio.sleep(0.1)
    assert writes == []

    pty_session.output_buffer.extend(b"startup noise" + TUI_READY_MARKER)
    await asyncio.wait_for(task, timeout=5)
    assert writes == [PASTED, b"\r"]


async def test_timeout_fallback_still_pastes(pty_env, monkeypatch):
    monkeypatch.setattr(sessions, "TUI_READY_TIMEOUT", 0.05)
    pty_session, writes = pty_env

    # Marker never appears; the paste must still go through after the timeout.
    await asyncio.wait_for(_send_initial_input("s1", "hello"), timeout=5)
    assert writes == [PASTED, b"\r"]


async def test_session_removed_during_polling(pty_env, mock_pty):
    _, writes = pty_env

    task = asyncio.create_task(_send_initial_input("s1", "hello"))
    await asyncio.sleep(0.05)
    # Session disappears from the manager mid-poll.
    mock_pty.get.side_effect = lambda sid: None
    await asyncio.wait_for(task, timeout=5)
    assert writes == []


async def test_session_died_during_polling(pty_env):
    pty_session, writes = pty_env

    task = asyncio.create_task(_send_initial_input("s1", "hello"))
    await asyncio.sleep(0.05)
    # Process exit closes the master fd (PtyManager._cleanup sets it to -1)
    # while the session object is still registered.
    pty_session.master_fd = -1
    await asyncio.wait_for(task, timeout=5)
    assert writes == []


async def test_spawn_session_delivers_initial_input(pty_env):
    pty_session, writes = pty_env
    pty_session.output_buffer.extend(TUI_READY_MARKER)

    watcher = spawn_session("s1", ["claude"], "/tmp", initial_input="hello")
    try:
        for _ in range(500):
            if len(writes) == 2:
                break
            await asyncio.sleep(0.01)
        assert writes == [PASTED, b"\r"]
    finally:
        watcher.cancel()


async def test_spawn_session_no_initial_input(pty_env):
    _, writes = pty_env

    watcher = spawn_session("s1", ["claude"], "/tmp")
    try:
        await asyncio.sleep(0.1)
        assert writes == []
    finally:
        watcher.cancel()
