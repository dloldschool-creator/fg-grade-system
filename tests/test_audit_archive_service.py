"""Tests for archiving old audit_logs entries (§50).

Hits the live database and rolls back, same convention as
test_audit_and_backup.py — a delete is only proven safe by a real DELETE
statement, and delete_before never calls commit() itself (the caller does),
so the whole thing is safe to exercise inside a fixture that rolls back.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import audit_archive_service, audit_service
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


def _old_entry(days_ago: int, **overrides) -> AuditLog:
    fields = dict(
        action=audit_service.LEARNER_CHANGED,
        object_type="learners",
        object_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    fields.update(overrides)
    return AuditLog(**fields)


# --- The protected recent window -------------------------------------------


def test_max_cutoff_is_min_age_days_in_the_past():
    expected = datetime.now(timezone.utc) - timedelta(days=audit_archive_service.MIN_AGE_DAYS)
    assert abs((audit_archive_service.max_cutoff() - expected).total_seconds()) < 5


def test_delete_before_refuses_a_cutoff_inside_the_protected_window(session):
    """Even a Super Admin can't archive the recent window — MIN_AGE_DAYS is
    a floor the function itself enforces, not just a UI suggestion."""
    with pytest.raises(ValueError, match="90 days"):
        audit_archive_service.delete_before(
            session,
            before=datetime.now(timezone.utc) - timedelta(days=1),
            expected_count=0,
            actor_user_id=None,
            reason="test",
        )


# --- The export must match what's actually there ---------------------------


def test_delete_before_refuses_a_stale_expected_count(session):
    session.add(_old_entry(400))
    session.add(_old_entry(400))
    session.flush()
    before = datetime.now(timezone.utc) - timedelta(days=200)

    with pytest.raises(ValueError, match="entries now match"):
        audit_archive_service.delete_before(
            session, before=before, expected_count=999, actor_user_id=None, reason="test"
        )

    # Nothing was deleted — a mismatch must refuse before touching any row.
    assert audit_archive_service.count_before(session, before) == 2


def test_delete_before_refuses_a_blank_reason_before_deleting_anything(session):
    """audit_service.record()'s own REASON_REQUIRED check fires *before* the
    DELETE runs — a blank reason must not leave the rows gone with no
    explanation on record."""
    session.add(_old_entry(400))
    session.flush()
    before = datetime.now(timezone.utc) - timedelta(days=200)
    count = audit_archive_service.count_before(session, before)

    with pytest.raises(ValueError):
        audit_archive_service.delete_before(
            session, before=before, expected_count=count, actor_user_id=None, reason="   "
        )

    assert audit_archive_service.count_before(session, before) == count


# --- The actual archive -----------------------------------------------------


def test_delete_before_deletes_only_matching_rows_and_audits_itself(session):
    kept_id = uuid.uuid4()
    session.add(_old_entry(400, object_id=uuid.uuid4()))
    session.add(_old_entry(400, object_id=uuid.uuid4()))
    session.add(_old_entry(10, object_id=kept_id))  # inside the protected window
    session.flush()

    before = datetime.now(timezone.utc) - timedelta(days=200)
    count = audit_archive_service.count_before(session, before)
    assert count == 2

    deleted = audit_archive_service.delete_before(
        session,
        before=before,
        expected_count=count,
        actor_user_id=None,
        reason="Routine archive — test",
    )
    session.flush()

    assert deleted == 2
    assert audit_archive_service.count_before(session, before) == 0
    # The recent entry was never in scope and must survive untouched.
    assert session.query(AuditLog).filter_by(object_id=kept_id).count() == 1

    archived = (
        session.query(AuditLog)
        .filter_by(action=audit_service.AUDIT_LOG_ARCHIVED)
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert archived is not None
    assert archived.reason == "Routine archive — test"
    assert archived.new_value["deleted_count"] == 2
    # Written with created_at = now, so it is itself newer than `before` and
    # would survive a second archive run at the same cutoff.
    assert archived.created_at.replace(tzinfo=timezone.utc) > before


# --- The downloadable export ------------------------------------------------


def test_export_csv_contains_a_header_and_every_matching_row(session):
    session.add(_old_entry(400, reason="first"))
    session.add(_old_entry(400, reason="second"))
    session.flush()

    before = datetime.now(timezone.utc) - timedelta(days=200)
    data, count = audit_archive_service.export_csv(session, before)

    assert count == 2
    text = data.decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0].split(",")[:3] == ["created_at", "user", "action"]
    assert len(lines) == 3  # header + 2 rows
    assert "first" in text and "second" in text
