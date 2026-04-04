from __future__ import annotations

from pathlib import Path

import tomllib

from server import config

ORRERY_PORT = 8000
PROMPTS_PATH = Path(__file__).parent.parent / "prompts.toml"


def _load_prompts() -> dict:
    return tomllib.loads(PROMPTS_PATH.read_text())


def write_cell_claude_md(worktree_path: str, cell_id: str) -> None:
    """Append orrery instructions to the worktree's .claude/CLAUDE.md.

    This ensures any Claude session in the worktree — whether spawned by
    orrery or launched manually — knows about orrery hooks.
    """
    base_url = f"http://localhost:{ORRERY_PORT}"
    prompts = _load_prompts()
    content = (
        prompts["cell_claude_md"]["template"]
        .strip()
        .format(cell_id=cell_id, base_url=base_url)
    )

    claude_dir = Path(worktree_path) / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    claude_md = claude_dir / "CLAUDE.md"

    existing = claude_md.read_text() if claude_md.exists() else ""
    with claude_md.open("a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"\n{content}\n")


def orrery_system_prompt(session_id: str) -> str:
    """Generate the session-scoped system prompt (done hook)."""
    base_url = f"http://localhost:{ORRERY_PORT}"
    prompts = _load_prompts()
    return (
        prompts["cell_system"]["template"]
        .strip()
        .format(session_id=session_id, base_url=base_url)
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


def tend_prompt(branch: str, cell_id: str) -> str:
    """Build a prompt for the tend session."""
    prompts = _load_prompts()
    author = config.get_author()
    base_url = f"http://localhost:{ORRERY_PORT}"
    return (
        prompts["tend"]["template"]
        .strip()
        .format(branch=branch, author=author, cell_id=cell_id, base_url=base_url)
    )
