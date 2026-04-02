from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import re
import signal
import struct
import subprocess
import termios
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PtySession:
    session_id: str
    master_fd: int
    pid: int
    listeners: list[Callable[[bytes], None]] = field(default_factory=list)
    output_buffer: bytearray = field(default_factory=bytearray)


class PtyManager:
    def __init__(self) -> None:
        self._sessions: dict[str, PtySession] = {}

    def spawn(
        self,
        session_id: str,
        cwd: str,
        cmd: list[str] | None = None,
        rows: int = 24,
        cols: int = 80,
    ) -> PtySession:
        if cmd is None:
            cmd = ["claude"]

        master_fd, slave_fd = os.openpty()

        # Set terminal size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        proc = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            preexec_fn=os.setsid,
        )
        os.close(slave_fd)

        # Make master_fd non-blocking
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        pty_session = PtySession(
            session_id=session_id,
            master_fd=master_fd,
            pid=proc.pid,
        )
        self._sessions[session_id] = pty_session

        loop = asyncio.get_event_loop()
        loop.add_reader(master_fd, self._on_readable, session_id)

        return pty_session

    def _on_readable(self, session_id: str) -> None:
        pty_session = self._sessions.get(session_id)
        if pty_session is None:
            return

        try:
            data = os.read(pty_session.master_fd, 65536)
        except OSError:
            # EIO means the child process has closed the PTY
            self._cleanup(session_id)
            return

        if not data:
            self._cleanup(session_id)
            return

        pty_session.output_buffer.extend(data)
        for listener in pty_session.listeners:
            try:
                listener(data)
            except Exception:
                logger.exception("PTY listener error")

    def _cleanup(self, session_id: str) -> None:
        pty_session = self._sessions.get(session_id)
        if pty_session is None:
            return

        loop = asyncio.get_event_loop()
        try:
            loop.remove_reader(pty_session.master_fd)
        except Exception:
            pass
        try:
            os.close(pty_session.master_fd)
        except OSError:
            pass

    def write(self, session_id: str, data: bytes) -> None:
        pty_session = self._sessions.get(session_id)
        if pty_session is None:
            return
        try:
            os.write(pty_session.master_fd, data)
        except OSError:
            logger.exception(f"Failed to write to PTY {session_id}")

    def resize(self, session_id: str, rows: int, cols: int) -> None:
        pty_session = self._sessions.get(session_id)
        if pty_session is None:
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(pty_session.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            logger.exception(f"Failed to resize PTY {session_id}")

    async def terminate(self, session_id: str) -> None:
        pty_session = self._sessions.get(session_id)
        if pty_session is None:
            return

        try:
            pgid = os.getpgid(pty_session.pid)
        except (OSError, ProcessLookupError):
            pgid = None

        # SIGHUP first, then escalate to SIGTERM/SIGKILL
        for sig in [signal.SIGHUP, signal.SIGTERM, signal.SIGKILL]:
            if pgid is not None:
                try:
                    os.killpg(pgid, sig)
                except (OSError, ProcessLookupError):
                    pass

            for _ in range(10):
                try:
                    pid, _ = os.waitpid(pty_session.pid, os.WNOHANG)
                    if pid != 0:
                        self._cleanup(session_id)
                        self._sessions.pop(session_id, None)
                        return
                except ChildProcessError:
                    self._cleanup(session_id)
                    self._sessions.pop(session_id, None)
                    return
                await asyncio.sleep(0.1)

        # Process refused even SIGKILL (shouldn't happen); clean up anyway
        self._cleanup(session_id)
        self._sessions.pop(session_id, None)

    def is_alive(self, session_id: str) -> bool:
        pty_session = self._sessions.get(session_id)
        if pty_session is None:
            return False
        try:
            pid, _ = os.waitpid(pty_session.pid, os.WNOHANG)
            return pid == 0
        except ChildProcessError:
            return False

    def get_raw_output(self, session_id: str) -> bytes:
        pty_session = self._sessions.get(session_id)
        if pty_session is None:
            return b""
        return bytes(pty_session.output_buffer)

    def get_transcript(self, session_id: str) -> str:
        pty_session = self._sessions.get(session_id)
        if pty_session is None:
            return ""
        raw = pty_session.output_buffer.decode("utf-8", errors="replace")
        return strip_ansi(raw)

    def get(self, session_id: str) -> PtySession | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        self._cleanup(session_id)
        self._sessions.pop(session_id, None)


# Regex to strip ANSI escape sequences
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][A-Z0-9]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# Global singleton
pty_manager = PtyManager()
