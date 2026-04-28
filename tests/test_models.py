from server.models import (
    CIStatus,
    Session,
    SessionRole,
    Slate,
    SyncStatus,
    Worktop,
    WorktopStatus,
)


def test_worktop_defaults():
    worktop = Worktop()
    assert worktop.id  # non-empty UUID
    assert worktop.status == WorktopStatus.open
    assert worktop.ci_status == CIStatus.unknown
    assert worktop.sync_status == SyncStatus.current
    assert worktop.slate_id is None
    assert worktop.pr_number is None
    assert worktop.pr_url is None
    assert worktop.archived_at is None
    assert worktop.created_at  # non-empty timestamp


def test_session_defaults():
    session = Session()
    assert session.id
    assert session.role == SessionRole.user
    assert session.succeeded is None
    assert session.trigger is None
    assert session.ended_at is None
    assert session.transcript == ""


def test_slate_defaults():
    slate = Slate()
    assert slate.id
    assert slate.session_id is None


def test_session_slate_id():
    session = Session(slate_id="abc")
    assert session.slate_id == "abc"
    assert session.worktop_id is None


def test_enum_values():
    assert WorktopStatus.open.value == "open"
    assert WorktopStatus.archived.value == "archived"
    assert CIStatus.passing.value == "passing"
    assert SyncStatus.behind.value == "behind"
    assert SessionRole.daemon.value == "daemon"


def test_worktop_unique_ids():
    c1 = Worktop()
    c2 = Worktop()
    assert c1.id != c2.id
