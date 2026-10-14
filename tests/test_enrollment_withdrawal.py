"""Withdrawing an enrollment, and the record check in front of it.

A withdrawal deletes a learner's year, so the two ways to get it wrong are
both about what it touches:

- **Too loose** — undoing someone's deliberate decision on the way
  (finalized grades, a frozen record, an award override), or deleting a
  table nobody has thought about. Those block.
- **Losing history** — every deleted row must survive in the audit entry,
  and every SF2/SF4 month the learner was on must be listed to reprint.

Database tests write, flush, assert and roll back; the fixture never
commits, and `withdraw_enrollment` does not commit either.
"""

import inspect
import textwrap
from datetime import date, datetime

import pytest

from app import audit_service
from app import enrollment_withdrawal as svc
from app.admin_pages import enrollment as page
from app.enrollment_section_correction import _enrollment_fks
from app.enrollment_withdrawal import WithdrawalRefused, load_record, withdraw_enrollment
from tests.test_enrollment_section_correction import (
    _calls_under_submit_button,
    _class_day,
    _two_sections_in_one_year,
)

# --- Structure --------------------------------------------------------------


def test_every_table_pointing_at_enrollments_has_been_decided():
    """A table added later blocks withdrawal until someone decides what
    withdrawing does to it — and this test names the decision as made."""
    every = {t.name for t, _ in _enrollment_fks()}
    known = svc.DELETED | svc.DERIVED
    assert every - known == {"learner_academic_records"}, (
        "A new table points at enrollments. It blocks withdrawal until it is "
        "added to DELETED (or DERIVED) in app/enrollment_withdrawal.py."
    )


def test_every_linked_table_has_a_readable_label():
    for table, _ in _enrollment_fks():
        assert table.name in svc.LABELS


def test_the_audit_action_requires_a_reason_and_is_filterable():
    from app.admin_pages.audit_log import ALL_ACTIONS

    assert audit_service.ENROLLMENT_WITHDRAWN in audit_service.REASON_REQUIRED
    assert audit_service.ENROLLMENT_WITHDRAWN in ALL_ACTIONS


def test_withdrawal_only_runs_when_its_button_is_pressed():
    source = textwrap.dedent(inspect.getsource(page._record_panel))
    guarded, total = _calls_under_submit_button(source, "withdraw_enrollment")
    assert total == 1 and guarded == 1


def test_the_record_check_is_never_loaded_per_row():
    """The check is ~10 round trips; the roster loop may only reach it for
    the one learner whose Check record was pressed."""
    roster = inspect.getsource(page._roster_tab)
    assert "load_record" not in roster
    assert 'checked["enrollment_id"] == str(enrollment.id)' in roster


def test_only_a_registrar_or_admin_sees_withdrawal():
    assert 'has_role("SUPER_ADMIN", "REGISTRAR")' in inspect.getsource(page._record_panel)
    render = inspect.getsource(page.render)
    assert 'if adviser_user_id is None:\n            tabs.append("Withdrawn")' in render


# --- Against the database, rolled back --------------------------------------


@pytest.fixture
def session():
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _enrolled(session, section, last_name="ZZ-WITHDRAW"):
    from app.models.enums import EnrollmentStatus, Sex
    from app.models.learners import Enrollment, Learner

    learner = Learner(last_name=last_name, first_name="Test", sex=Sex.MALE, birthdate=date(2009, 1, 1))
    session.add(learner)
    session.flush()
    enrollment = Enrollment(
        learner_id=learner.id,
        school_year_id=section.school_year_id,
        grade_level_id=section.grade_level_id,
        section_id=section.id,
        enrollment_status=EnrollmentStatus.ENROLLED,
    )
    session.add(enrollment)
    session.flush()
    return learner, enrollment


def _grade(session, enrollment, section, status=None):
    from app.models.grades import TermGrade
    from app.models.subjects import SectionSubjectOffering

    offering = session.query(SectionSubjectOffering).filter_by(section_id=section.id).first()
    if offering is None:
        pytest.skip("section has no offerings")
    grade = TermGrade(
        enrollment_id=enrollment.id,
        section_subject_offering_id=offering.id,
        term_id=offering.term_id,
        official_grade=85,
    )
    if status is not None:
        grade.status = status
    session.add(grade)
    session.flush()
    return grade


def _withdraw(session, enrollment, learner, **kwargs):
    kwargs.setdefault("expected_version", enrollment.version)
    kwargs.setdefault("reason", "Not enrolled in LIS per school head")
    kwargs.setdefault("authorized_by", "Test School Head")
    kwargs.setdefault("confirm_last_name", learner.last_name)
    return withdraw_enrollment(session, enrollment_id=enrollment.id, user_id=None, **kwargs)


def test_a_withdrawal_removes_the_year_keeps_the_learner_and_copies_everything(session):
    from app.models.admin import AuditLog
    from app.models.attendance import AttendanceRecord
    from app.models.enums import EnrollmentStatus
    from app.models.grades import TermGrade
    from app.models.learners import Enrollment, Learner, LearnerMovement

    a, b = _two_sections_in_one_year(session)
    learner, enrollment = _enrolled(session, a)
    day = _class_day(session, a.school_year_id, a, b)
    session.add(AttendanceRecord(enrollment_id=enrollment.id, calendar_date_id=day.id))
    session.add(
        LearnerMovement(
            enrollment_id=enrollment.id,
            movement_type=EnrollmentStatus.DROPPED,
            effective_date=day.calendar_date,
        )
    )
    _grade(session, enrollment, a)
    enrollment_id = enrollment.id

    summary = _withdraw(session, enrollment, learner)
    session.flush()

    assert session.get(Enrollment, enrollment_id) is None
    assert session.get(Learner, learner.id) is not None
    for model in (AttendanceRecord, LearnerMovement, TermGrade):
        assert session.query(model).filter_by(enrollment_id=enrollment_id).count() == 0

    entry = session.query(AuditLog).filter_by(action="ENROLLMENT_WITHDRAWN", object_id=enrollment_id).one()
    assert entry.reason == "Not enrolled in LIS per school head"
    assert entry.new_value["authorized_by"] == "Test School Head"
    assert entry.new_value["section"] == a.name
    copied = entry.previous_value["rows"]
    assert len(copied["attendance_records"]) == 1
    assert len(copied["learner_movements"]) == 1
    assert copied["term_grades"][0]["official_grade"] == 85
    assert entry.previous_value["enrollment"]["section_id"] == str(a.id)
    month = day.calendar_date.strftime("%Y-%m")
    assert summary["sf2_sf4_months_to_reprint"] == [month]


def test_a_finalized_month_is_reopened_and_refinalized(session):
    from app.models.admin import AuditLog
    from app.models.attendance import AttendanceMonthStatus, AttendanceRecord
    from app.models.enums import FinalizationState

    a, b = _two_sections_in_one_year(session)
    learner, enrollment = _enrolled(session, a)
    day = _class_day(session, a.school_year_id, a, b)
    session.add(AttendanceRecord(enrollment_id=enrollment.id, calendar_date_id=day.id))
    month = day.calendar_date.replace(day=1)
    status = session.query(AttendanceMonthStatus).filter_by(section_id=a.id, year_month=month).one_or_none()
    if status is None:
        status = AttendanceMonthStatus(section_id=a.id, school_year_id=a.school_year_id, year_month=month)
        session.add(status)
    status.status = FinalizationState.FINALIZED
    original_at = datetime(2026, 7, 3, 2, 0)
    status.finalized_at = original_at
    original_by = status.finalized_by_user_id
    session.flush()

    summary = _withdraw(session, enrollment, learner)
    session.flush()

    assert status.status == FinalizationState.FINALIZED
    # The adviser who closed the month stays its finalizer.
    assert status.finalized_at == original_at
    assert status.finalized_by_user_id == original_by
    assert status.reopen_reason and "withdrawal" in status.reopen_reason
    assert summary["months_reopened_and_refinalized"] == [month.strftime("%Y-%m")]
    actions = [
        e.action
        for e in session.query(AuditLog)
        .filter_by(object_id=status.id)
        .order_by(AuditLog.created_at)
        .all()
    ]
    assert "ATTENDANCE_MONTH_REOPENED" in actions and "ATTENDANCE_MONTH_FINALIZED" in actions


def test_a_finalized_grade_blocks(session):
    from app.models.enums import GradeWorkflowStatus
    from app.models.learners import Enrollment

    a, _ = _two_sections_in_one_year(session)
    learner, enrollment = _enrolled(session, a)
    _grade(session, enrollment, a, status=GradeWorkflowStatus.FINALIZED)

    assert load_record(session, enrollment.id).blockers
    with pytest.raises(WithdrawalRefused, match="Term grades"):
        _withdraw(session, enrollment, learner)
    assert session.get(Enrollment, enrollment.id) is not None


def test_the_last_name_must_be_typed(session):
    a, _ = _two_sections_in_one_year(session)
    learner, enrollment = _enrolled(session, a)
    with pytest.raises(WithdrawalRefused, match="last name"):
        _withdraw(session, enrollment, learner, confirm_last_name="SOMEONE ELSE")
    _withdraw(session, enrollment, learner, confirm_last_name="  zz-withdraw ")


def test_a_stale_check_is_refused(session):
    a, _ = _two_sections_in_one_year(session)
    learner, enrollment = _enrolled(session, a)
    with pytest.raises(WithdrawalRefused, match="after the record above was shown"):
        _withdraw(session, enrollment, learner, expected_version=enrollment.version - 1)


@pytest.mark.parametrize("missing", ["reason", "authorized_by"])
def test_reason_and_authorizer_are_required(session, missing):
    a, _ = _two_sections_in_one_year(session)
    learner, enrollment = _enrolled(session, a)
    with pytest.raises(WithdrawalRefused):
        _withdraw(session, enrollment, learner, **{missing: "  "})


def test_present_after_leaving_is_flagged(session):
    from app.models.attendance import AttendanceRecord
    from app.models.enums import EnrollmentStatus
    from app.models.learners import LearnerMovement

    a, b = _two_sections_in_one_year(session)
    learner, enrollment = _enrolled(session, a)
    day = _class_day(session, a.school_year_id, a, b)
    session.add(AttendanceRecord(enrollment_id=enrollment.id, calendar_date_id=day.id))
    session.add(
        LearnerMovement(
            enrollment_id=enrollment.id,
            movement_type=EnrollmentStatus.DROPPED,
            effective_date=date(day.calendar_date.year, 1, 1),
        )
    )
    session.flush()
    record = load_record(session, enrollment.id)
    flags = [a for a in record.anomalies if "after the learner left" in a]
    assert flags and "printed form is unaffected" in flags[0]
    assert record.section == a.name


def test_a_month_seeded_after_the_learner_left_is_not_listed_for_reprint(session):
    """SF2 drops a learner from the months after they leave (§32), so a
    seeded row there is not a reason to reopen or reprint that month."""
    from app.models.attendance import AttendanceRecord
    from app.models.enums import EnrollmentStatus
    from app.models.learners import LearnerMovement

    a, b = _two_sections_in_one_year(session)
    learner, enrollment = _enrolled(session, a)
    day = _class_day(session, a.school_year_id, a, b)
    session.add(AttendanceRecord(enrollment_id=enrollment.id, calendar_date_id=day.id))
    left = day.calendar_date.replace(day=1)
    left = date(left.year - 1, 12, 31) if left.month == 1 else date(left.year, left.month - 1, 1)
    session.add(
        LearnerMovement(enrollment_id=enrollment.id, movement_type=EnrollmentStatus.DROPPED, effective_date=left)
    )
    session.flush()

    record = load_record(session, enrollment.id)
    assert day.calendar_date.replace(day=1) not in record.affected_months
    assert left.replace(day=1) in record.affected_months


def test_shifted_out_counts_as_leaving():
    """The engine's EXIT_MOVEMENTS, not a local copy without SHIFTED_OUT."""
    from app.attendance_engine import ActiveWindow
    from app.models.enums import AttendanceStatus

    window = ActiveWindow(start=None, end=date(2026, 8, 5))
    flags = svc._anomalies(None, window, [(date(2026, 8, 6), AttendanceStatus.PRESENT)])
    assert flags
    assert not hasattr(svc, "LEFT_SCHOOL")


def test_blank_grade_rows_are_counted_and_shown_as_blank(session):
    a, _ = _two_sections_in_one_year(session)
    learner, enrollment = _enrolled(session, a)
    grade = _grade(session, enrollment, a)
    grade.official_grade = None
    session.flush()
    record = load_record(session, enrollment.id)
    counts = next(c for c in record.grades_by_term.values() if c.get("blank"))
    assert counts["blank"] == 1
    term_rows = sum(v for k, v in counts.items() if k not in ("offerings", "blank"))
    assert term_rows == next(t.total for t in record.tables if t.table == "term_grades")


def test_the_page_guards_the_version_it_showed_and_catches_database_errors():
    source = inspect.getsource(page._record_panel)
    assert source.index('shown_version = checked["version"]') < source.index(
        'checked["version"] = record.enrollment_version'
    )
    assert "expected_version=shown_version" in source
    assert "except SQLAlchemyError" in source


class _Admin:
    id = None

    def has_role(self, *codes):
        return True


_RENDER = """
import streamlit as st
from app.admin_pages import enrollment as page

s, e = st.session_state["db"], st.session_state["enr"]
page._record_panel(s, e, {"enrollment_id": str(e.id), "version": e.version}, st.session_state["user"], "p")
st.session_state["withdraw"](s)
page._withdrawn_tab(s)
"""


def test_the_panel_and_the_withdrawn_tab_render(session):
    """Through Streamlit's own runtime — neither can be reached without an
    account holding the right role. No browser (see test_add_form_reset)."""
    from streamlit.testing.v1 import AppTest

    a, _ = _two_sections_in_one_year(session)
    learner, enrollment = _enrolled(session, a)
    if not svc.authorizer_options(session):
        pytest.skip("no school head named")
    done = []

    def withdraw_once(s):
        if not done:
            done.append(_withdraw(s, enrollment, learner))

    at = AppTest.from_string(_RENDER, default_timeout=120)
    at.session_state["db"] = session
    at.session_state["enr"] = enrollment
    at.session_state["user"] = _Admin()
    at.session_state["withdraw"] = withdraw_once
    at.run()

    assert not at.exception, [x.value for x in at.exception]
    assert any(a.name in m.value for m in at.markdown)
    assert [b.label for b in at.button][:2] == ["Close record", "Withdraw enrollment"]
    assert any("ZZ-WITHDRAW" in str(df.value.values) for df in at.dataframe)


def test_the_school_info_head_is_offered_first(session):
    from app.models.organization import School

    school = session.query(School).first()
    if school is None:
        pytest.skip("no School Info row")
    assert svc.authorizer_options(session)[0] == school.school_head_name.strip()
