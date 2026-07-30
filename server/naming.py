"""Automatic display-naming of worktops.

Worktops are created with an auto-generated branch and no display name.
The daemon calls `maybe_name_worktop` for every unnamed worktop each
tick: it gathers cheap signal (commits vs main, session transcripts),
asks a small model for a short title, and stores it in `worktops.name`.

The model call is headless (`claude -p`) from a neutral temp cwd — no
PTY, no session row, and no repo-level .claude config is picked up.
Failures are non-fatal: an unnamed worktop is simply retried on the
next tick.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from server import db, git
from server.models import SessionRole, Worktop

logger = logging.getLogger(__name__)

MODEL = "haiku"
CLAUDE_TIMEOUT = 120  # seconds
# Per-session transcript slices fed to the naming model. The head holds
# the task prompt (diluted by TUI chrome, hence the generous size); the
# tail holds Claude's final summary of what was actually done — often
# the best description of the work.
TRANSCRIPT_HEAD_CHARS = 10_000
TRANSCRIPT_TAIL_CHARS = 10_000
# Reject model output longer than this as garbage.
NAME_MAX_CHARS = 80

PROMPT = """\
You are naming a unit of development work for display in a dashboard.
Below is activity from a git worktree: commits (if any) and the start of
each Claude session transcript (if any).

Respond with ONLY the title — no quotes, no trailing punctuation, no
explanation.

Titles are terse noun phrases, 2-5 words:
- telegraphic: drop filler verbs ("Implement", "Rewrite") and articles
- abbreviate where natural: "auth", "dev", "docs", "+" for "and"
- never include the repo/project name — it's shown next to the title
- sentence case, but keep code identifiers verbatim (e.g. one_of)

Examples of good titles:
- Auto-name worktops
- METR auth + session setup
- Add anchor support to docs
- one_of swarm testing
- Strategy inversion
- gtest integration + conformance tests

{signal}
"""


async def gather_signal(worktop: Worktop) -> str | None:
    """Collect naming signal for a worktop, or None if there is none yet.

    Signal sources:
    - commits ahead of main (skipped when the worktree is gone, e.g.
      archived worktops during backfill)
    - the head of each non-empty session transcript
    """
    parts: list[str] = []

    worktree = Path(worktop.worktree_path) if worktop.worktree_path else None
    if worktree is not None and worktree.is_dir():
        try:
            main = await git.main_ref(worktop.repo)
            _, log_out, _ = await git.run(
                "git", "log", "--oneline", f"{main}..HEAD", cwd=worktree
            )
            if log_out.strip():
                _, stat_out, _ = await git.run(
                    "git", "diff", "--stat", f"{main}...HEAD", cwd=worktree
                )
                parts.append(
                    f"## Commits\n{log_out.strip()}\n\n"
                    f"## Diffstat\n{stat_out.strip()}"
                )
        except Exception:
            logger.exception(f"Failed to gather git signal for worktop {worktop.id}")

    # User sessions only: tend transcripts (role=daemon) are CI/merge
    # noise that drowns out the actual task. Slate-spawned task sessions
    # are role=user, so their task briefs are kept.
    sessions = [
        s for s in await db.list_sessions(worktop.id) if s.role is SessionRole.user
    ]
    for i, session in enumerate(sessions):
        transcript = session.transcript.strip()
        if not transcript:
            continue
        if len(transcript) <= TRANSCRIPT_HEAD_CHARS + TRANSCRIPT_TAIL_CHARS:
            parts.append(f"## Session {i + 1} transcript\n{transcript}")
        else:
            parts.append(
                f"## Session {i + 1} transcript (middle elided)\n"
                f"{transcript[:TRANSCRIPT_HEAD_CHARS]}\n"
                f"[...]\n"
                f"{transcript[-TRANSCRIPT_TAIL_CHARS:]}"
            )

    if not parts:
        return None
    return "\n\n".join(parts)


async def _run_claude(prompt: str) -> str | None:
    """Run `claude -p` headlessly and return its stdout, or None on failure.

    Runs from a fresh temp dir so no repo-level .claude config (including
    METR study hooks) is picked up.
    """
    with tempfile.TemporaryDirectory(prefix="plait-naming-") as cwd:
        proc = await asyncio.create_subprocess_exec(
            "claude",
            "-p",
            "--model",
            MODEL,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode()), timeout=CLAUDE_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("Naming claude call timed out")
            return None
    if proc.returncode != 0:
        logger.warning(f"Naming claude call failed: {stderr.decode()[:500]}")
        return None
    return stdout.decode()


def _sanitize(raw: str) -> str | None:
    """Reduce model output to a usable display name, or None if garbage."""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return None
    name = lines[0].strip("\"'").rstrip(".")
    if not name or len(name) > NAME_MAX_CHARS:
        return None
    return name


async def maybe_name_worktop(worktop: Worktop) -> str | None:
    """Name an unnamed worktop if there's enough signal.

    Returns the new name if one was set, else None. The caller is
    responsible for broadcasting `worktop_updated`.
    """
    if worktop.name is not None:
        return None
    signal = await gather_signal(worktop)
    if signal is None:
        return None

    raw = await _run_claude(PROMPT.format(signal=signal))
    if raw is None:
        return None
    name = _sanitize(raw)
    if name is None:
        logger.warning(f"Naming model returned unusable output: {raw[:200]!r}")
        return None

    await db.update_worktop(worktop.id, name=name)
    worktop.name = name
    logger.info(f"Named worktop {worktop.id}: {name!r}")
    return name
