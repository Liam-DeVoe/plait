from __future__ import annotations

import asyncio

import pytest

from server.pty import PtyManager, strip_ansi


@pytest.fixture
def pty_mgr():
    mgr = PtyManager()
    yield mgr
    # Clean up any remaining sessions
    for sid in list(mgr._sessions):
        mgr._cleanup(sid)
        mgr._sessions.pop(sid, None)


async def test_spawn_and_write(pty_mgr, tmp_path):
    """Spawning a PTY with cat and writing to it should produce output."""
    session = pty_mgr.spawn("test-1", cwd=str(tmp_path), cmd=["cat"])
    assert session.pid > 0
    assert pty_mgr.is_alive("test-1")

    pty_mgr.write("test-1", b"hello\n")
    # Give cat time to echo back
    await asyncio.sleep(0.3)

    transcript = pty_mgr.get_transcript("test-1")
    assert "hello" in transcript

    await pty_mgr.terminate("test-1")
    assert not pty_mgr.is_alive("test-1")


async def test_resize(pty_mgr, tmp_path):
    """Resizing should not raise."""
    pty_mgr.spawn("test-resize", cwd=str(tmp_path), cmd=["cat"])
    pty_mgr.resize("test-resize", 50, 120)
    await pty_mgr.terminate("test-resize")


async def test_terminate_already_exited(pty_mgr, tmp_path):
    """Terminating a session whose process already exited should not error."""
    pty_mgr.spawn("test-exit", cwd=str(tmp_path), cmd=["true"])
    # 'true' exits immediately
    await asyncio.sleep(0.3)
    await pty_mgr.terminate("test-exit")
    assert pty_mgr.get("test-exit") is None


async def test_listeners_receive_output(pty_mgr, tmp_path):
    """Registered listeners should receive PTY output."""
    pty_mgr.spawn("test-listen", cwd=str(tmp_path), cmd=["cat"])

    received: list[bytes] = []
    pty_mgr.get("test-listen").listeners.append(lambda data: received.append(data))

    pty_mgr.write("test-listen", b"ping\n")
    await asyncio.sleep(0.3)

    combined = b"".join(received)
    assert b"ping" in combined

    await pty_mgr.terminate("test-listen")


async def test_get_nonexistent(pty_mgr):
    assert pty_mgr.get("nope") is None
    assert not pty_mgr.is_alive("nope")
    assert pty_mgr.get_transcript("nope") == ""


def test_strip_ansi():
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"
    assert strip_ansi("plain text") == "plain text"


async def test_write_to_nonexistent(pty_mgr):
    """Writing to a nonexistent session should not raise."""
    pty_mgr.write("nope", b"data")


async def test_resize_nonexistent(pty_mgr):
    """Resizing a nonexistent session should not raise."""
    pty_mgr.resize("nope", 24, 80)


async def test_terminate_nonexistent(pty_mgr):
    """Terminating a nonexistent session should not raise."""
    await pty_mgr.terminate("nope")
