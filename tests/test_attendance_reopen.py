"""§33 as amended 2026-10-15: who may reopen a finalized attendance month,
and the "SF2 may need reprinting" notice a reopened month carries.

No database: the permission rule and the notice are pure, and
`reopen_month` only sets attributes, so plain stand-ins are enough.
"""

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.attendance_service import reopen_month, reprint_notice
from app.auth import AuthUser
from app.models.enums import FinalizationState
from app.section_access import may_reopen_attendance_month

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
        reopen_reason=None, **kwargs,
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

    assert "reprint_notice_for(" in inspect.getsource(sf2.render)
    source = inspect.getsource(dashboard)
    assert "REOPENED_COLUMN: reopened" in source
    assert "r[REOPENED_COLUMN]" in source


def test_attendance_page_shows_the_reprint_flag():
    from app.admin_pages import attendance

    assert "reprint_notice_for(" in inspect.getsource(attendance._finalization_panel)
