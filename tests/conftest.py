from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- In-memory SQLite DB ---


@pytest.fixture(autouse=True)
async def _use_memory_db(tmp_path):
    """Override DB_PATH to use a temporary file DB for every test.

    db.py caches a single connection at module level. We must close it before
    and after each test so that DB_PATH overrides take effect and tests don't
    share state through a stale connection.
    """
    import server.db as db_module

    db_file = tmp_path / "test.db"
    original = db_module.DB_PATH
    db_module.DB_PATH = db_file
    await db_module.close_db()
    yield
    await db_module.close_db()
    db_module.DB_PATH = original


@pytest.fixture(autouse=True)
def _stub_config():
    """Stub the synchronous config caches for every test.

    Without this, any test that touches `config.get_repos()` /
    `config.get_author()` would raise (the cache is normally primed by
    `await config.refresh()` in the FastAPI lifespan, which tests don't
    run). git_env and git_env_local override this with their own test
    repos.
    """
    import server.config as config_module

    original_repos = config_module._repos_cache
    original_author = config_module._author_cache
    config_module._repos_cache = {}
    config_module._author_cache = "testuser"
    yield
    config_module._repos_cache = original_repos
    config_module._author_cache = original_author


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


@pytest.fixture(scope="session")
def _git_env_template(tmp_path_factory) -> Path:
    """Create a template git environment once per worker.
    Returns the path to a directory containing remote.git/ and testrepo/."""
    template = tmp_path_factory.mktemp("git_template")

    def _git(*args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    remote = template / "remote.git"
    remote.mkdir()
    _git("init", "--bare", "--initial-branch=main", str(remote))

    clone = template / "testrepo"
    _git("clone", str(remote), str(clone))

    _git("config", "user.email", "test@test.com", cwd=clone)
    _git("config", "user.name", "Test", cwd=clone)

    _git("checkout", "-b", "main", cwd=clone)

    (clone / "README.md").write_text("initial")
    _git("add", "README.md", cwd=clone)
    _git("commit", "-m", "initial", cwd=clone)
    _git("push", "-u", "origin", "main", cwd=clone)
    _git("remote", "set-head", "origin", "--auto", cwd=clone)

    return template


@pytest.fixture
def git_env(tmp_path, _git_env_template) -> GitEnv:
    """Copy the template git environment for this test.
    Also patches git.WORKTREE_ROOT and the config caches to use temp dirs."""
    import server.config as config_module
    import server.git as git_module
    from server.models import Repo

    repo_id = "testrepo"

    # Copy template instead of creating from scratch
    shutil.copytree(_git_env_template, tmp_path / "git", symlinks=True)
    git_root = tmp_path / "git"
    remote = git_root / "remote.git"
    clone = git_root / "testrepo"

    # Fix remote URL to point to this copy's bare repo, not the template's
    subprocess.run(
        ["git", "remote", "set-url", "origin", str(remote)],
        cwd=clone,
        check=True,
        capture_output=True,
    )

    env = GitEnv(remote=remote, clone=clone, repo_id=repo_id)

    # Patch git module to use our temp worktree dir
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()

    original_worktree_root = git_module.WORKTREE_ROOT
    git_module.WORKTREE_ROOT = worktree_root

    # Override the config caches directly. We don't go through the DB
    # because most tests don't need DB-resident repos — they just need
    # `config.get_repo(...)` to return the right object synchronously.
    original_repos = config_module._repos_cache
    original_author = config_module._author_cache
    config_module._repos_cache = {
        repo_id: Repo(
            id=repo_id,
            path=clone,
            kind="remote",
            upstream="testorg/testrepo",
        )
    }
    config_module._author_cache = "testuser"

    git_module._main_branch_cache.clear()
    # The test clone's `origin` URL is a local path, which doesn't normalize
    # to a GitHub identity. Skip the live `git remote -v` resolution by
    # seeding the cache directly.
    git_module._upstream_remote_cache.clear()
    git_module._upstream_remote_cache[repo_id] = "origin"

    yield env

    git_module.WORKTREE_ROOT = original_worktree_root
    config_module._repos_cache = original_repos
    config_module._author_cache = original_author
    git_module._main_branch_cache.clear()
    git_module._upstream_remote_cache.clear()


@pytest.fixture(scope="session")
def _git_env_local_template(tmp_path_factory) -> Path:
    """Template for a local-only git repo: a normal repo with no `origin`."""
    template = tmp_path_factory.mktemp("git_local_template")

    def _git(*args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    repo = template / "localrepo"
    _git("init", "--initial-branch=main", str(repo))
    _git("config", "user.email", "test@test.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)

    (repo / "README.md").write_text("initial")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "initial", cwd=repo)

    return template


@dataclass
class LocalGitEnv:
    """A local-only git environment: a single repo with no remote."""

    clone: Path
    repo_id: str

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
        target = cwd or self.clone
        (target / filename).write_text(content)
        self.run_git("add", filename, cwd=target)
        self.run_git("commit", "-m", message, cwd=target)
        return self.run_git("rev-parse", "HEAD", cwd=target).strip()

    def create_branch(self, name: str) -> None:
        self.run_git("checkout", "-b", name)

    def checkout(self, branch: str) -> None:
        self.run_git("checkout", branch)


@pytest.fixture
def git_env_local(tmp_path, _git_env_local_template) -> LocalGitEnv:
    """Local-only git environment for this test.
    Patches git.WORKTREE_ROOT and the config caches to mark the repo as local."""
    import server.config as config_module
    import server.git as git_module
    from server.models import Repo

    repo_id = "localrepo"

    shutil.copytree(_git_env_local_template, tmp_path / "git", symlinks=True)
    git_root = tmp_path / "git"
    clone = git_root / "localrepo"

    env = LocalGitEnv(clone=clone, repo_id=repo_id)

    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()

    original_worktree_root = git_module.WORKTREE_ROOT
    git_module.WORKTREE_ROOT = worktree_root

    original_repos = config_module._repos_cache
    original_author = config_module._author_cache
    config_module._repos_cache = {
        repo_id: Repo(
            id=repo_id,
            path=clone,
            kind="local",
            upstream=None,
        )
    }
    config_module._author_cache = "testuser"

    git_module._main_branch_cache.clear()
    git_module._upstream_remote_cache.clear()

    yield env

    git_module.WORKTREE_ROOT = original_worktree_root
    config_module._repos_cache = original_repos
    config_module._author_cache = original_author
    git_module._main_branch_cache.clear()
    git_module._upstream_remote_cache.clear()


# --- Mock gh CLI ---


@pytest.fixture(autouse=True)
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


class _SpawnSessionMock:
    """Mock for spawn_session that returns a task resolving with an exit code.

    Usage:
        mock.return_value = 0                              # success (default)
        mock.return_value = 1                              # failure
        mock.side_effect = async_func(session_id, cmd, cwd, **kw) -> int
    """

    def __init__(self):
        self.return_value = 0
        self.side_effect = None
        self._calls: list[tuple] = []

    def __call__(self, session_id, cmd, cwd, **kwargs):
        self._calls.append((session_id, cmd, cwd, kwargs))

        async def _run():
            from server import db

            if self.side_effect:
                exit_code = await self.side_effect(session_id, cmd, cwd, **kwargs)
            else:
                exit_code = self.return_value

            await db.update_session(
                session_id,
                ended_at=datetime.now(timezone.utc).isoformat(),
            )
            return exit_code

        return asyncio.create_task(_run())

    def assert_called_once(self):
        assert len(self._calls) == 1

    def assert_not_called(self):
        assert len(self._calls) == 0


@pytest.fixture(autouse=True)
def mock_claude():
    """Mock spawn_session in the daemon to avoid spawning real Claude processes.

    Only affects daemon code paths (process_worktop, spawn_slate_session).
    API endpoints use the real spawn_session backed by mock_pty.
    """
    import server.daemon as daemon_module

    mock = _SpawnSessionMock()

    with patch.object(daemon_module, "spawn_session", mock):
        yield mock


# --- Mock PTY manager ---


@pytest.fixture
def mock_pty():
    """Mock pty_manager in server.api and server.sessions to avoid real PTY spawning."""
    import server.api as api_module
    import server.sessions as sessions_module
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

    with (
        patch.object(api_module, "pty_manager", mock_manager),
        patch.object(sessions_module, "pty_manager", mock_manager),
    ):
        yield mock_manager
