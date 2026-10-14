"""Audit Log search (`app/admin_pages/audit_log._search_filter`).

Runs against the live database inside a rolled-back session: it writes
two audit entries, searches for them, and never commits.
"""

import uuid
from types import SimpleNamespace

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


def _matches(session, term, users=None, learners=None):
    return {
        row.id
        for row in session.query(AuditLog).filter(_search_filter(term, users or {}, learners or {}))
    }


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


def test_search_matches_names_the_table_shows_by_id(session):
    """A learner's name finds entries that only carry the learner's id
    (LEARNER_CHANGED stores just the changed field), and a user's name
    finds what they did — both resolved from dicts the page already holds."""
    learner_id, user_id = uuid.uuid4(), uuid.uuid4()
    marker = f"Zq{uuid.uuid4().hex[:8]}"
    learners = {
        learner_id: SimpleNamespace(
            last_name=marker, first_name="Ana", middle_name=None, extension_name=None
        )
    }
    entry = audit_service.record(
        session, action=audit_service.LEARNER_CHANGED, object_type="learners",
        object_id=learner_id, previous={"sex": "MALE"}, new={"sex": "FEMALE"},
    )
    session.flush()
    assert entry.id in _matches(session, marker.lower(), learners=learners)

    users = {user_id: SimpleNamespace(full_name=f"{marker} Teacher")}
    query = session.query(AuditLog).filter(_search_filter(f"{marker} teach", users, {}))
    # The condition is built; no entry carries this made-up user id.
    assert str(query.statement.compile()).count("user_id IN") == 1
