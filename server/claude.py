from __future__ import annotations

from pathlib import Path

import tomllib

from server import config

ORRERY_PORT = 57381
PROMPTS_PATH = Path(__file__).parent.parent / "prompts.toml"


def _load_prompts() -> dict:
    return tomllib.loads(PROMPTS_PATH.read_text())


def write_cell_claude_md(worktree_path: str, cell_id: str) -> None:
    """Append orrery instructions to the worktree's CLAUDE.local.md.

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

    claude_local = Path(worktree_path) / "CLAUDE.local.md"

    existing = claude_local.read_text() if claude_local.exists() else ""
    with claude_local.open("a") as f:
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


def tend_prompt(branch: str, cell_id: str, main_branch: str, has_conflict: bool) -> str:
    """Build a prompt for the tend session.

    `has_conflict` selects between two mutually-exclusive merge sections:
    one tells Claude to perform the merge and resolve conflicts, the other
    tells Claude to leave behind-main alone. The daemon is authoritative
    about which case we're in, so the prompt never asks Claude to figure
    that out itself.
    """
    prompts = _load_prompts()
    author = config.get_author()
    base_url = f"http://localhost:{ORRERY_PORT}"
    section_key = "merge_conflict" if has_conflict else "merge_skip"
    merge_section = (
        prompts["tend"][section_key]["template"].strip().format(main_branch=main_branch)
    )
    return (
        prompts["tend"]["template"]
        .strip()
        .format(
            branch=branch,
            author=author,
            cell_id=cell_id,
            base_url=base_url,
            main_branch=main_branch,
            merge_section=merge_section,
        )
    )
