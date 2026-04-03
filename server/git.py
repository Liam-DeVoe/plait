from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from server import config

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


async def fetch_origin(repo_id: str) -> None:
    repo = config.get_repo(repo_id)
    rc, out, err = await run("git", "fetch", "origin", cwd=repo.path)
    if rc != 0:
        raise RuntimeError(f"Failed to fetch origin for {repo_id}: {err}")


async def create_sortie_worktrees(sortie_id: str) -> dict[str, str]:
    """Create read-only worktrees for all repos at origin/main.

    Returns a dict mapping repo_id to worktree path.
    Worktrees are created at worktrees/sortie-{id}/{repo_id}/ using
    detached HEAD (no branch).
    """
    sortie_dir = WORKTREE_ROOT / f"sortie-{sortie_id}"
    sortie_dir.mkdir(parents=True, exist_ok=True)

    async def _create_one(repo_id: str, repo: config.Repo) -> tuple[str, str]:
        await fetch_origin(repo_id)
        wt_dir = sortie_dir / repo_id
        rc, out, err = await run(
            "git",
            "worktree",
            "add",
            "--detach",
            str(wt_dir),
            "origin/main",
            cwd=repo.path,
        )
        if rc != 0:
            raise RuntimeError(f"Failed to create sortie worktree for {repo_id}: {err}")
        return repo_id, str(wt_dir)

    pairs = await asyncio.gather(
        *[_create_one(rid, r) for rid, r in config.get_repos().items()]
    )
    return dict(pairs)


async def remove_sortie_worktrees(sortie_id: str) -> None:
    """Remove all exploration worktrees for a sortie."""
    sortie_dir = WORKTREE_ROOT / f"sortie-{sortie_id}"
    if not sortie_dir.exists():
        return
    for repo_id, repo in config.get_repos().items():
        wt_dir = sortie_dir / repo_id
        if wt_dir.exists():
            await run(
                "git",
                "worktree",
                "remove",
                "--force",
                str(wt_dir),
                cwd=repo.path,
            )
    if sortie_dir.exists():
        sortie_dir.rmdir()


async def create_worktree(repo_id: str, branch: str, cell_id: str) -> str:
    """Create a git worktree for a cell. Returns the worktree path."""
    repo = config.get_repo(repo_id)
    repo_dir = repo.path
    if not repo_dir.exists():
        raise RuntimeError(
            f"Repo path does not exist: {repo_dir}. Clone the repo first."
        )
    worktree_dir = WORKTREE_ROOT / cell_id
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    # Check if branch exists on remote
    rc, out, err = await run(
        "git", "ls-remote", "--heads", "origin", branch, cwd=repo_dir
    )
    if rc == 0 and branch in out:
        # Fetch and create worktree tracking the remote branch
        await run("git", "fetch", "origin", branch, cwd=repo_dir)
        # Check if the local branch already exists (e.g. from a previous checkout)
        rc2, _, _ = await run(
            "git", "rev-parse", "--verify", f"refs/heads/{branch}", cwd=repo_dir
        )
        if rc2 == 0:
            # Local branch exists — use it directly, then reset to remote
            rc, out, err = await run(
                "git",
                "worktree",
                "add",
                str(worktree_dir),
                branch,
                cwd=repo_dir,
            )
            if rc == 0:
                await run(
                    "git",
                    "reset",
                    "--hard",
                    f"origin/{branch}",
                    cwd=str(worktree_dir),
                )
        else:
            # Create new local branch tracking remote
            rc, out, err = await run(
                "git",
                "worktree",
                "add",
                "-b",
                branch,
                str(worktree_dir),
                f"origin/{branch}",
                cwd=repo_dir,
            )
    else:
        # Create new branch from origin/main
        await fetch_origin(repo_id)
        rc, out, err = await run(
            "git",
            "worktree",
            "add",
            "-b",
            branch,
            str(worktree_dir),
            "origin/main",
            cwd=repo_dir,
        )

    if rc != 0:
        raise RuntimeError(f"Failed to create worktree: {err}")

    return str(worktree_dir)


async def remove_worktree(repo_id: str, worktree_path: str) -> None:
    repo = config.get_repo(repo_id)
    rc, out, err = await run(
        "git", "worktree", "remove", "--force", worktree_path, cwd=repo.path
    )
    if rc != 0:
        raise RuntimeError(f"Failed to remove worktree {worktree_path}: {err}")


async def assert_not_detached(worktree_path: str) -> None:
    """Assert the worktree is on a branch, not in detached HEAD state."""
    rc, out, err = await run("git", "symbolic-ref", "HEAD", cwd=worktree_path)
    assert rc == 0, f"Worktree is in detached HEAD state: {worktree_path}"


async def is_behind_main(worktree_path: str, branch: str) -> bool:
    """Check if the remote branch is behind origin/main."""
    rc, out, err = await run(
        "git",
        "rev-list",
        "--count",
        f"origin/{branch}..origin/main",
        cwd=worktree_path,
    )
    if rc != 0:
        return False
    return int(out.strip()) > 0


async def merge_from_main(worktree_path: str) -> tuple[bool, str]:
    """Attempt to merge origin/main into the worktree branch.
    Returns (success, output)."""
    # Fetch latest main
    rc, out, err = await run("git", "fetch", "origin", "main", cwd=worktree_path)
    if rc != 0:
        return False, f"fetch failed: {err}"

    rc, out, err = await run(
        "git", "merge", "origin/main", "--no-edit", cwd=worktree_path
    )
    if rc == 0:
        return True, out

    # Merge had conflicts — abort it so Claude can try
    await run("git", "merge", "--abort", cwd=worktree_path)
    return False, f"conflicts: {err}"


async def push(worktree_path: str, branch: str) -> tuple[bool, str]:
    rc, out, err = await run("git", "push", "origin", branch, cwd=worktree_path)
    if rc == 0:
        return True, out
    return False, err


async def get_pr_info_from_url(pr_url: str) -> dict:
    """Get PR details from a GitHub PR URL using gh CLI.
    Returns dict with keys: repo_id, number, url, branch."""

    # Parse owner/repo from URL
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/\d+", pr_url)
    if not m:
        raise RuntimeError(f"Could not parse repo from URL: {pr_url}")
    upstream = m.group(1)

    # Find which config repo matches this upstream
    repo_id = _upstream_to_repo_id(upstream)
    if repo_id is None:
        raise RuntimeError(
            f"No configured repo matches upstream {upstream!r}. "
            f"Add it to config.toml first."
        )

    rc, out, err = await run(
        "gh",
        "pr",
        "view",
        pr_url,
        "--json",
        "number,url,headRefName",
    )
    if rc != 0:
        raise RuntimeError(f"Failed to fetch PR info from {pr_url}: {err}")
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"Failed to parse PR info from {pr_url}: {out[:200]}")
    data["repo_id"] = repo_id
    data["branch"] = data.pop("headRefName")
    return data


def _upstream_to_repo_id(upstream: str) -> str | None:
    """Find the repo ID whose upstream matches the given owner/repo string."""
    for repo_id, repo in config.get_repos().items():
        if repo.upstream == upstream:
            return repo_id
    return None


async def get_pr_state(repo_id: str, pr_number: int) -> str:
    """Get PR state: 'OPEN', 'MERGED', or 'CLOSED'."""
    upstream = config.get_repo(repo_id).upstream
    rc, out, err = await run(
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--repo",
        upstream,
        "--json",
        "state",
        "--jq",
        ".state",
    )
    if rc != 0:
        return "OPEN"  # default to open on failure to avoid false archival
    return out.strip()


async def get_ci_status(repo_id: str, pr_number: int) -> str:
    """Get CI status for a PR. Returns 'passing', 'failing', 'pending', or 'unknown'."""
    upstream = config.get_repo(repo_id).upstream
    rc, out, err = await run(
        "gh",
        "pr",
        "checks",
        str(pr_number),
        "--repo",
        upstream,
    )
    # gh pr checks exits with 1 when any check is failing — that's not an
    # error, so we still parse the output.  Only bail on truly empty output
    # (e.g. network failure).
    if not out.strip():
        return "unknown"

    if "fail" in out.lower():
        return "failing"
    if "pending" in out.lower() or "queued" in out.lower():
        return "pending"
    if "pass" in out.lower():
        return "passing"
    return "unknown"


async def get_pr_comment_count(repo_id: str, pr_number: int) -> int | None:
    """Get the total number of comments on a PR (issue comments + review comments)."""
    upstream = config.get_repo(repo_id).upstream
    rc, out, err = await run(
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--repo",
        upstream,
        "--json",
        "comments,reviews",
    )
    if rc != 0:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    comments = len(data.get("comments", []))
    reviews = len(data.get("reviews", []))
    return comments + reviews


async def get_pr_reaction_count(repo_id: str, pr_number: int) -> int | None:
    """Count reactions by the configured author on others' PR comments.

    Only counts reactions where the reactor is the orrery author AND the
    comment was written by someone else.  This detects the author
    acknowledging reviewer feedback.
    """
    author = config.get_author()
    if not author:
        return 0
    upstream = config.get_repo(repo_id).upstream
    owner, name = upstream.split("/")
    query = """
    query($owner: String!, $name: String!, $pr: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $pr) {
          comments(first: 100) {
            nodes {
              author { login }
              reactions(first: 10) {
                nodes { user { login } }
              }
            }
          }
          reviews(first: 100) {
            nodes {
              comments(first: 100) {
                nodes {
                  author { login }
                  reactions(first: 10) {
                    nodes { user { login } }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    rc, out, _err = await run(
        "gh",
        "api",
        "graphql",
        "-F",
        f"owner={owner}",
        "-F",
        f"name={name}",
        "-F",
        f"pr={pr_number}",
        "-f",
        f"query={query}",
    )
    if rc != 0:
        return None

    data = json.loads(out)
    pr_data = data["data"]["repository"]["pullRequest"]
    total = 0

    # Issue comments (top-level PR comments)
    for comment in pr_data["comments"]["nodes"]:
        comment_author = (comment.get("author") or {}).get("login")
        if comment_author == author:
            continue
        for reaction in comment["reactions"]["nodes"]:
            if (reaction.get("user") or {}).get("login") == author:
                total += 1

    # Review comments
    for review in pr_data["reviews"]["nodes"]:
        for comment in review["comments"]["nodes"]:
            comment_author = (comment.get("author") or {}).get("login")
            if comment_author == author:
                continue
            for reaction in comment["reactions"]["nodes"]:
                if (reaction.get("user") or {}).get("login") == author:
                    total += 1

    return total


async def get_pr_latest_comment_time(repo_id: str, pr_number: int) -> datetime | None:
    """Get the creation time of the most recent comment or review on a PR."""
    upstream = config.get_repo(repo_id).upstream
    rc, out, err = await run(
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--repo",
        upstream,
        "--json",
        "comments,reviews",
    )
    if rc != 0:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None

    timestamps: list[str] = []
    for item in [*data.get("comments", []), *data.get("reviews", [])]:
        if "createdAt" in item:
            timestamps.append(item["createdAt"])

    if not timestamps:
        return None

    latest = max(timestamps)
    return datetime.fromisoformat(latest.replace("Z", "+00:00"))


@dataclass
class PRData:
    """Batched PR data fetched via pure REST API calls."""

    state: str  # "OPEN", "MERGED", "CLOSED"
    comment_count: int  # issue comments + reviews
    reaction_count: int  # author's reactions on others' comments
    latest_comment_time: datetime | None
    head_sha: str


async def get_pr_data(repo_id: str, pr_number: int) -> PRData | None:
    """Fetch all PR data in one batch using pure REST API (no GraphQL).

    Returns None on any API failure so callers can skip comparisons
    rather than acting on stale data.
    """
    upstream = config.get_repo(repo_id).upstream
    author = config.get_author()

    # 1. PR metadata: state + head SHA (1 REST call)
    rc, out, _ = await run("gh", "api", f"repos/{upstream}/pulls/{pr_number}")
    if rc != 0:
        return None
    try:
        pr = json.loads(out)
    except json.JSONDecodeError:
        return None
    # REST returns "open"/"closed" — merged PRs are "closed" with merged=true
    state = "MERGED" if pr.get("merged") else pr["state"].upper()
    head_sha = pr["head"]["sha"]

    # 2. Review comments with reaction counts (1 REST call)
    rc, out, _ = await run("gh", "api", f"repos/{upstream}/pulls/{pr_number}/comments")
    if rc != 0:
        return None
    try:
        review_comments = json.loads(out)
    except json.JSONDecodeError:
        return None

    # 3. Issue comments with reaction counts (1 REST call)
    rc, out, _ = await run("gh", "api", f"repos/{upstream}/issues/{pr_number}/comments")
    if rc != 0:
        return None
    try:
        issue_comments = json.loads(out)
    except json.JSONDecodeError:
        return None

    # 4. Reviews — for count + timestamps (1 REST call)
    rc, out, _ = await run("gh", "api", f"repos/{upstream}/pulls/{pr_number}/reviews")
    if rc != 0:
        return None
    try:
        reviews = json.loads(out)
    except json.JSONDecodeError:
        return None

    # Derive comment_count: issue comments + reviews (same as current logic)
    comment_count = len(issue_comments) + len(reviews)

    # Derive latest_comment_time from all comment/review timestamps
    timestamps: list[str] = []
    for c in issue_comments:
        if "created_at" in c:
            timestamps.append(c["created_at"])
    for r in reviews:
        if "submitted_at" in r:
            timestamps.append(r["submitted_at"])
    for c in review_comments:
        if "created_at" in c:
            timestamps.append(c["created_at"])
    latest_comment_time = None
    if timestamps:
        latest_ts = max(timestamps)
        latest_comment_time = datetime.fromisoformat(latest_ts.replace("Z", "+00:00"))

    # Derive reaction_count: author's reactions on non-author comments.
    # REST comment data includes reactions.total_count, so we only fetch
    # the full reactors list for comments that actually have reactions.
    reaction_count = 0
    if author:
        for c in review_comments:
            if (c.get("user") or {}).get("login") == author:
                continue
            if (c.get("reactions") or {}).get("total_count", 0) > 0:
                rc2, out2, _ = await run(
                    "gh",
                    "api",
                    f"repos/{upstream}/pulls/comments/{c['id']}/reactions",
                )
                if rc2 == 0:
                    for r in json.loads(out2):
                        if (r.get("user") or {}).get("login") == author:
                            reaction_count += 1
        for c in issue_comments:
            if (c.get("user") or {}).get("login") == author:
                continue
            if (c.get("reactions") or {}).get("total_count", 0) > 0:
                rc2, out2, _ = await run(
                    "gh",
                    "api",
                    f"repos/{upstream}/issues/comments/{c['id']}/reactions",
                )
                if rc2 == 0:
                    for r in json.loads(out2):
                        if (r.get("user") or {}).get("login") == author:
                            reaction_count += 1

    return PRData(
        state=state,
        comment_count=comment_count,
        reaction_count=reaction_count,
        latest_comment_time=latest_comment_time,
        head_sha=head_sha,
    )


async def get_ci_status_rest(repo_id: str, head_sha: str) -> str:
    """Get CI status for a commit SHA using pure REST API (no GraphQL).

    Returns 'passing', 'failing', 'pending', or 'unknown'.
    """
    upstream = config.get_repo(repo_id).upstream
    rc, out, _ = await run(
        "gh", "api", f"repos/{upstream}/commits/{head_sha}/check-runs"
    )
    if rc != 0:
        return "unknown"

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return "unknown"

    runs = data.get("check_runs", [])
    if not runs:
        return "unknown"

    for r in runs:
        if r.get("conclusion") in ("failure", "timed_out"):
            return "failing"
    for r in runs:
        if r.get("status") != "completed":
            return "pending"
    return "passing"


async def get_ci_failure_logs(repo_id: str, branch: str) -> str:
    """Get CI failure logs for the most recent failing run on a branch."""
    upstream = config.get_repo(repo_id).upstream
    rc, out, err = await run(
        "gh",
        "run",
        "list",
        "--branch",
        branch,
        "--status",
        "failure",
        "--limit",
        "1",
        "--json",
        "databaseId",
        "--repo",
        upstream,
    )
    if rc != 0 or not out.strip():
        return "Could not retrieve CI failure information"

    runs = json.loads(out)
    if not runs:
        return "No failed runs found"

    run_id = str(runs[0]["databaseId"])

    rc, out, err = await run(
        "gh",
        "run",
        "view",
        run_id,
        "--log-failed",
        "--repo",
        upstream,
    )
    if rc != 0:
        return f"Could not retrieve logs for run {run_id}: {err}"

    # Truncate if too long
    if len(out) > 10000:
        out = out[:10000] + "\n\n... (truncated)"

    return out


async def has_remote_branch(worktree_path: str, branch: str) -> bool:
    """Check if a branch exists on the remote (assumes fetch has been run)."""
    rc, _, _ = await run(
        "git", "rev-parse", "--verify", f"origin/{branch}", cwd=worktree_path
    )
    return rc == 0


async def find_pr_for_branch(repo_id: str, branch: str) -> dict | None:
    """Check if a PR exists for a branch. Returns {number, url} or None."""
    upstream = config.get_repo(repo_id).upstream
    rc, out, err = await run(
        "gh",
        "pr",
        "list",
        "--head",
        branch,
        "--repo",
        upstream,
        "--json",
        "number,url",
        "--limit",
        "1",
    )
    if rc != 0:
        return None
    prs = json.loads(out)
    if not prs:
        return None
    return {"number": prs[0]["number"], "url": prs[0]["url"]}


async def create_pr(worktree_path: str, repo_id: str, title: str, body: str) -> dict:
    """Create a PR from the current branch. Returns dict with number and url."""
    upstream = config.get_repo(repo_id).upstream
    rc, out, err = await run(
        "gh",
        "pr",
        "create",
        "--title",
        title,
        "--body",
        body,
        "--repo",
        upstream,
        cwd=worktree_path,
    )
    if rc != 0:
        raise RuntimeError(f"Failed to create PR: {err}")

    pr_url = out.strip()
    m = re.search(r"/pull/(\d+)", pr_url)
    number = int(m.group(1)) if m else None

    return {"number": number, "url": pr_url}
