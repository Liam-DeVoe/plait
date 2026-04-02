from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from server.daemon_config import DAEMON_ALLOWED_TOOLS

ORRERY_PORT = 8000


def orrery_system_prompt(cell_id: str) -> str:
    """Generate the system prompt that tells Claude about Orrery hooks."""
    base = f"http://localhost:{ORRERY_PORT}"
    return (
        f"You are working inside an Orrery cell (id: {cell_id}).\n"
        "Orrery is a development tool that manages your worktree and tracks your work.\n"
        "\n"
        "## When asked to push and/or create a PR:\n"
        "\n"
        "1. Choose a descriptive branch name based on your changes "
        "(e.g. `fix-timeout-handling`, `add-retry-logic`)\n"
        "2. Rename the current branch: `git branch -m <new-name>`\n"
        "3. Notify Orrery about the branch rename:\n"
        f"   curl -s -X POST {base}/hooks/cells/{cell_id}/branch-updated "
        "-H 'Content-Type: application/json' "
        '-d \'{"branch": "<new-name>"}\'\n'
        "4. Push: `git push -u origin <new-name>`\n"
        '5. Create the PR: `gh pr create --title "..." --body "..."`\n'
        "6. Notify Orrery about the PR:\n"
        f"   curl -s -X POST {base}/hooks/cells/{cell_id}/pr-created "
        "-H 'Content-Type: application/json' "
        '-d \'{"pr_url": "<url>", "pr_number": <number>}\'\n'
        "\n"
        "Always notify Orrery after renaming the branch and after creating the PR.\n"
    )


def sortie_system_prompt(
    sortie_id: str,
    exploration_dir: str,
    repo_paths: dict[str, str],
) -> str:
    """Generate the system prompt for a sortie orchestrator session."""
    base = f"http://localhost:{ORRERY_PORT}"
    repo_list = "\n".join(f"  - {rid}: {path}" for rid, path in repo_paths.items())
    return (
        f"You are running a sortie (id: {sortie_id}) in Orrery.\n"
        "Orrery is a development tool that manages worktrees across multiple repos.\n"
        "\n"
        "## Exploration worktrees (READ-ONLY)\n"
        f"You have read-only worktrees of all repos at origin/main under:\n"
        f"  {exploration_dir}\n"
        "\n"
        f"Repos:\n{repo_list}\n"
        "\n"
        "Use these to explore the codebases and understand what changes are needed.\n"
        "Do NOT modify files in these directories.\n"
        "\n"
        "## Creating cells for repos you need to change\n"
        "When you decide a repo needs changes, create a cell:\n"
        f"  curl -s -X POST {base}/hooks/sorties/{sortie_id}/create-cell \\\n"
        "    -H 'Content-Type: application/json' \\\n"
        '    -d \'{"repo": "<repo_id>"}\'\n'
        "\n"
        'This returns JSON: {"cell_id": "...", "worktree_path": "...", '
        '"branch": "..."}.\n'
        "The worktree is a fresh branch from origin/main where you can make changes.\n"
        "\n"
        "## Working in cell worktrees\n"
        "After creating a cell, work in its worktree_path to make changes.\n"
        "When ready to push and create a PR for a cell:\n"
        "1. Choose a descriptive branch name\n"
        "2. Rename: `git branch -m <new-name>`\n"
        f"3. Notify: curl -s -X POST {base}/hooks/cells/<cell_id>/branch-updated "
        "-H 'Content-Type: application/json' "
        '-d \'{"branch": "<new-name>"}\'\n'
        "4. Push: `git push -u origin <new-name>`\n"
        '5. Create PR: `gh pr create --title "..." --body "..."`\n'
        f"6. Notify: curl -s -X POST {base}/hooks/cells/<cell_id>/pr-created "
        "-H 'Content-Type: application/json' "
        '-d \'{"pr_url": "<url>", "pr_number": <number>}\'\n'
        "\n"
        "You can create cells for multiple repos. Work through them one at a time.\n"
        "When you're done with all repos, push your changes and create PRs, "
        "then finish.\n"
    )


async def run_claude_headless(
    prompt: str,
    cwd: str | Path,
    session_id: str | None = None,
    system_prompt: str | None = None,
    on_output: Callable[[str], Awaitable[None]] | None = None,
    allowed_tools: list[str] | None = None,
) -> tuple[bool, str]:
    """Run claude in print mode with a prompt. Returns (success, output).

    If session_id is provided, passes --session-id so the conversation can be
    resumed interactively later.

    If on_output is provided, it is called with the accumulated stdout so far
    each time a new line is read, enabling streaming transcript updates.

    If allowed_tools is provided, uses those directly instead of formatting
    DAEMON_ALLOWED_TOOLS with the cwd.
    """
    if allowed_tools is None:
        worktree = str(Path(cwd).resolve())
        allowed_tools = [t.format(worktree=worktree) for t in DAEMON_ALLOWED_TOOLS]
    args = ["claude", "-p", prompt, "--verbose", "--allowedTools", *allowed_tools]
    if session_id:
        args.extend(["--session-id", session_id])
    if system_prompt:
        args.extend(["--system-prompt", system_prompt])
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
    system_prompt: str | None = None,
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
        prompt,
        cwd=worktree_path,
        session_id=session_id,
        system_prompt=system_prompt,
        on_output=on_output,
    )


async def fix_ci(
    worktree_path: str,
    branch: str,
    ci_output: str,
    session_id: str | None = None,
    system_prompt: str | None = None,
    on_output: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[bool, str]:
    """Use Claude to diagnose and fix a CI failure."""
    prompt = (
        f"The CI for branch {branch} is failing. Here's the CI output:\n\n"
        f"{ci_output}\n\n"
        "Please diagnose the issue and fix it. Commit your fix."
    )
    return await run_claude_headless(
        prompt,
        cwd=worktree_path,
        session_id=session_id,
        system_prompt=system_prompt,
        on_output=on_output,
    )
