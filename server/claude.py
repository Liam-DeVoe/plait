from __future__ import annotations

import shutil
from pathlib import Path

import tomllib

from server import config

ORRERY_PORT = 57381
ORRERY_ROOT = Path(__file__).parent.parent
PROMPTS_PATH = ORRERY_ROOT / "prompts.toml"
CLAUDE_FILES_SRC = ORRERY_ROOT / "claude_files"


def _load_prompts() -> dict:
    return tomllib.loads(PROMPTS_PATH.read_text())


async def write_cell_claude_md(worktree_path: str, cell_id: str, repo_id: str) -> None:
    """Install orrery's per-cell Claude config into the worktree.

    Writes CLAUDE.local.md with hook instructions, and copies orrery's
    bundled skills and agents into worktree/.claude/ so every Claude
    session in the worktree (orrery-spawned or manual) sees them. This
    keeps orrery self-contained — no dependency on ~/.claude.

    Selects a local-flavored template for local-only repos.
    """
    from server import git

    worktree = Path(worktree_path)
    base_url = f"http://localhost:{ORRERY_PORT}"
    prompts = _load_prompts()
    if config.is_local(repo_id):
        main_branch = await git.main_branch(repo_id)
        content = (
            prompts["cell_local_claude_md"]["template"]
            .strip()
            .format(cell_id=cell_id, base_url=base_url, main_branch=main_branch)
        )
    else:
        content = (
            prompts["cell_claude_md"]["template"]
            .strip()
            .format(cell_id=cell_id, base_url=base_url)
        )

    claude_local = worktree / "CLAUDE.local.md"
    existing = claude_local.read_text() if claude_local.exists() else ""
    with claude_local.open("a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"\n{content}\n")

    shutil.copytree(CLAUDE_FILES_SRC, worktree / ".claude", dirs_exist_ok=True)


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


def tend_prompt(
    branch: str,
    cell_id: str,
    main_branch: str,
    has_conflict: bool,
    is_local: bool = False,
) -> str:
    """Build a prompt for the tend session.

    For remote repos, `has_conflict` selects between two mutually-exclusive
    merge sections: one tells Claude to perform the merge and resolve
    conflicts, the other tells Claude to leave behind-main alone. The
    daemon is authoritative about which case we're in.

    For local repos, the only thing the daemon ever spawns a tend for is
    a merge conflict — there's no CI / no PR comments — so the prompt is
    simpler.
    """
    prompts = _load_prompts()
    base_url = f"http://localhost:{ORRERY_PORT}"
    if is_local:
        return (
            prompts["tend_local"]["template"]
            .strip()
            .format(
                branch=branch,
                cell_id=cell_id,
                base_url=base_url,
                main_branch=main_branch,
            )
        )
    author = config.get_author()
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
