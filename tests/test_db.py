from __future__ import annotations

from hypothesis import given, settings, strategies as st

from server import db
from server.models import (
    Cell,
    CellStatus,
    CIStatus,
    Session,
    SessionRole,
    Sortie,
    SyncStatus,
)

# --- Hand-written CRUD tests ---


async def test_create_and_get_cell(init_db):
    cell = Cell(repo="org/repo", branch="main", worktree_path="/tmp/wt")
    await db.create_cell(cell)

    fetched = await db.get_cell(cell.id)
    assert fetched is not None
    assert fetched.repo == "org/repo"
    assert fetched.branch == "main"
    assert fetched.status == CellStatus.open


async def test_list_cells_empty(init_db):
    cells = await db.list_cells()
    assert cells == []


async def test_list_cells_filter_by_status(init_db):
    active = Cell(repo="org/a", branch="b", worktree_path="/tmp/a")
    archived = Cell(
        repo="org/b", branch="c", worktree_path="/tmp/b", status=CellStatus.archived
    )
    await db.create_cell(active)
    await db.create_cell(archived)

    active_cells = await db.list_cells(status=CellStatus.open)
    assert len(active_cells) == 1
    assert active_cells[0].id == active.id

    archived_cells = await db.list_cells(status=CellStatus.archived)
    assert len(archived_cells) == 1
    assert archived_cells[0].id == archived.id


async def test_update_cell(init_db):
    cell = Cell(repo="org/repo", branch="main", worktree_path="/tmp/wt")
    await db.create_cell(cell)

    updated = await db.update_cell(
        cell.id,
        ci_status=CIStatus.passing,
        sync_status=SyncStatus.current,
    )
    assert updated is not None
    assert updated.ci_status == CIStatus.passing
    assert updated.sync_status == SyncStatus.current


async def test_delete_cell(init_db):
    cell = Cell(repo="org/repo", branch="main", worktree_path="/tmp/wt")
    await db.create_cell(cell)

    assert await db.delete_cell(cell.id)
    assert await db.get_cell(cell.id) is None


async def test_delete_nonexistent_cell(init_db):
    assert not await db.delete_cell("nonexistent")


async def test_get_nonexistent_cell(init_db):
    assert await db.get_cell("nonexistent") is None


async def test_create_and_list_sessions(init_db):
    cell = Cell(repo="org/repo", branch="main", worktree_path="/tmp/wt")
    await db.create_cell(cell)

    s1 = Session(cell_id=cell.id, role=SessionRole.daemon, trigger="merge")
    s2 = Session(cell_id=cell.id, role=SessionRole.user)
    await db.create_session(s1)
    await db.create_session(s2)

    sessions = await db.list_sessions(cell.id)
    assert len(sessions) == 2


async def test_update_session(init_db):
    cell = Cell(repo="org/repo", branch="main", worktree_path="/tmp/wt")
    await db.create_cell(cell)
    session = Session(cell_id=cell.id)
    await db.create_session(session)

    updated = await db.update_session(session.id, succeeded=1, transcript="done")
    assert updated is not None
    assert updated.succeeded is True
    assert updated.transcript == "done"


async def test_create_and_get_sortie(init_db):
    sortie = Sortie()
    await db.create_sortie(sortie)

    fetched = await db.get_sortie(sortie.id)
    assert fetched is not None
    assert fetched.session_id is None


async def test_list_sorties(init_db):
    await db.create_sortie(Sortie())
    await db.create_sortie(Sortie())

    sorties = await db.list_sorties()
    assert len(sorties) == 2


async def test_sortie_session_id(init_db):
    sortie = Sortie(session_id="sess-123")
    await db.create_sortie(sortie)
    fetched = await db.get_sortie(sortie.id)
    assert fetched is not None
    assert fetched.session_id == "sess-123"


async def test_update_sortie(init_db):
    sortie = Sortie()
    await db.create_sortie(sortie)

    updated = await db.update_sortie(sortie.id, session_id="sess-456")
    assert updated is not None
    assert updated.session_id == "sess-456"


async def test_get_session(init_db):
    cell = Cell(repo="org/repo", branch="main", worktree_path="/tmp/wt")
    await db.create_cell(cell)
    session = Session(cell_id=cell.id, role=SessionRole.daemon)
    await db.create_session(session)

    fetched = await db.get_session(session.id)
    assert fetched is not None
    assert fetched.id == session.id
    assert fetched.cell_id == cell.id

    assert await db.get_session("nonexistent") is None


async def test_session_with_sortie_id(init_db):
    sortie = Sortie()
    await db.create_sortie(sortie)
    session = Session(sortie_id=sortie.id, role=SessionRole.daemon, trigger="sortie")
    await db.create_session(session)

    fetched = await db.get_session(session.id)
    assert fetched is not None
    assert fetched.sortie_id == sortie.id
    assert fetched.cell_id is None


async def test_list_cells_by_sortie(init_db):
    sortie = Sortie()
    await db.create_sortie(sortie)

    c1 = Cell(repo="org/a", branch="b1", worktree_path="/tmp/a", sortie_id=sortie.id)
    c2 = Cell(repo="org/b", branch="b2", worktree_path="/tmp/b", sortie_id=sortie.id)
    c3 = Cell(repo="org/c", branch="b3", worktree_path="/tmp/c")  # no sortie
    await db.create_cell(c1)
    await db.create_cell(c2)
    await db.create_cell(c3)

    cells = await db.list_cells_by_sortie(sortie.id)
    assert len(cells) == 2
    assert {c.id for c in cells} == {c1.id, c2.id}


# --- Hypothesis round-trip tests ---


st_cell_status = st.sampled_from(list(CellStatus))
st_ci_status = st.sampled_from(list(CIStatus))
st_sync_status = st.sampled_from(list(SyncStatus))
st_session_role = st.sampled_from(list(SessionRole))


@st.composite
def st_cell(draw):
    return Cell(
        repo=draw(st.text(min_size=1, max_size=50)),
        branch=draw(st.text(min_size=1, max_size=50)),
        worktree_path=draw(st.text(min_size=1, max_size=100)),
        sortie_id=draw(st.none() | st.text(min_size=1, max_size=36)),
        pr_number=draw(st.none() | st.integers(min_value=1, max_value=99999)),
        pr_url=draw(st.none() | st.text(min_size=1, max_size=200)),
        ci_status=draw(st_ci_status),
        sync_status=draw(st_sync_status),
        status=draw(st_cell_status),
        archived_at=draw(st.none() | st.text(min_size=1, max_size=30)),
        archive_reason=draw(
            st.none() | st.sampled_from(["merged", "closed"])
        ),
    )


@st.composite
def st_session(draw):
    return Session(
        cell_id=draw(st.none() | st.text(min_size=1, max_size=36)),
        sortie_id=draw(st.none() | st.text(min_size=1, max_size=36)),
        role=draw(st_session_role),
        trigger=draw(st.none() | st.text(min_size=0, max_size=50)),
        succeeded=draw(st.none() | st.booleans()),
        transcript=draw(st.text(min_size=0, max_size=500)),
        ended_at=draw(st.none() | st.text(min_size=1, max_size=30)),
    )


@st.composite
def st_sortie(draw):
    return Sortie(
        session_id=draw(st.none() | st.text(min_size=1, max_size=36)),
    )


@given(cell=st_cell())
@settings(max_examples=50)
async def test_cell_roundtrip(cell: Cell):
    await db.init_db()
    await db.create_cell(cell)
    fetched = await db.get_cell(cell.id)
    assert fetched is not None
    assert fetched.id == cell.id
    assert fetched.repo == cell.repo
    assert fetched.branch == cell.branch
    assert fetched.worktree_path == cell.worktree_path
    assert fetched.sortie_id == cell.sortie_id
    assert fetched.pr_number == cell.pr_number
    assert fetched.pr_url == cell.pr_url
    assert fetched.ci_status == cell.ci_status
    assert fetched.sync_status == cell.sync_status
    assert fetched.status == cell.status
    assert fetched.archived_at == cell.archived_at
    assert fetched.archive_reason == cell.archive_reason


@given(session=st_session())
@settings(max_examples=50)
async def test_session_roundtrip(session: Session):
    await db.init_db()
    await db.create_session(session)
    fetched = await db.get_session(session.id)
    assert fetched is not None
    assert fetched.cell_id == session.cell_id
    assert fetched.sortie_id == session.sortie_id
    assert fetched.role == session.role
    assert fetched.trigger == session.trigger
    assert fetched.succeeded == session.succeeded
    assert fetched.transcript == session.transcript
    assert fetched.ended_at == session.ended_at


@given(sortie=st_sortie())
@settings(max_examples=50)
async def test_sortie_roundtrip(sortie: Sortie):
    await db.init_db()
    await db.create_sortie(sortie)
    fetched = await db.get_sortie(sortie.id)
    assert fetched is not None
    assert fetched.id == sortie.id
    assert fetched.session_id == sortie.session_id
