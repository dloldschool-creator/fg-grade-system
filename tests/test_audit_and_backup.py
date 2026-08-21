"""Tests for audit logging (§50) and the administrative backup (§55).

The audit tests hit the live database and roll back — an audit entry is
only useful if it survives a real INSERT, and the JSONB coercion is the
part most likely to break there rather than in memory.
"""

import io
import uuid
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from app import audit_service
from app.backup_service import backup_filename, generate_backup
from app.database import SessionLocal
from app.models.admin import AuditLog
from app.models.base import Base
from app.models.enums import GradeWorkflowStatus


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# --- Value coercion --------------------------------------------------------


def test_a_decimal_grade_becomes_a_plain_int():
    """Grades are Numeric(5,2) but always whole (§60) — "93.00" in an audit
    entry reads as a precision the system doesn't actually have."""
    assert audit_service.jsonable(Decimal("93.00")) == 93


def test_a_fractional_decimal_keeps_its_fraction():
    assert audit_service.jsonable(Decimal("92.5")) == 92.5


def test_enums_and_dates_and_uuids_survive_serialisation():
    value = audit_service.jsonable(
        {
            "status": GradeWorkflowStatus.SUBMITTED,
            "when": date(2026, 6, 8),
            "who": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        }
    )
    assert value == {
        "status": "SUBMITTED",
        "when": "2026-06-08",
        "who": "00000000-0000-0000-0000-000000000001",
    }


def test_none_stays_none_rather_than_becoming_a_string():
    """Rule 2 again: a missing grade in a before/after pair must not read
    as the string "None"."""
    assert audit_service.jsonable({"official_grade": None}) == {"official_grade": None}


def test_changes_narrows_to_what_actually_differs():
    previous, new = audit_service.changes(
        {"grade": 90, "status": "DRAFT"}, {"grade": 95, "status": "DRAFT"}
    )
    assert previous == {"grade": 90}
    assert new == {"grade": 95}


def test_changes_is_empty_when_nothing_moved():
    assert audit_service.changes({"a": 1}, {"a": 1}) == ({}, {})


# --- Reason enforcement ----------------------------------------------------


@pytest.mark.parametrize("action", sorted(audit_service.REASON_REQUIRED))
def test_reason_required_actions_refuse_a_blank_reason(session, action):
    """§50 requires a reason on these; recording one without is a bug in
    the calling page, so it should fail loudly rather than write a row
    that can't answer "why"."""
    with pytest.raises(ValueError):
        audit_service.record(
            session,
            action=action,
            object_type="term_grades",
            object_id=uuid.uuid4(),
            reason="   ",
        )


def test_a_reason_satisfies_the_requirement(session):
    entry = audit_service.record(
        session,
        action=audit_service.GRADE_REOPENED,
        object_type="enrollments",
        object_id=uuid.uuid4(),
        reason="Encoding error on Term 2",
    )
    assert entry.reason == "Encoding error on Term 2"


# --- Round-tripping through the real table ---------------------------------


def test_an_entry_persists_with_both_values(session):
    object_id = uuid.uuid4()
    audit_service.record(
        session,
        action=audit_service.GRADE_CHANGED,
        object_type="term_grades",
        object_id=object_id,
        previous={"official_grade": Decimal("90.00"), "status": GradeWorkflowStatus.SUBMITTED},
        new={"official_grade": Decimal("95.00"), "status": GradeWorkflowStatus.DRAFT},
    )
    session.flush()

    stored = session.query(AuditLog).filter_by(object_id=object_id).one()
    assert stored.previous_value == {"official_grade": 90, "status": "SUBMITTED"}
    assert stored.new_value == {"official_grade": 95, "status": "DRAFT"}
    assert stored.created_at is not None


def test_the_audit_service_exposes_no_way_to_delete_history():
    """§50: normal teachers must not be able to delete audit history. The
    guarantee is structural — the capability doesn't exist in the app —
    so this test asserts the shape of the module, not a permission check
    that could be bypassed by reaching the function another way.

    **Callables only.** A capability is a function; a string constant
    cannot delete anything. Scanning every public name caught
    `LEARNER_DELETED` on 2026-08-21 — an action *recorded in* the log,
    which is the opposite of a way to erase it — and would catch every
    `*_DELETED` action added after it. Narrowing to callables keeps what
    the test is actually for: `def purge_old_entries(...)` still trips it.
    """
    forbidden = [
        name
        for name in dir(audit_service)
        if not name.startswith("_")
        and callable(getattr(audit_service, name))
        and any(word in name.lower() for word in ("delete", "purge", "clear_log", "truncate"))
    ]
    assert forbidden == []


# --- Backup (§55) ----------------------------------------------------------


def test_backup_contains_every_table_plus_a_manifest(session):
    data, counts = generate_backup(session, taken_by="Test Runner")
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = archive.namelist()

    assert "MANIFEST.txt" in names
    assert len(counts) == len(Base.metadata.sorted_tables)
    for table in Base.metadata.sorted_tables:
        assert any(name.endswith(f"_{table.name}.csv") for name in names), table.name


def test_backup_files_are_numbered_in_foreign_key_order(session):
    """Restoring in file order must never violate a foreign key, which is
    the whole reason the files carry a numeric prefix."""
    data, _ = generate_backup(session)
    archive = zipfile.ZipFile(io.BytesIO(data))
    dumped = [n for n in sorted(archive.namelist()) if n.startswith("data/")]
    expected = [t.name for t in Base.metadata.sorted_tables]
    assert [name.split("_", 1)[1][:-4] for name in dumped] == expected


def test_backup_csv_carries_a_header_row(session):
    data, _ = generate_backup(session)
    archive = zipfile.ZipFile(io.BytesIO(data))
    name = next(n for n in archive.namelist() if n.endswith("_schools.csv"))
    text = archive.read(name).decode("utf-8-sig")
    header = text.splitlines()[0].split(",")
    # Every column, in the table's own order — the mixin's `id`/timestamps
    # sort last, so don't assume a leading `id`.
    assert set(header) >= {"id", "school_name", "deped_school_id"}


def test_the_manifest_says_auth_accounts_are_not_included(session):
    """The most dangerous misunderstanding about this file is thinking it
    is a complete disaster-recovery backup — it has no logins in it."""
    data, _ = generate_backup(session)
    manifest = zipfile.ZipFile(io.BytesIO(data)).read("MANIFEST.txt").decode("utf-8")
    assert "Supabase Auth" in manifest
    assert "alembic upgrade head" in manifest


def test_backup_filename_is_timestamped():
    from datetime import datetime, timezone

    name = backup_filename(datetime(2026, 8, 11, 14, 30, tzinfo=timezone.utc))
    assert name == "fgnmhs-backup-20260811-1430.zip"
