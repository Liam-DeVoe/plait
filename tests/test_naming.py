from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from pathlib import Path

from server import db, git, naming
from server.api import app
from server.daemon import process_worktop
from server.models import Session, SessionRole, Worktop
from server.naming import _sanitize, gather_signal, maybe_name_worktop


async def _create_worktop_in_db(git_env, branch: str, worktop_id: str) -> Worktop:
    wt_path = await git.create_worktree(git_env.repo_id, branch, worktop_id)
    worktop = Worktop(
        id=worktop_id,
        repo=git_env.repo_id,
        branch=branch,
        worktree_path=wt_path,
    )
    await db.create_worktop(worktop)
    return worktop


# --- _sanitize ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Fix timeout handling", "Fix timeout handling"),
        ("  Fix timeout handling  \n", "Fix timeout handling"),
        ('"Fix timeout handling"', "Fix timeout handling"),
        ("Fix timeout handling.", "Fix timeout handling"),
        ("\nFix timeout handling\nExtra explanation line", "Fix timeout handling"),
        ("", None),
        ("   \n \n", None),
        ("x" * 200, None),
    ],
)
def test_sanitize(raw, expected):
    assert _sanitize(raw) == expected


# --- gather_signal ---


async def test_no_signal_without_sessions_or_commits(git_env, init_db):
    worktop = await _create_worktop_in_db(git_env, "naming-1", "naming-1")
    assert await gather_signal(worktop) is None


async def test_signal_from_commits(git_env, init_db):
    worktop = await _create_worktop_in_db(git_env, "naming-2", "naming-2")
    git_env.add_commit(
        "feature.py", "x = 1", "add feature flag", cwd=Path(worktop.worktree_path)
    )

    signal = await gather_signal(worktop)
    assert "add feature flag" in signal
    assert "feature.py" in signal


async def test_signal_from_transcript(git_env, init_db):
    worktop = await _create_worktop_in_db(git_env, "naming-3", "naming-3")
    await db.create_session(
        Session(
            worktop_id=worktop.id,
            role=SessionRole.user,
            transcript="please refactor the retry logic",
        )
    )

    signal = await gather_signal(worktop)
    assert "please refactor the retry logic" in signal


async def test_signal_without_worktree_uses_transcripts(init_db):
    """Archived worktops (no worktree on disk) are named from transcripts."""
    worktop = Worktop(repo="gone", branch="b", worktree_path="/nonexistent")
    await db.create_worktop(worktop)
    await db.create_session(
        Session(worktop_id=worktop.id, transcript="fix the flaky CI job")
    )

    signal = await gather_signal(worktop)
    assert "fix the flaky CI job" in signal


async def test_transcript_truncated(git_env, init_db):
    worktop = await _create_worktop_in_db(git_env, "naming-4", "naming-4")
    await db.create_session(Session(worktop_id=worktop.id, transcript="x" * 100_000))

    signal = await gather_signal(worktop)
    assert len(signal) < naming.TRANSCRIPT_HEAD_CHARS + 1000


# --- maybe_name_worktop ---


async def test_names_worktop_with_signal(git_env, init_db, mock_namer):
    worktop = await _create_worktop_in_db(git_env, "naming-5", "naming-5")
    git_env.add_commit("f.py", "x", "add feature", cwd=Path(worktop.worktree_path))
    mock_namer.return_value = "Add feature flag"

    assert await maybe_name_worktop(worktop) == "Add feature flag"
    fetched = await db.get_worktop(worktop.id)
    assert fetched.name == "Add feature flag"


async def test_skips_without_signal(git_env, init_db, mock_namer):
    worktop = await _create_worktop_in_db(git_env, "naming-6", "naming-6")
    mock_namer.return_value = "Should not be used"

    assert await maybe_name_worktop(worktop) is None
    mock_namer.assert_not_called()
    fetched = await db.get_worktop(worktop.id)
    assert fetched.name is None


async def test_skips_already_named(git_env, init_db, mock_namer):
    worktop = await _create_worktop_in_db(git_env, "naming-7", "naming-7")
    await db.update_worktop(worktop.id, name="Existing name")
    worktop.name = "Existing name"

    assert await maybe_name_worktop(worktop) is None
    mock_namer.assert_not_called()


async def test_model_failure_leaves_unnamed(git_env, init_db, mock_namer):
    worktop = await _create_worktop_in_db(git_env, "naming-8", "naming-8")
    git_env.add_commit("f.py", "x", "add feature", cwd=Path(worktop.worktree_path))
    mock_namer.return_value = None  # claude call failed

    assert await maybe_name_worktop(worktop) is None
    fetched = await db.get_worktop(worktop.id)
    assert fetched.name is None


async def test_garbage_output_leaves_unnamed(git_env, init_db, mock_namer):
    worktop = await _create_worktop_in_db(git_env, "naming-9", "naming-9")
    git_env.add_commit("f.py", "x", "add feature", cwd=Path(worktop.worktree_path))
    mock_namer.return_value = "x" * 500

    assert await maybe_name_worktop(worktop) is None
    fetched = await db.get_worktop(worktop.id)
    assert fetched.name is None


# --- daemon integration ---


async def test_daemon_names_unnamed_worktop(git_env, init_db, mock_namer):
    worktop = await _create_worktop_in_db(git_env, "naming-10", "naming-10")
    git_env.add_commit("f.py", "x", "add feature", cwd=Path(worktop.worktree_path))
    mock_namer.return_value = "Add feature flag"

    await process_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched.name == "Add feature flag"


async def test_daemon_names_tends_disabled_worktop(git_env, init_db, mock_namer):
    """Naming is metadata, not a tend — it runs even with auto-tends off."""
    worktop = await _create_worktop_in_db(git_env, "naming-11", "naming-11")
    await db.update_worktop(worktop.id, tends_enabled=0)
    worktop.tends_enabled = False
    git_env.add_commit("f.py", "x", "add feature", cwd=Path(worktop.worktree_path))
    mock_namer.return_value = "Add feature flag"

    await process_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched.name == "Add feature flag"


# --- rename API ---


@pytest.fixture
async def client(init_db, git_env, mock_gh):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, git_env


async def test_rename_worktop(client):
    c, git_env = client
    worktop = await _create_worktop_in_db(git_env, "naming-12", "naming-12")

    resp = await c.put(f"/worktops/{worktop.id}/name", json={"name": "  My name  "})
    assert resp.status_code == 200
    assert resp.json()["name"] == "My name"

    fetched = await db.get_worktop(worktop.id)
    assert fetched.name == "My name"


@pytest.mark.parametrize("cleared", [None, "", "   "])
async def test_clear_worktop_name(client, cleared):
    c, git_env = client
    worktop = await _create_worktop_in_db(git_env, "naming-13", "naming-13")
    await db.update_worktop(worktop.id, name="Old name")

    resp = await c.put(f"/worktops/{worktop.id}/name", json={"name": cleared})
    assert resp.status_code == 200
    assert resp.json()["name"] is None

    fetched = await db.get_worktop(worktop.id)
    assert fetched.name is None


async def test_rename_missing_worktop(client):
    c, _ = client
    resp = await c.put("/worktops/nonexistent/name", json={"name": "x"})
    assert resp.status_code == 404
