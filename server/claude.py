from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path


async def run_claude_headless(
    prompt: str,
    cwd: str | Path,
    session_id: str | None = None,
    on_output: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[bool, str]:
    """Run claude in print mode with a prompt. Returns (success, output).

    If session_id is provided, passes --session-id so the conversation can be
    resumed interactively later.

    If on_output is provided, it is called with the accumulated stdout so far
    each time a new line is read, enabling streaming transcript updates.
    """
    args = ["claude", "-p", prompt]
    if session_id:
        args.extend(["--session-id", session_id])
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Drain stderr in background to prevent deadlock
    async def drain_stderr() -> None:
        assert proc.stderr is not None
        await proc.stderr.read()

    stderr_task = asyncio.create_task(drain_stderr())

    output_parts: list[str] = []
    assert proc.stdout is not None
    async for raw_line in proc.stdout:
        output_parts.append(raw_line.decode())
        if on_output is not None:
            await on_output("".join(output_parts))

    await stderr_task
    await proc.wait()
    output = "".join(output_parts)
    success = proc.returncode == 0
    return success, output


async def resolve_conflicts(
    worktree_path: str,
    branch: str,
    session_id: str | None = None,
    on_output: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[bool, str]:
    """Use Claude to merge origin/main and resolve any conflicts."""
    prompt = (
        f"Merge origin/main into the current branch ({branch}). "
        "Resolve any merge conflicts to the best of your ability, "
        "preserving the intent of both sides. After resolving, "
        "make sure the code compiles/passes basic checks. "
        "Do NOT push — just complete the merge locally."
    )
    return await run_claude_headless(
        prompt, cwd=worktree_path, session_id=session_id, on_output=on_output
    )


async def fix_ci(
    worktree_path: str,
    branch: str,
    ci_output: str,
    session_id: str | None = None,
    on_output: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[bool, str]:
    """Use Claude to diagnose and fix a CI failure."""
    prompt = (
        f"The CI for branch {branch} is failing. Here's the CI output:\n\n"
        f"{ci_output}\n\n"
        "Please diagnose the issue and fix it. Commit your fix."
    )
    return await run_claude_headless(
        prompt, cwd=worktree_path, session_id=session_id, on_output=on_output
    )
