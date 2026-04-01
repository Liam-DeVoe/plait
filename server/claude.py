from __future__ import annotations

import asyncio
from pathlib import Path


async def run_claude_headless(
    prompt: str,
    cwd: str | Path,
) -> tuple[bool, str]:
    """Run claude in print mode with a prompt. Returns (success, output)."""
    proc = await asyncio.create_subprocess_exec(
        "claude",
        "-p",
        prompt,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode()
    success = proc.returncode == 0
    return success, output


async def resolve_conflicts(worktree_path: str, branch: str) -> tuple[bool, str]:
    """Use Claude to rebase onto main and resolve any conflicts."""
    prompt = (
        f"Rebase the current branch ({branch}) onto origin/main. "
        "Resolve any merge conflicts to the best of your ability, "
        "preserving the intent of both sides. After resolving, "
        "make sure the code compiles/passes basic checks. "
        "Do NOT push — just complete the rebase locally."
    )
    return await run_claude_headless(prompt, cwd=worktree_path)


async def fix_ci(worktree_path: str, branch: str, ci_output: str) -> tuple[bool, str]:
    """Use Claude to diagnose and fix a CI failure."""
    prompt = (
        f"The CI for branch {branch} is failing. Here's the CI output:\n\n"
        f"{ci_output}\n\n"
        "Please diagnose the issue and fix it. Commit your fix."
    )
    return await run_claude_headless(prompt, cwd=worktree_path)
