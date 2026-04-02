from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import tomllib

from server.daemon_config import DAEMON_ALLOWED_TOOLS

ORRERY_PORT = 8000
PROMPTS_PATH = Path(__file__).parent.parent / "prompts.toml"


def _load_prompts() -> dict:
    return tomllib.loads(PROMPTS_PATH.read_text())


def orrery_system_prompt(cell_id: str) -> str:
    """Generate the system prompt that tells Claude about Orrery hooks."""
    base_url = f"http://localhost:{ORRERY_PORT}"
    prompts = _load_prompts()
    return (
        prompts["cell_system"]["template"]
        .strip()
        .format(cell_id=cell_id, base_url=base_url)
    )


def sortie_system_prompt(
    sortie_id: str,
    exploration_dir: str,
    repo_paths: dict[str, str],
) -> str:
    """Generate the system prompt for a sortie orchestrator session."""
    base_url = f"http://localhost:{ORRERY_PORT}"
    repo_list = "\n".join(f"  - {rid}: {path}" for rid, path in repo_paths.items())
    prompts = _load_prompts()
    return (
        prompts["sortie_system"]["template"]
        .strip()
        .format(
            sortie_id=sortie_id,
            base_url=base_url,
            exploration_dir=exploration_dir,
            repo_list=repo_list,
        )
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


def tend_prompt(branch: str) -> str:
    """Build a prompt for the tend session."""
    prompts = _load_prompts()
    return prompts["tend"]["template"].strip().format(branch=branch)
