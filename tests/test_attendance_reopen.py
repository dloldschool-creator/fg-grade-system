"""§33 as amended 2026-10-15: who may reopen a finalized attendance month,
and the "SF2 may need reprinting" notice a reopened month carries.

No database: the permission rule and the notice are pure, and
`reopen_month` only sets attributes, so plain stand-ins are enough.
"""

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.attendance_service import (
    acknowledge_reprint,
    reopen_month,
    reprint_notice,
    reprint_pending,
)
from app.auth import AuthUser
from app.models.enums import FinalizationState
from app.section_access import may_acknowledge_reprint, may_reopen_attendance_month

ADVISER_ID = uuid.uuid4()


def _user(*roles, user_id=None):
    return AuthUser(
        # str, as AuthUser.id really is — passing the UUID would hide the
        # str-vs-UUID mismatch is_advised_by exists for.
        id=str(user_id or uuid.uuid4()),
        supabase_auth_user_id="x",
        email="x@example.test",
        full_name="Test User",
        access_token="",
        refresh_token="",
        role_codes=set(roles),
    )


SECTION = SimpleNamespace(adviser_user_id=ADVISER_ID)


@pytest.mark.parametrize("role", ["SUPER_ADMIN", "REGISTRAR"])
def test_admin_and_registrar_may_reopen_any_section(role):
    assert may_reopen_attendance_month(_user(role), SECTION)


def test_adviser_may_reopen_their_own_section():
    assert may_reopen_attendance_month(_user("ADVISER", user_id=ADVISER_ID), SECTION)


def test_adviser_may_not_reopen_someone_elses_section():
    assert not may_reopen_attendance_month(_user("ADVISER"), SECTION)


def test_a_section_with_no_adviser_is_nobodys():
    orphan = SimpleNamespace(adviser_user_id=None)
    assert not may_reopen_attendance_month(_user("ADVISER", user_id=ADVISER_ID), orphan)


@pytest.mark.parametrize("role", ["SUBJECT_TEACHER", "SCHOOL_HEAD"])
def test_other_roles_may_not_reopen(role):
    # Even holding the section's adviser id, without the ADVISER role.
    assert not may_reopen_attendance_month(_user(role, user_id=ADVISER_ID), SECTION)


def test_the_attendance_page_gates_reopen_on_the_shared_rule():
    """The page used to hardcode has_role("SUPER_ADMIN"); a second copy of
    the rule there would drift from this one."""
    from app.admin_pages import attendance

    source = inspect.getsource(attendance._finalization_panel)
    assert "may_reopen_attendance_month(" in source
    assert 'has_role("SUPER_ADMIN")' not in source


class _FakeSession:
    """Records refresh() calls; the status stand-ins are plain objects."""

    def __init__(self):
        self.refreshed = []

    def refresh(self, obj, **kwargs):
        self.refreshed.append(kwargs)


def _status(state, **kwargs):
    return SimpleNamespace(
        status=state, version=1, reopened_by_user_id=None, reopened_at=None,
        reopen_reason=None, reprint_acknowledged_at=None,
        reprint_acknowledged_by_user_id=None, **kwargs,
    )


def test_reopen_sets_the_record_and_bumps_version():
    status = _status(FinalizationState.FINALIZED)
    reopen_month(_FakeSession(), status, ADVISER_ID, "Marked a learner absent by mistake")
    assert status.status == FinalizationState.OPEN
    assert status.reopened_by_user_id == ADVISER_ID
    assert status.reopened_at is not None
    assert status.reopen_reason == "Marked a learner absent by mistake"
    assert status.version == 2


@pytest.mark.parametrize(
    "state", [FinalizationState.OPEN, FinalizationState.FOR_REVIEW, FinalizationState.NOT_STARTED]
)
def test_reopening_a_month_that_is_not_finalized_is_refused(state):
    """A stale second Reopen must not overwrite the first one's reason."""
    status = _status(state)
    status.reopen_reason = "the first reason"
    with pytest.raises(ValueError):
        reopen_month(_FakeSession(), status, ADVISER_ID, "a second reason")
    assert status.reopen_reason == "the first reason"


def test_no_notice_for_a_month_never_reopened():
    assert reprint_notice(None, None) is None
    assert reprint_notice(_status(FinalizationState.FINALIZED), None) is None


def test_notice_names_who_when_and_why():
    status = _status(FinalizationState.OPEN)
    status.reopened_at = datetime(2026, 10, 15, 2, 30, tzinfo=timezone.utc)
    status.reopen_reason = "Late transferee"
    notice = reprint_notice(status, "Juan Dela Cruz")
    assert "Juan Dela Cruz" in notice
    assert "Late transferee" in notice
    assert "Oct 15, 2026" in notice
    assert "SF2" in notice


def test_notice_survives_refinalization():
    """Nothing records whether the SF2 was reprinted, so finalizing again
    must not make the flag disappear."""
    status = _status(FinalizationState.FINALIZED)
    status.reopened_at = datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert reprint_notice(status, None) is not None


def test_reopen_locks_the_row_before_checking_it():
    """Two simultaneous Reopens both see FINALIZED unless the row is locked
    and re-read first; `version` is a plain column and can't catch it."""
    session = _FakeSession()
    reopen_month(session, _status(FinalizationState.FINALIZED), ADVISER_ID, "reason")
    assert session.refreshed == [{"with_for_update": True}]


def test_withdrawal_only_reopens_months_still_finalized():
    """An adviser may reopen a month between load_record and the withdrawal's
    locked read; reopen_month would then refuse and crash the page."""
    from app import enrollment_withdrawal

    source = inspect.getsource(enrollment_withdrawal.withdraw_enrollment)
    locked = source[source.index("Reopen finalized months") :]
    locked = locked[: locked.index("with_for_update")]
    assert "AttendanceMonthStatus.status == FinalizationState.FINALIZED" in locked


def test_sf2_and_dashboard_show_the_reprint_flag():
    from app.admin_pages import dashboard, sf2

    assert "render_reprint_notice(" in inspect.getsource(sf2.render)
    source = inspect.getsource(dashboard)
    assert "reprint_pending(status)" in source
    assert "REPRINT_COLUMN: awaiting" in source
    assert "r[REPRINT_COLUMN]" in source


def test_attendance_page_shows_the_reprint_flag():
    from app.admin_pages import attendance

    assert "render_reprint_notice(" in inspect.getsource(attendance._finalization_panel)


# --- Acknowledging a reprint -------------------------------------------

T0 = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)


def _reopened(state=FinalizationState.FINALIZED, at=T0):
    status = _status(state)
    status.reopened_at = at
    return status


def test_a_reopened_month_is_pending_until_acknowledged():
    assert reprint_pending(_reopened())
    assert not reprint_pending(None)
    assert not reprint_pending(_status(FinalizationState.FINALIZED))


def test_acknowledging_clears_the_notice():
    status = _reopened()
    acknowledge_reprint(_FakeSession(), status, ADVISER_ID)
    assert status.reprint_acknowledged_by_user_id == ADVISER_ID
    assert not reprint_pending(status)
    assert reprint_notice(status, "Juan") is None
    assert status.version == 2


def test_a_second_reopen_brings_the_flag_back_without_resetting_anything():
    status = _reopened()
    acknowledge_reprint(_FakeSession(), status, ADVISER_ID)
    status.reopened_at = status.reprint_acknowledged_at + timedelta(minutes=5)
    assert reprint_pending(status)


def test_acknowledgement_compares_naive_and_aware_timestamps():
    """Postgres hands back naive values; an uncommitted write is aware."""
    status = _reopened(at=T0)
    status.reprint_acknowledged_at = (T0 + timedelta(hours=1)).replace(tzinfo=None)
    assert not reprint_pending(status)
    status.reprint_acknowledged_at = (T0 - timedelta(hours=1)).replace(tzinfo=None)
    assert reprint_pending(status)


@pytest.mark.parametrize("state", [FinalizationState.OPEN, FinalizationState.FOR_REVIEW])
def test_acknowledging_before_the_month_is_finalized_again_is_refused(state):
    status = _reopened(state)
    with pytest.raises(ValueError):
        acknowledge_reprint(_FakeSession(), status, ADVISER_ID)
    assert status.reprint_acknowledged_at is None


def test_acknowledging_a_month_with_nothing_pending_is_refused():
    with pytest.raises(ValueError):
        acknowledge_reprint(_FakeSession(), _status(FinalizationState.FINALIZED), ADVISER_ID)


def test_acknowledging_locks_the_row_first():
    session = _FakeSession()
    acknowledge_reprint(session, _reopened(), ADVISER_ID)
    assert session.refreshed == [{"with_for_update": True}]


def test_the_notice_tells_you_what_to_do_in_each_state():
    open_notice = reprint_notice(_reopened(FinalizationState.OPEN), None)
    final_notice = reprint_notice(_reopened(FinalizationState.FINALIZED), None, can_confirm=True)
    assert "finalized again" in open_notice
    assert "confirm below" in final_notice


def test_a_viewer_with_no_button_is_not_pointed_at_one():
    """A School Head sees the notice but has no confirm button; "confirm
    below" would send them looking for nothing."""
    notice = reprint_notice(_reopened(FinalizationState.FINALIZED), None, can_confirm=False)
    assert "below" not in notice
    assert "adviser or the registrar confirms" in notice


def test_acknowledging_returns_what_it_replaced_for_the_audit_entry():
    status = _reopened()
    first = acknowledge_reprint(_FakeSession(), status, ADVISER_ID)
    assert first == {
        "reprint_acknowledged_by_user_id": None,
        "reprint_acknowledged_at": None,
    }
    acked_at = status.reprint_acknowledged_at
    status.reopened_at = acked_at + timedelta(minutes=1)
    other = uuid.uuid4()
    second = acknowledge_reprint(_FakeSession(), status, other)
    assert second == {
        "reprint_acknowledged_by_user_id": ADVISER_ID,
        "reprint_acknowledged_at": acked_at,
    }


def test_the_confirm_button_records_the_old_acknowledgement():
    from app.admin_pages import _helpers

    source = inspect.getsource(_helpers.render_reprint_notice)
    assert "previous=previous" in source


def test_the_school_head_cannot_acknowledge_a_reprint():
    assert not may_acknowledge_reprint(_user("SCHOOL_HEAD"), SECTION)
    assert may_acknowledge_reprint(_user("REGISTRAR"), SECTION)
    assert may_acknowledge_reprint(_user("ADVISER", user_id=ADVISER_ID), SECTION)
    assert not may_acknowledge_reprint(_user("ADVISER"), SECTION)


def test_the_confirm_button_is_shared_and_audited():
    from app.admin_pages import _helpers

    source = inspect.getsource(_helpers.render_reprint_notice)
    assert "ATTENDANCE_REPRINT_ACKNOWLEDGED" in source
    assert "may_acknowledge_reprint(" in source
    assert "FinalizationState.FINALIZED" in source


# --- Against the real database (rolled back, never committed) -----------


@pytest.fixture
def db_session():
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _db_status(session):
    """A FINALIZED, once-reopened month in 1999 — no real month collides."""
    from datetime import date

    from app.models.academic_structure import Section
    from app.models.attendance import AttendanceMonthStatus

    section = session.query(Section).first()
    assert section is not None, "needs at least one section in the database"
    status = AttendanceMonthStatus(
        section_id=section.id,
        school_year_id=section.school_year_id,
        year_month=date(1999, 1, 1),
        status=FinalizationState.FINALIZED,
        reopened_at=datetime(1999, 2, 1, 2, 0),
    )
    session.add(status)
    session.flush()
    return status


def test_acknowledging_persists_on_a_real_row(db_session):
    status = _db_status(db_session)
    assert reprint_pending(status)
    previous = acknowledge_reprint(db_session, status, None)
    db_session.flush()
    db_session.refresh(status)
    assert previous["reprint_acknowledged_at"] is None
    assert status.reprint_acknowledged_at is not None
    assert not reprint_pending(status)


def test_a_real_second_reopen_revives_the_flag(db_session):
    status = _db_status(db_session)
    acknowledge_reprint(db_session, status, None)
    db_session.flush()
    reopen_month(db_session, status, None, "found another mistake")
    db_session.flush()
    db_session.refresh(status)
    assert status.status == FinalizationState.OPEN
    assert reprint_pending(status)


def test_the_withdrawn_tab_reads_the_live_flag(db_session):
    """Its "not yet confirmed" column follows reprint_pending, not the audit
    entry's frozen list — and a month that was never finalized at withdrawal
    (so never reopened, no flag) is listed as not finalized yet rather than
    silently omitted."""
    from datetime import date
    from types import SimpleNamespace

    from app.admin_pages.enrollment import _months_awaiting_reprint
    from app.models.attendance import AttendanceMonthStatus

    status = _db_status(db_session)  # 1999-01: FINALIZED, reopened, pending
    never_final = AttendanceMonthStatus(
        section_id=status.section_id,
        school_year_id=status.school_year_id,
        year_month=date(1999, 4, 1),
        status=FinalizationState.OPEN,
    )
    db_session.add(never_final)
    db_session.flush()
    entry = SimpleNamespace(
        id=uuid.uuid4(),
        new_value={
            "section_id": str(status.section_id),
            # 1999-03 has no status row at all: never started, owes nothing.
            "sf2_sf4_months_to_reprint": ["1999-01", "1999-03", "1999-04"],
        },
    )
    assert _months_awaiting_reprint(db_session, [entry]) == {
        entry.id: ["1999-01", "1999-04 (not finalized yet)"]
    }

    acknowledge_reprint(db_session, status, None)
    never_final.status = FinalizationState.FINALIZED
    db_session.flush()
    assert _months_awaiting_reprint(db_session, [entry]) == {entry.id: []}
    assert _months_awaiting_reprint(db_session, []) == {}


def test_a_failed_sf2_build_is_logged_with_which_form_it_was(caplog):
    import logging

    from app.admin_pages.sf2 import _deferred_builders

    build_xlsx, _ = _deferred_builders(uuid.uuid4(), uuid.uuid4(), 1999, 1)
    with caplog.at_level(logging.ERROR), pytest.raises(Exception):
        build_xlsx()
    assert any(
        "SF2 download failed" in r.getMessage() and "1999-01" in r.getMessage()
        for r in caplog.records
    )
