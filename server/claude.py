from __future__ import annotations

import shutil
from pathlib import Path

import tomllib

from server import config

PLAIT_PORT = 57381
PLAIT_ROOT = Path(__file__).parent.parent
PROMPTS_PATH = PLAIT_ROOT / "prompts.toml"
CLAUDE_FILES_SRC = PLAIT_ROOT / "claude_files"


def install_claude_files(worktree_path: str | Path) -> None:
    """Copy claude_files/ into <worktree>/.claude/ and render the
    settings template.

    `claude_files/` holds plait's per-worktree Claude config (skills,
    agents, hook scripts, settings.local.json). It's copied wholesale
    into the worktree so every Claude session — plait-spawned or
    user-launched — picks it up automatically and the worktree stays
    self-contained.

    The settings template uses `{worktree_root}` as a placeholder for
    the absolute worktree path; it's substituted in-place after copy
    so hook commands resolve to the local copy of each script.

    PreToolUse hooks declared in settings.local.json fire even under
    `--dangerously-skip-permissions`, making them a hard guardrail.
    Edit `claude_files/settings.local.json` directly to adjust hooks,
    permissions, or anything else in the Claude settings schema.
    """
    worktree = Path(worktree_path).resolve()
    claude_dir = worktree / ".claude"
    shutil.copytree(CLAUDE_FILES_SRC, claude_dir, dirs_exist_ok=True)
    settings = claude_dir / "settings.local.json"
    if settings.exists():
        settings.write_text(
            settings.read_text().replace("{worktree_root}", str(worktree))
        )


def _load_prompts() -> dict:
    return tomllib.loads(PROMPTS_PATH.read_text())


async def write_worktop_claude_md(
    worktree_path: str, worktop_id: str, repo_id: str
) -> None:
    """Install plait's per-worktop Claude config into the worktree.

    Writes CLAUDE.local.md with hook instructions, and copies plait's
    bundled skills and agents into worktree/.claude/ so every Claude
    session in the worktree (plait-spawned or manual) sees them. This
    keeps plait self-contained — no dependency on ~/.claude.

    Selects a local-flavored template for local-only repos.
    """
    from server import git

    worktree = Path(worktree_path)
    base_url = f"http://localhost:{PLAIT_PORT}"
    prompts = _load_prompts()
    if config.is_local(repo_id):
        main_branch = await git.main_branch(repo_id)
        repo_path = str(config.get_repo(repo_id).path)
        content = (
            prompts["worktop_local_claude_md"]["template"]
            .strip()
            .format(
                worktop_id=worktop_id,
                base_url=base_url,
                main_branch=main_branch,
                repo_path=repo_path,
            )
        )
    else:
        content = (
            prompts["worktop_claude_md"]["template"]
            .strip()
            .format(worktop_id=worktop_id, base_url=base_url)
        )

    claude_local = worktree / "CLAUDE.local.md"
    existing = claude_local.read_text() if claude_local.exists() else ""
    with claude_local.open("a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"\n{content}\n")

    install_claude_files(worktree_path)


def plait_system_prompt() -> str:
    """Generate the system prompt for worktop sessions."""
    prompts = _load_prompts()
    return prompts["worktop_system"]["template"].strip()


def slate_system_prompt(
    slate_id: str,
    exploration_dir: str,
    repo_paths: dict[str, str],
) -> str:
    """Generate the system prompt for a slate orchestrator session."""
    base_url = f"http://localhost:{PLAIT_PORT}"
    repo_list = "\n".join(f"  - {rid}: {path}" for rid, path in repo_paths.items())
    prompts = _load_prompts()
    return (
        prompts["slate_system"]["template"]
        .strip()
        .format(
            slate_id=slate_id,
            base_url=base_url,
            exploration_dir=exploration_dir,
            repo_list=repo_list,
        )
    )


def tend_prompt(
    branch: str,
    worktop_id: str,
    session_id: str,
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
    base_url = f"http://localhost:{PLAIT_PORT}"
    if is_local:
        return (
            prompts["tend_local"]["template"]
            .strip()
            .format(
                branch=branch,
                worktop_id=worktop_id,
                session_id=session_id,
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
            worktop_id=worktop_id,
            session_id=session_id,
            base_url=base_url,
            main_branch=main_branch,
            merge_section=merge_section,
        )
    )
