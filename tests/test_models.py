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


def test_cell_defaults():
    cell = Cell()
    assert cell.id  # non-empty UUID
    assert cell.status == CellStatus.active
    assert cell.ci_status == CIStatus.unknown
    assert cell.sync_status == SyncStatus.current
    assert cell.sortie_id is None
    assert cell.pr_number is None
    assert cell.pr_url is None
    assert cell.archived_at is None
    assert cell.created_at  # non-empty timestamp


def test_session_defaults():
    session = Session()
    assert session.id
    assert session.role == SessionRole.user
    assert session.succeeded is None
    assert session.trigger is None
    assert session.ended_at is None
    assert session.transcript == ""


def test_sortie_defaults():
    sortie = Sortie()
    assert sortie.id
    assert sortie.status == SortieStatus.active
    assert sortie.session_id is None
    assert sortie.prompt == ""


def test_session_sortie_id():
    session = Session(sortie_id="abc")
    assert session.sortie_id == "abc"
    assert session.cell_id is None


def test_enum_values():
    assert CellStatus.active.value == "active"
    assert CellStatus.archived.value == "archived"
    assert CIStatus.passing.value == "passing"
    assert SyncStatus.conflict.value == "conflict"
    assert SessionRole.daemon.value == "daemon"


def test_cell_unique_ids():
    c1 = Cell()
    c2 = Cell()
    assert c1.id != c2.id
