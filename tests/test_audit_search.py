"""Audit Log search (`app/admin_pages/audit_log._search_filter`).

Runs against the live database inside a rolled-back session: it writes
two audit entries, searches for them, and never commits.
"""

import uuid

import pytest

from app import audit_service
from app.admin_pages.audit_log import _search_filter
from app.database import SessionLocal
from app.models.admin import AuditLog


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _matches(session, term):
    return {row.id for row in session.query(AuditLog).filter(_search_filter(term))}


def test_search_finds_text_inside_the_payload_and_reason(session):
    marker = f"zq{uuid.uuid4().hex[:10]}"
    entry = audit_service.record(
        session,
        action=audit_service.ATTENDANCE_CHANGED,
        object_type="attendance_records",
        object_id=uuid.uuid4(),
        previous={"status": "PRESENT"},
        new={"status": "ABSENT", "learner": f"{marker.upper()}, Test"},
    )
    session.flush()
    found = _matches(session, marker)  # case-insensitive
    assert entry.id in found
    assert entry.id not in _matches(session, marker + "-nope")


def test_search_treats_percent_and_underscore_literally(session):
    """`%` and `_` are LIKE wildcards; typed into the box they must mean
    themselves, or "_" would match every entry."""
    marker = f"zq{uuid.uuid4().hex[:10]}"
    with_underscore = audit_service.record(
        session, action=audit_service.DATA_IMPORTED, object_type="import_jobs",
        object_id=uuid.uuid4(), new={"file": f"{marker}_a.csv"},
    )
    without = audit_service.record(
        session, action=audit_service.DATA_IMPORTED, object_type="import_jobs",
        object_id=uuid.uuid4(), new={"file": f"{marker}xa.csv"},
    )
    session.flush()
    found = _matches(session, f"{marker}_a")
    assert with_underscore.id in found
    assert without.id not in found


def test_every_word_must_match_in_any_order(session):
    """'pineda anne' must find "PINEDA, ANNE" — the old phrase match did
    not — and each extra word only narrows."""
    marker = f"zq{uuid.uuid4().hex[:8]}"
    anne = audit_service.record(
        session, action=audit_service.ATTENDANCE_CHANGED, object_type="attendance_records",
        object_id=uuid.uuid4(), new={"learner": f"{marker.upper()}, ANNE"},
    )
    mark = audit_service.record(
        session, action=audit_service.ATTENDANCE_CHANGED, object_type="attendance_records",
        object_id=uuid.uuid4(), new={"learner": f"{marker.upper()}, MARK"},
    )
    session.flush()
    assert {anne.id, mark.id} <= _matches(session, marker)
    found = _matches(session, f"{marker} anne")
    assert anne.id in found and mark.id not in found
    assert anne.id in _matches(session, f"anne {marker}")


def test_search_matches_a_learner_by_name_through_the_id(session):
    """LEARNER_CHANGED stores only the changed field; the learner's name
    still finds it, by subquery on `learners`."""
    from app.models.learners import Learner

    learner = session.query(Learner).first()
    if learner is None:
        pytest.skip("no learners")
    entry = audit_service.record(
        session, action=audit_service.LEARNER_CHANGED, object_type="learners",
        object_id=learner.id, previous={"sex": "MALE"}, new={"sex": "FEMALE"},
    )
    session.flush()
    assert entry.id in _matches(session, f"{learner.first_name} {learner.last_name}")


def test_empty_search_is_no_filter():
    assert _search_filter("") is None
    assert _search_filter("  ,  ") is None
