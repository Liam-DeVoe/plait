from __future__ import annotations

import asyncio
from pathlib import Path

WORKTREE_ROOT = Path(__file__).parent.parent / "worktrees"


async def run(*args: str, cwd: str | Path | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()


def repo_path(repo: str) -> Path:
    """Path to the main clone of a repo. Expects repos to be siblings of
    the coordination directory, e.g. ../hegel-rust for hegeldev/hegel-rust."""
    name = repo.split("/")[-1]
    return Path(__file__).parent.parent.parent / name


async def ensure_repo_cloned(repo: str) -> Path:
    path = repo_path(repo)
    if path.exists():
        return path
    url = f"https://github.com/{repo}.git"
    rc, out, err = await run("git", "clone", url, str(path))
    if rc != 0:
        raise RuntimeError(f"Failed to clone {repo}: {err}")
    return path


async def fetch_origin(repo: str) -> None:
    path = repo_path(repo)
    rc, out, err = await run("git", "fetch", "origin", cwd=path)
    if rc != 0:
        raise RuntimeError(f"Failed to fetch origin for {repo}: {err}")


async def create_worktree(repo: str, branch: str, cell_id: str) -> str:
    """Create a git worktree for a cell. Returns the worktree path."""
    main_repo = await ensure_repo_cloned(repo)
    worktree_dir = WORKTREE_ROOT / cell_id
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    # Check if branch exists on remote
    rc, out, err = await run(
        "git", "ls-remote", "--heads", "origin", branch, cwd=main_repo
    )
    if rc == 0 and branch in out:
        # Fetch and create worktree from remote branch
        await run("git", "fetch", "origin", branch, cwd=main_repo)
        rc, out, err = await run(
            "git",
            "worktree",
            "add",
            str(worktree_dir),
            f"origin/{branch}",
            cwd=main_repo,
        )
        if rc != 0:
            # Try creating from the tracking branch
            rc, out, err = await run(
                "git",
                "worktree",
                "add",
                "-b",
                branch,
                str(worktree_dir),
                f"origin/{branch}",
                cwd=main_repo,
            )
    else:
        # Create new branch from origin/main
        await fetch_origin(repo)
        rc, out, err = await run(
            "git",
            "worktree",
            "add",
            "-b",
            branch,
            str(worktree_dir),
            "origin/main",
            cwd=main_repo,
        )

    if rc != 0:
        raise RuntimeError(f"Failed to create worktree: {err}")

    return str(worktree_dir)


async def remove_worktree(repo: str, worktree_path: str) -> None:
    main_repo = repo_path(repo)
    await run("git", "worktree", "remove", "--force", worktree_path, cwd=main_repo)


async def is_behind_main(worktree_path: str) -> bool:
    """Check if the worktree branch is behind origin/main."""
    rc, out, err = await run(
        "git", "rev-list", "--count", "HEAD..origin/main", cwd=worktree_path
    )
    if rc != 0:
        return False
    return int(out.strip()) > 0


async def rebase_onto_main(worktree_path: str) -> tuple[bool, str]:
    """Attempt to rebase the worktree branch onto origin/main.
    Returns (success, output)."""
    # Fetch latest main
    rc, out, err = await run("git", "fetch", "origin", "main", cwd=worktree_path)
    if rc != 0:
        return False, f"fetch failed: {err}"

    rc, out, err = await run("git", "rebase", "origin/main", cwd=worktree_path)
    if rc == 0:
        return True, out

    # Rebase had conflicts — abort it so Claude can try
    await run("git", "rebase", "--abort", cwd=worktree_path)
    return False, f"conflicts: {err}"


async def force_push(worktree_path: str, branch: str) -> tuple[bool, str]:
    rc, out, err = await run(
        "git", "push", "--force-with-lease", "origin", branch, cwd=worktree_path
    )
    if rc == 0:
        return True, out
    return False, err


async def get_pr_info(repo: str, branch: str) -> dict | None:
    """Get PR info for a branch using gh CLI."""
    rc, out, err = await run(
        "gh",
        "pr",
        "view",
        branch,
        "--repo",
        repo,
        "--json",
        "number,url,state,statusCheckRollup",
    )
    if rc != 0:
        return None
    import json

    return json.loads(out)


async def get_ci_status(repo: str, pr_number: int) -> str:
    """Get CI status for a PR. Returns 'passing', 'failing', 'pending', or 'unknown'."""
    rc, out, err = await run(
        "gh",
        "pr",
        "checks",
        str(pr_number),
        "--repo",
        repo,
    )
    if rc != 0:
        return "unknown"

    if "fail" in out.lower():
        return "failing"
    if "pending" in out.lower() or "queued" in out.lower():
        return "pending"
    if "pass" in out.lower():
        return "passing"
    return "unknown"
