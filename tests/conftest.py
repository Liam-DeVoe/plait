from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- In-memory SQLite DB ---


@pytest.fixture(autouse=True)
def _use_memory_db(tmp_path):
    """Override DB_PATH to use a temporary file DB for every test.
    We use a temp file rather than :memory: because db.py opens a new
    connection per call, and :memory: would give each call a different DB."""
    import server.db as db_module

    db_file = tmp_path / "test.db"
    original = db_module.DB_PATH
    db_module.DB_PATH = db_file
    yield
    db_module.DB_PATH = original


@pytest.fixture
async def init_db():
    """Initialize the DB schema. Use this in tests that need the database."""
    from server.db import init_db

    await init_db()


# --- Temporary git environment ---


@dataclass
class GitEnv:
    """A temporary git environment with a bare remote and a local clone."""

    remote: Path  # bare repo acting as "origin"
    clone: Path  # local clone of the remote
    repo_id: str  # config key like "testrepo"

    def run_git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.clone,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def add_commit(
        self, filename: str, content: str, message: str, cwd: Path | None = None
    ) -> str:
        """Add a file and commit it. Returns the commit hash."""
        target = cwd or self.clone
        (target / filename).write_text(content)
        self.run_git("add", filename, cwd=target)
        self.run_git("commit", "-m", message, cwd=target)
        return self.run_git("rev-parse", "HEAD", cwd=target).strip()

    def push(self, branch: str = "main", cwd: Path | None = None) -> None:
        self.run_git("push", "origin", branch, cwd=cwd or self.clone)

    def create_branch(self, name: str, push: bool = True) -> None:
        self.run_git("checkout", "-b", name)
        if push:
            self.run_git("push", "-u", "origin", name)

    def checkout(self, branch: str) -> None:
        self.run_git("checkout", branch)


@pytest.fixture
def git_env(tmp_path) -> GitEnv:
    """Create a temporary git environment with a bare remote and clone.
    Also patches git.WORKTREE_ROOT and the config module to use temp dirs."""
    import server.config as config_module
    import server.git as git_module

    repo_id = "testrepo"

    def _git(*args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    # Create bare remote with main as default branch
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git("init", "--bare", "--initial-branch=main", str(remote))

    # Clone it
    clone = tmp_path / "testrepo"
    _git("clone", str(remote), str(clone))

    # Configure git user for commits
    _git("config", "user.email", "test@test.com", cwd=clone)
    _git("config", "user.name", "Test", cwd=clone)

    # Ensure we're on main
    _git("checkout", "-b", "main", cwd=clone)

    # Initial commit on main
    (clone / "README.md").write_text("initial")
    _git("add", "README.md", cwd=clone)
    _git("commit", "-m", "initial", cwd=clone)
    _git("push", "-u", "origin", "main", cwd=clone)

    env = GitEnv(remote=remote, clone=clone, repo_id=repo_id)

    # Patch git module to use our temp worktree dir
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()

    original_worktree_root = git_module.WORKTREE_ROOT
    git_module.WORKTREE_ROOT = worktree_root

    # Patch config to return our test repo
    from server.config import Repo

    test_repo = Repo(id=repo_id, path=clone, upstream="testorg/testrepo")
    original_repos = config_module._repos
    config_module._repos = {repo_id: test_repo}

    yield env

    git_module.WORKTREE_ROOT = original_worktree_root
    config_module._repos = original_repos


# --- Mock gh CLI ---


@pytest.fixture
def mock_gh():
    """Returns a helper to set up canned gh CLI responses.
    Patches git.run to intercept gh commands while letting git commands through."""
    import server.git as git_module

    original_run = git_module.run
    gh_responses: dict[str, tuple[int, str, str]] = {}

    async def patched_run(*args: str, cwd=None):
        if args and args[0] == "gh":
            key = " ".join(args)
            for pattern, response in gh_responses.items():
                if pattern in key:
                    return response
            return (1, "", f"no mock for: {key}")
        return await original_run(*args, cwd=cwd)

    class GhMock:
        def set_response(self, pattern: str, rc: int, stdout: str, stderr: str = ""):
            gh_responses[pattern] = (rc, stdout, stderr)

    mock = GhMock()

    with patch.object(git_module, "run", side_effect=patched_run):
        yield mock


# --- Mock Claude CLI ---


@pytest.fixture
def mock_claude():
    """Mock claude.run_claude_headless to return configurable results."""
    import server.claude as claude_module

    mock = AsyncMock(return_value=(True, "Claude resolved the conflicts"))

    with patch.object(claude_module, "run_claude_headless", mock):
        yield mock


# --- Mock PTY manager ---


@pytest.fixture
def mock_pty():
    """Mock the pty_manager in server.api to avoid real PTY spawning."""
    import server.api as api_module
    from server.pty import PtySession

    fake_session = PtySession(session_id="fake", master_fd=-1, pid=0)

    alive_sessions: set[str] = set()

    mock_manager = MagicMock()

    def _spawn(session_id, **kwargs):
        alive_sessions.add(session_id)
        return fake_session

    mock_manager.spawn.side_effect = _spawn
    mock_manager.is_alive.side_effect = lambda sid: sid in alive_sessions
    mock_manager.get_transcript.return_value = ""
    mock_manager.get_raw_output.return_value = b""
    mock_manager.get.return_value = None
    mock_manager.remove.side_effect = lambda sid: alive_sessions.discard(sid)

    async def _terminate(sid):
        alive_sessions.discard(sid)

    mock_manager.terminate = AsyncMock(side_effect=_terminate)

    with patch.object(api_module, "pty_manager", mock_manager):
        yield mock_manager
