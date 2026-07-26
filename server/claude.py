from __future__ import annotations

import shutil
from pathlib import Path

import tomllib

from server import config

PLAIT_PORT = 57381
PLAIT_ROOT = Path(__file__).parent.parent
PROMPTS_PATH = PLAIT_ROOT / "prompts.toml"
CLAUDE_FILES_SRC = PLAIT_ROOT / "claude_files"
METR_CLAUDE_FILES_SRC = PLAIT_ROOT / "claude_files_metr"


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


def install_metr_claude_files(
    worktree_path: str | Path, worktop_id: str, repo_id: str
) -> None:
    """Install the /metr-repopulate skill into a metr repo's worktop.

    Worktrees of metr repos are created with the study content stripped
    (see server/metr.py); this skill is how a worktree opts back in. It
    lives in `claude_files_metr/` — separate from `claude_files/` so it
    only ever reaches worktrees of metr-flagged repos — and is rendered
    with the worktop id, plait's base URL, and the canonical clone path
    so the skill can copy the study files back and call the
    tends-enabled hook without any discovery work.
    """
    worktree = Path(worktree_path).resolve()
    claude_dir = worktree / ".claude"
    shutil.copytree(METR_CLAUDE_FILES_SRC, claude_dir, dirs_exist_ok=True)
    skill = claude_dir / "skills" / "metr-repopulate" / "SKILL.md"
    text = skill.read_text()
    substitutions = {
        "{worktop_id}": worktop_id,
        "{base_url}": f"http://localhost:{PLAIT_PORT}",
        "{repo_path}": str(config.get_repo(repo_id).path),
    }
    for placeholder, value in substitutions.items():
        text = text.replace(placeholder, value)
    skill.write_text(text)


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

    Also copies the repo's local-only files from the canonical clone —
    untracked `.claude/` files plus any configured `copy_globs` —
    *before* the plait overlay so plait's guardrails win any path
    collision.

    Selects a local-flavored template for local-only repos.
    """
    from server import git

    await git.copy_local_files(repo_id, worktree_path)

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
    if config.get_repo(repo_id).metr:
        install_metr_claude_files(worktree_path, worktop_id, repo_id)


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


def review_prompt(pr_url: str, pr_number: int, main_branch: str, main_ref: str) -> str:
    """Build the initial prompt for a 'Review locally' session.

    `main_branch` is the bare branch name (e.g. `master`); `main_ref` is the
    canonical remote-tracking ref to diff against (e.g. `origin/master`). In a
    review worktree the local `main_branch` ref is often stale, so the prompt
    diffs against `main_ref` instead.
    """
    prompts = _load_prompts()
    return (
        prompts["review"]["template"]
        .strip()
        .format(
            pr_url=pr_url,
            pr_number=pr_number,
            main_branch=main_branch,
            main_ref=main_ref,
        )
    )


def tend_prompt(
    branch: str,
    worktop_id: str,
    session_id: str,
    main_branch: str,
    has_conflict: bool,
    is_local: bool = False,
    upstream_remote: str | None = None,
) -> str:
    """Build a prompt for the tend session.

    For remote repos, `has_conflict` selects between two mutually-exclusive
    merge sections: one tells Claude to perform the merge and resolve
    conflicts, the other tells Claude to leave behind-main alone. The
    daemon is authoritative about which case we're in.

    For local repos, the only thing the daemon ever spawns a tend for is
    a merge conflict — there's no CI / no PR comments — so the prompt is
    simpler.

    `upstream_remote` is the local git remote name that tracks the
    canonical repo (usually `origin`, but `upstream` in fork-and-PR
    setups). Required for non-local repos so the merge instructions point
    at the right ref; omitted for local repos.
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
    assert upstream_remote is not None
    author = config.get_author()
    section_key = "merge_conflict" if has_conflict else "merge_skip"
    merge_section = (
        prompts["tend"][section_key]["template"]
        .strip()
        .format(main_branch=main_branch, upstream_remote=upstream_remote)
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
