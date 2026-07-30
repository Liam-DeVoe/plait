from __future__ import annotations

from hypothesis import given, settings, strategies as st

from server import db
from server.models import (
    CIStatus,
    Session,
    SessionRole,
    Slate,
    SyncStatus,
    Worktop,
    WorktopStatus,
)

# --- Hand-written CRUD tests ---


async def test_create_and_get_worktop(init_db):
    worktop = Worktop(repo="org/repo", branch="main", worktree_path="/tmp/wt")
    await db.create_worktop(worktop)

    fetched = await db.get_worktop(worktop.id)
    assert fetched is not None
    assert fetched.repo == "org/repo"
    assert fetched.branch == "main"
    assert fetched.status is WorktopStatus.open


async def test_list_worktops_empty(init_db):
    worktops = await db.list_worktops()
    assert worktops == []


async def test_list_worktops_filter_by_status(init_db):
    active = Worktop(repo="org/a", branch="b", worktree_path="/tmp/a")
    archived = Worktop(
        repo="org/b", branch="c", worktree_path="/tmp/b", status=WorktopStatus.archived
    )
    await db.create_worktop(active)
    await db.create_worktop(archived)

    active_worktops = await db.list_worktops(status=WorktopStatus.open)
    assert len(active_worktops) == 1
    assert active_worktops[0].id == active.id

    archived_worktops = await db.list_worktops(status=WorktopStatus.archived)
    assert len(archived_worktops) == 1
    assert archived_worktops[0].id == archived.id


async def test_update_worktop(init_db):
    worktop = Worktop(repo="org/repo", branch="main", worktree_path="/tmp/wt")
    await db.create_worktop(worktop)

    updated = await db.update_worktop(
        worktop.id,
        ci_status=CIStatus.passing,
        sync_status=SyncStatus.current,
    )
    assert updated is not None
    assert updated.ci_status is CIStatus.passing
    assert updated.sync_status is SyncStatus.current


async def test_delete_worktop(init_db):
    worktop = Worktop(repo="org/repo", branch="main", worktree_path="/tmp/wt")
    await db.create_worktop(worktop)

    assert await db.delete_worktop(worktop.id)
    assert await db.get_worktop(worktop.id) is None


async def test_delete_nonexistent_worktop(init_db):
    assert not await db.delete_worktop("nonexistent")


async def test_get_nonexistent_worktop(init_db):
    assert await db.get_worktop("nonexistent") is None


async def test_create_and_list_sessions(init_db):
    worktop = Worktop(repo="org/repo", branch="main", worktree_path="/tmp/wt")
    await db.create_worktop(worktop)

    s1 = Session(worktop_id=worktop.id, role=SessionRole.daemon, trigger="merge")
    s2 = Session(worktop_id=worktop.id, role=SessionRole.user)
    await db.create_session(s1)
    await db.create_session(s2)

    sessions = await db.list_sessions(worktop.id)
    assert len(sessions) == 2


async def test_update_session(init_db):
    worktop = Worktop(repo="org/repo", branch="main", worktree_path="/tmp/wt")
    await db.create_worktop(worktop)
    session = Session(worktop_id=worktop.id)
    await db.create_session(session)

    updated = await db.update_session(session.id, succeeded=1, transcript="done")
    assert updated is not None
    assert updated.succeeded is True
    assert updated.transcript == "done"


async def test_create_and_get_slate(init_db):
    slate = Slate(view_id="test-view")
    await db.create_slate(slate)

    fetched = await db.get_slate(slate.id)
    assert fetched is not None
    assert fetched.session_id is None


async def test_list_slates(init_db):
    await db.create_slate(Slate(view_id="v"))
    await db.create_slate(Slate(view_id="v"))

    slates = await db.list_slates()
    assert len(slates) == 2


async def test_slate_session_id(init_db):
    slate = Slate(session_id="sess-123", view_id="v")
    await db.create_slate(slate)
    fetched = await db.get_slate(slate.id)
    assert fetched is not None
    assert fetched.session_id == "sess-123"


async def test_update_slate(init_db):
    slate = Slate(view_id="v")
    await db.create_slate(slate)

    updated = await db.update_slate(slate.id, session_id="sess-456")
    assert updated is not None
    assert updated.session_id == "sess-456"


async def test_get_session(init_db):
    worktop = Worktop(repo="org/repo", branch="main", worktree_path="/tmp/wt")
    await db.create_worktop(worktop)
    session = Session(worktop_id=worktop.id, role=SessionRole.daemon)
    await db.create_session(session)

    fetched = await db.get_session(session.id)
    assert fetched is not None
    assert fetched.id == session.id
    assert fetched.worktop_id == worktop.id

    assert await db.get_session("nonexistent") is None


async def test_session_with_slate_id(init_db):
    slate = Slate(view_id="v")
    await db.create_slate(slate)
    session = Session(slate_id=slate.id, role=SessionRole.daemon, trigger="slate")
    await db.create_session(session)

    fetched = await db.get_session(session.id)
    assert fetched is not None
    assert fetched.slate_id == slate.id
    assert fetched.worktop_id is None


async def test_list_worktops_by_slate(init_db):
    slate = Slate(view_id="v")
    await db.create_slate(slate)

    c1 = Worktop(repo="org/a", branch="b1", worktree_path="/tmp/a", slate_id=slate.id)
    c2 = Worktop(repo="org/b", branch="b2", worktree_path="/tmp/b", slate_id=slate.id)
    c3 = Worktop(repo="org/c", branch="b3", worktree_path="/tmp/c")  # no slate
    await db.create_worktop(c1)
    await db.create_worktop(c2)
    await db.create_worktop(c3)

    worktops = await db.list_worktops_by_slate(slate.id)
    assert len(worktops) == 2
    assert {c.id for c in worktops} == {c1.id, c2.id}


# --- Hypothesis round-trip tests ---


st_worktop_status = st.sampled_from(list(WorktopStatus))
st_ci_status = st.sampled_from(list(CIStatus))
st_sync_status = st.sampled_from(list(SyncStatus))
st_session_role = st.sampled_from(list(SessionRole))


@st.composite
def st_worktop(draw):
    return Worktop(
        repo=draw(st.text(min_size=1, max_size=50)),
        branch=draw(st.text(min_size=1, max_size=50)),
        worktree_path=draw(st.text(min_size=1, max_size=100)),
        slate_id=draw(st.none() | st.text(min_size=1, max_size=36)),
        pr_number=draw(st.none() | st.integers(min_value=1, max_value=99999)),
        pr_url=draw(st.none() | st.text(min_size=1, max_size=200)),
        ci_status=draw(st_ci_status),
        sync_status=draw(st_sync_status),
        status=draw(st_worktop_status),
        archived_at=draw(st.none() | st.text(min_size=1, max_size=30)),
        archive_reason=draw(st.none() | st.sampled_from(["merged", "closed"])),
    )


@st.composite
def st_session(draw):
    return Session(
        worktop_id=draw(st.none() | st.text(min_size=1, max_size=36)),
        slate_id=draw(st.none() | st.text(min_size=1, max_size=36)),
        role=draw(st_session_role),
        trigger=draw(st.none() | st.text(min_size=0, max_size=50)),
        succeeded=draw(st.none() | st.booleans()),
        transcript=draw(st.text(min_size=0, max_size=500)),
        ended_at=draw(st.none() | st.text(min_size=1, max_size=30)),
    )


@st.composite
def st_slate(draw):
    return Slate(
        session_id=draw(st.none() | st.text(min_size=1, max_size=36)),
        view_id=draw(st.text(min_size=1, max_size=36)),
    )


@given(worktop=st_worktop())
@settings(max_examples=50)
async def test_worktop_roundtrip(worktop: Worktop):
    await db.init_db()
    await db.create_worktop(worktop)
    fetched = await db.get_worktop(worktop.id)
    assert fetched is not None
    assert fetched.id == worktop.id
    assert fetched.repo == worktop.repo
    assert fetched.branch == worktop.branch
    assert fetched.worktree_path == worktop.worktree_path
    assert fetched.slate_id == worktop.slate_id
    assert fetched.pr_number == worktop.pr_number
    assert fetched.pr_url == worktop.pr_url
    assert fetched.ci_status is worktop.ci_status
    assert fetched.sync_status is worktop.sync_status
    assert fetched.status is worktop.status
    assert fetched.archived_at == worktop.archived_at
    assert fetched.archive_reason == worktop.archive_reason


@given(session=st_session())
@settings(max_examples=50)
async def test_session_roundtrip(session: Session):
    await db.init_db()
    await db.create_session(session)
    fetched = await db.get_session(session.id)
    assert fetched is not None
    assert fetched.worktop_id == session.worktop_id
    assert fetched.slate_id == session.slate_id
    assert fetched.role is session.role
    assert fetched.trigger == session.trigger
    assert fetched.succeeded == session.succeeded
    assert fetched.transcript == session.transcript
    assert fetched.ended_at == session.ended_at


@given(slate=st_slate())
@settings(max_examples=50)
async def test_slate_roundtrip(slate: Slate):
    await db.init_db()
    await db.create_slate(slate)
    fetched = await db.get_slate(slate.id)
    assert fetched is not None
    assert fetched.id == slate.id
    assert fetched.session_id == slate.session_id
