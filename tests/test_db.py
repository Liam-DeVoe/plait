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
    SortieStatus,
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
    assert fetched.status == CellStatus.active


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

    active_cells = await db.list_cells(status=CellStatus.active)
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
    sortie = Sortie(prompt="update all repos", repos=["org/a", "org/b"])
    await db.create_sortie(sortie)

    fetched = await db.get_sortie(sortie.id)
    assert fetched is not None
    assert fetched.prompt == "update all repos"
    assert fetched.repos == ["org/a", "org/b"]


async def test_list_sorties(init_db):
    await db.create_sortie(Sortie(prompt="first", repos=["a"]))
    await db.create_sortie(Sortie(prompt="second", repos=["b"]))

    sorties = await db.list_sorties()
    assert len(sorties) == 2


async def test_sortie_empty_repos(init_db):
    sortie = Sortie(prompt="empty", repos=[])
    await db.create_sortie(sortie)
    fetched = await db.get_sortie(sortie.id)
    assert fetched is not None
    assert fetched.repos == []


async def test_update_sortie(init_db):
    sortie = Sortie(prompt="test", repos=["a"])
    await db.create_sortie(sortie)

    updated = await db.update_sortie(sortie.id, status=SortieStatus.completed)
    assert updated is not None
    assert updated.status == SortieStatus.completed


async def test_list_cells_by_sortie(init_db):
    sortie = Sortie(prompt="test", repos=["a", "b"])
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
st_sortie_status = st.sampled_from(list(SortieStatus))


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
    )


@st.composite
def st_session(draw):
    return Session(
        cell_id=draw(st.text(min_size=1, max_size=36)),
        role=draw(st_session_role),
        trigger=draw(st.none() | st.text(min_size=0, max_size=50)),
        succeeded=draw(st.none() | st.booleans()),
        transcript=draw(st.text(min_size=0, max_size=500)),
        ended_at=draw(st.none() | st.text(min_size=1, max_size=30)),
    )


@st.composite
def st_sortie(draw):
    return Sortie(
        prompt=draw(st.text(min_size=0, max_size=200)),
        repos=draw(st.lists(st.text(min_size=1, max_size=50), max_size=10)),
        status=draw(st_sortie_status),
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


@given(session=st_session())
@settings(max_examples=50)
async def test_session_roundtrip(session: Session):
    await db.init_db()
    await db.create_session(session)
    sessions = await db.list_sessions(session.cell_id)
    assert len(sessions) >= 1
    fetched = next(s for s in sessions if s.id == session.id)
    assert fetched.cell_id == session.cell_id
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
    assert fetched.prompt == sortie.prompt
    assert fetched.repos == sortie.repos
    assert fetched.status == sortie.status
