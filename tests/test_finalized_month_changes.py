"""§33, 2026-10-17: a calendar day flipped, or a movement logged or removed,
after a month was finalized. The month is flagged for reprint, or reopened
when the change leaves class days nobody marked.

The pure rules are tested without a database. The rest write, flush,
assert and roll back — nothing commits.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.attendance_engine import ActiveWindow, Movement
from app.attendance_service import reprint_cause, reprint_notice, reprint_pending
from app.finalized_month_changes import (
    Outcome,
    _widened,
    months_touched_by_movements,
    movement_note,
    removal_note,
)
from app.models.enums import EnrollmentStatus, FinalizationState

YEAR_START = date(2026, 6, 8)
YEAR_END = date(2027, 4, 9)
OPEN_WINDOW = ActiveWindow(start=YEAR_START, end=None)


def _months(*pairs):
    return {date(y, m, 1) for y, m in pairs}


def _span(first, last):
    """Every first-of-month from `first` to `last` (year, month) inclusive."""
    months, (y, m) = set(), first
    while (y, m) <= last:
        months.add(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


# --- Which months a movement touches -----------------------------------------


def test_an_exit_touches_its_month_and_every_month_after():
    """Transferred Out on 12 September: still on September's SF2 with a
    remark, off October's onwards."""
    after = ActiveWindow(start=YEAR_START, end=date(2026, 9, 12))
    touched = months_touched_by_movements(
        OPEN_WINDOW, after, [date(2026, 9, 12)], YEAR_START, YEAR_END
    )
    assert touched == _span((2026, 9), (2027, 4))


def test_an_entry_touches_its_month_and_every_month_before():
    after = ActiveWindow(start=date(2026, 9, 10), end=None)
    touched = months_touched_by_movements(
        OPEN_WINDOW, after, [date(2026, 9, 10)], YEAR_START, YEAR_END
    )
    assert touched == _span((2026, 6), (2026, 9))


def test_a_movement_that_moves_no_edge_touches_only_its_own_month():
    """A second, later exit: the earliest exit still ends the window, but
    the new row's remark and SF2/SF4 counts land in its own month."""
    window = ActiveWindow(start=YEAR_START, end=date(2026, 9, 12))
    touched = months_touched_by_movements(
        window, window, [date(2026, 11, 3)], YEAR_START, YEAR_END
    )
    assert touched == _months((2026, 11))


def test_removing_an_exit_touches_the_same_months_as_logging_it():
    before = ActiveWindow(start=YEAR_START, end=date(2026, 9, 12))
    touched = months_touched_by_movements(
        before, OPEN_WINDOW, [date(2026, 9, 12)], YEAR_START, YEAR_END
    )
    assert touched == _span((2026, 9), (2027, 4))


def test_months_outside_the_school_year_are_dropped():
    touched = months_touched_by_movements(
        OPEN_WINDOW, OPEN_WINDOW, [date(2027, 6, 1)], YEAR_START, YEAR_END
    )
    assert touched == set()


def test_only_a_removal_can_widen_the_window():
    """Logging a movement keeps the latest entry and the earliest exit, so
    it can only narrow the window; reopening for unmarked days is a removal
    question only."""
    narrowed = ActiveWindow(start=YEAR_START, end=date(2026, 9, 12))
    assert _widened(narrowed, OPEN_WINDOW)
    assert not _widened(OPEN_WINDOW, narrowed)
    later_start = ActiveWindow(start=date(2026, 9, 10), end=None)
    assert _widened(later_start, OPEN_WINDOW)
    assert not _widened(OPEN_WINDOW, later_start)
    assert not _widened(OPEN_WINDOW, OPEN_WINDOW)


# --- The reprint flag, now with two causes ------------------------------------

T0 = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)


def _status(state=FinalizationState.FINALIZED, **kwargs):
    fields = dict(
        status=state, version=1, reopened_by_user_id=None, reopened_at=None,
        reopen_reason=None, reprint_acknowledged_at=None,
        reprint_acknowledged_by_user_id=None, changed_after_finalize_at=None,
        changed_after_finalize_by_user_id=None, change_after_finalize_note=None,
    )
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def test_a_change_after_finalizing_is_pending_until_acknowledged():
    status = _status(changed_after_finalize_at=T0, change_after_finalize_note="Sep 18 suspended.")
    assert reprint_pending(status)
    status.reprint_acknowledged_at = T0 + timedelta(minutes=5)
    assert not reprint_pending(status)


def test_a_change_after_an_acknowledged_reopen_revives_the_flag():
    status = _status(
        reopened_at=T0,
        reprint_acknowledged_at=T0 + timedelta(hours=1),
        changed_after_finalize_at=T0 + timedelta(hours=2),
        change_after_finalize_note="Movement logged.",
    )
    assert reprint_pending(status)
    assert not reprint_cause(status).reopened


def test_the_notice_names_the_latest_cause():
    status = _status(
        reopened_at=T0,
        reopen_reason="Marked a learner absent by mistake",
        changed_after_finalize_at=T0 + timedelta(hours=2),
        change_after_finalize_note="Sep 18, 2026 is no longer a class day on the Academic Calendar.",
    )
    notice = reprint_notice(status, "Ana Admin")
    assert "Changed after it was finalized" in notice
    assert "Sep 18, 2026 is no longer a class day" in notice
    assert "Ana Admin" in notice
    assert "absent by mistake" not in notice

    status.reopened_at = T0 + timedelta(hours=3)
    notice = reprint_notice(status, "Ana Admin")
    assert notice.startswith("Reopened")
    assert "absent by mistake" in notice


def test_a_naive_and_an_aware_timestamp_compare():
    """Read back from Postgres the columns are naive; set in this session
    they are aware. The latest-cause choice must cope with one of each."""
    status = _status(
        reopened_at=datetime(2026, 10, 15, 2, 0),
        changed_after_finalize_at=T0 + timedelta(hours=1),
    )
    assert not reprint_cause(status).reopened


def test_nothing_finalized_means_nothing_to_say():
    assert Outcome().message() is None
    assert "reprint" in Outcome(flagged=["A — September 2026"]).message()
    assert "reopened" in Outcome(reopened=["A — September 2026"]).message()


def test_the_movement_note_reads_as_a_sentence():
    learner = SimpleNamespace(last_name="DELA CRUZ", first_name="JUAN")
    note = movement_note("logged", learner, EnrollmentStatus.TRANSFERRED_OUT, date(2026, 9, 12))
    assert note == "Movement logged: DELA CRUZ, JUAN — Transferred Out, effective Sep 12, 2026."


def test_both_pages_report_what_the_change_did():
    """The calendar and movement writers call the shared rule rather than
    each deciding which months matter."""
    import inspect

    from app.admin_pages import academic_calendar, enrollment

    assert "apply_calendar_flip(" in inspect.getsource(academic_calendar._month_editor)
    source = inspect.getsource(enrollment)
    assert source.count("apply_movement_change(") == 2, "log and remove both"


# --- Against the database (rolled back) ---------------------------------------


@pytest.fixture
def db_session():
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _last_month_with_class_days(session, school_year_id):
    from app.attendance_service import months_with_class_days

    months = months_with_class_days(session, school_year_id)
    assert months, "needs a generated academic calendar"
    year, month = months[-1]
    return date(year, month, 1)


def _finalized(session, section, year_month):
    from app.models.attendance import AttendanceMonthStatus

    existing = (
        session.query(AttendanceMonthStatus)
        .filter_by(section_id=section.id, year_month=year_month)
        .one_or_none()
    )
    if existing is not None:
        pytest.skip(f"{section.name} already has a status row for {year_month:%Y-%m}")
    status = AttendanceMonthStatus(
        section_id=section.id,
        school_year_id=section.school_year_id,
        year_month=year_month,
        status=FinalizationState.FINALIZED,
        finalized_at=datetime(2026, 10, 1),
    )
    session.add(status)
    return status


def _two_sections_with_learners(session):
    from sqlalchemy import func

    from app.models.academic_structure import Section
    from app.models.learners import Enrollment

    rows = (
        session.query(Section)
        .join(Enrollment, Enrollment.section_id == Section.id)
        .group_by(Section.id)
        .having(func.count(Enrollment.id) > 0)
        .order_by(Section.name)
        .limit(2)
        .all()
    )
    if len(rows) < 2:
        pytest.skip("needs two sections with enrolled learners")
    return rows


def _day_in(session, school_year_id, year_month, is_class_day):
    from app.models.attendance import AcademicCalendarDate

    day = (
        session.query(AcademicCalendarDate)
        .filter(
            AcademicCalendarDate.school_year_id == school_year_id,
            AcademicCalendarDate.calendar_date >= year_month,
            AcademicCalendarDate.calendar_date < date(
                year_month.year + year_month.month // 12, year_month.month % 12 + 1, 1
            ),
            AcademicCalendarDate.is_default_class_day.is_(is_class_day),
        )
        .order_by(AcademicCalendarDate.calendar_date)
        .first()
    )
    if day is None:
        pytest.skip(f"no {'class' if is_class_day else 'non-class'} day in {year_month:%Y-%m}")
    return day


def _names(labels):
    return {label.split(" — ")[0] for label in labels}


def _audit_actions(session, statuses):
    from app.models.admin import AuditLog

    session.flush()
    return {
        (row.object_id, row.action)
        for row in session.query(AuditLog).filter(
            AuditLog.object_id.in_([s.id for s in statuses])
        )
    }


def test_a_suspension_flags_every_finalized_section_and_reopens_none(db_session):
    from app import audit_service
    from app.finalized_month_changes import apply_calendar_flip

    first, second = _two_sections_with_learners(db_session)
    year_month = _last_month_with_class_days(db_session, first.school_year_id)
    statuses = [_finalized(db_session, s, year_month) for s in (first, second)]
    day = _day_in(db_session, first.school_year_id, year_month, is_class_day=True)
    day.is_default_class_day = False

    outcome = apply_calendar_flip(db_session, day, None, "Typhoon suspension")

    assert {first.name, second.name} <= _names(outcome.flagged)
    assert not outcome.reopened
    for status in statuses:
        assert status.status == FinalizationState.FINALIZED
        assert reprint_pending(status)
        assert "no longer a class day" in status.change_after_finalize_note
        assert "Typhoon suspension" in status.change_after_finalize_note
    assert _audit_actions(db_session, statuses) == {
        (s.id, audit_service.ATTENDANCE_CHANGED_AFTER_FINALIZE) for s in statuses
    }


def test_a_make_up_day_reopens_a_section_with_unmarked_learners_only(db_session):
    """One section has every learner marked on the new day (a day unticked
    and ticked again keeps its marks): flagged. The other has none: reopened,
    because SF2 would print the blank day as present."""
    from app import audit_service
    from app.finalized_month_changes import apply_calendar_flip
    from app.models.attendance import AttendanceRecord
    from app.models.learners import Enrollment

    marked, unmarked = _two_sections_with_learners(db_session)
    year_month = _last_month_with_class_days(db_session, marked.school_year_id)
    statuses = {s.id: _finalized(db_session, s, year_month) for s in (marked, unmarked)}
    day = _day_in(db_session, marked.school_year_id, year_month, is_class_day=False)
    day.is_default_class_day = True
    for enrollment in db_session.query(Enrollment).filter_by(
        section_id=marked.id, school_year_id=marked.school_year_id
    ):
        db_session.add(AttendanceRecord(enrollment_id=enrollment.id, calendar_date_id=day.id))
    db_session.flush()

    outcome = apply_calendar_flip(db_session, day, None, "Make-up class")

    assert unmarked.name in _names(outcome.reopened)
    assert marked.name in _names(outcome.flagged)
    reopened, flagged = statuses[unmarked.id], statuses[marked.id]
    assert reopened.status == FinalizationState.OPEN
    assert "Reopened automatically" in reopened.reopen_reason
    assert reprint_pending(reopened)
    assert flagged.status == FinalizationState.FINALIZED
    assert reprint_pending(flagged)
    assert _audit_actions(db_session, statuses.values()) == {
        (reopened.id, audit_service.ATTENDANCE_MONTH_REOPENED),
        (flagged.id, audit_service.ATTENDANCE_CHANGED_AFTER_FINALIZE),
    }


def test_a_calendar_flip_costs_the_same_queries_for_any_number_of_sections(db_session):
    from sqlalchemy import event

    from app.database import engine
    from app.finalized_month_changes import apply_calendar_flip

    sections = _two_sections_with_learners(db_session)
    year_month = _last_month_with_class_days(db_session, sections[0].school_year_id)
    for section in sections:
        _finalized(db_session, section, year_month)
    day = _day_in(db_session, sections[0].school_year_id, year_month, is_class_day=False)
    day.is_default_class_day = True
    db_session.flush()

    statements = []
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        apply_calendar_flip(db_session, day, None, "Make-up class")
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    # statuses, enrollments, movements, records, school year, section names —
    # none of them per section.
    assert len(statements) <= 8, statements


def test_logging_an_exit_flags_its_month_and_the_finalized_months_after(db_session):
    from app.finalized_month_changes import apply_movement_change, lock_movements
    from app.models.learners import Enrollment, LearnerMovement

    section, _ = _two_sections_with_learners(db_session)
    enrollment = (
        db_session.query(Enrollment)
        .filter_by(section_id=section.id, school_year_id=section.school_year_id)
        .first()
    )
    last = _last_month_with_class_days(db_session, section.school_year_id)
    previous = date(last.year - (last.month == 1), (last.month - 2) % 12 + 1, 1)
    statuses = [_finalized(db_session, section, m) for m in (previous, last)]
    db_session.flush()

    exit_movement = Movement(EnrollmentStatus.TRANSFERRED_OUT, previous + timedelta(days=3))
    before = lock_movements(db_session, enrollment)
    db_session.add(
        LearnerMovement(
            enrollment_id=enrollment.id,
            movement_type=exit_movement.movement_type,
            effective_date=exit_movement.effective_date,
        )
    )
    db_session.flush()
    outcome = apply_movement_change(
        db_session, enrollment, before=before, changed=[exit_movement],
        user_id=None, note="Movement logged: TEST — Transferred Out.",
    )

    assert len(outcome.flagged) == 2 and not outcome.reopened
    assert all(s.status == FinalizationState.FINALIZED and reprint_pending(s) for s in statuses)


def test_removing_an_exit_reopens_a_month_the_learner_has_no_marks_in(db_session):
    from app.finalized_month_changes import apply_movement_change, lock_movements
    from app.models.attendance import AttendanceRecord
    from app.models.learners import Enrollment, LearnerMovement

    from app.models.attendance import AcademicCalendarDate

    section, _ = _two_sections_with_learners(db_session)
    year_month = _last_month_with_class_days(db_session, section.school_year_id)
    enrollment = (
        db_session.query(Enrollment)
        .filter_by(section_id=section.id, school_year_id=section.school_year_id)
        .first()
    )
    # The year's last month is in the future, so nothing is marked in it yet.
    if (
        db_session.query(AttendanceRecord.id)
        .join(AcademicCalendarDate, AcademicCalendarDate.id == AttendanceRecord.calendar_date_id)
        .filter(
            AttendanceRecord.enrollment_id == enrollment.id,
            AcademicCalendarDate.calendar_date >= year_month,
        )
        .first()
    ):
        pytest.skip(f"the learner already has marks in {year_month:%Y-%m}")
    status = _finalized(db_session, section, year_month)
    db_session.flush()
    # Off since before this month; removing the exit puts them back on it,
    # and nobody marked them there.
    row = LearnerMovement(
        enrollment_id=enrollment.id,
        movement_type=EnrollmentStatus.DROPPED,
        effective_date=year_month - timedelta(days=40),
    )
    db_session.add(row)
    db_session.flush()
    before = lock_movements(db_session, enrollment)
    db_session.delete(row)
    db_session.flush()

    outcome = apply_movement_change(
        db_session, enrollment, before=before, changed=[row],
        user_id=None, note="Movement removed: TEST — Dropped.",
    )

    assert outcome.reopened and status.status == FinalizationState.OPEN
    assert reprint_pending(status)


def test_a_month_that_is_not_finalized_is_left_alone(db_session):
    from app.finalized_month_changes import apply_calendar_flip

    section, _ = _two_sections_with_learners(db_session)
    year_month = _last_month_with_class_days(db_session, section.school_year_id)
    status = _finalized(db_session, section, year_month)
    status.status = FinalizationState.OPEN
    day = _day_in(db_session, section.school_year_id, year_month, is_class_day=True)
    day.is_default_class_day = False
    db_session.flush()

    outcome = apply_calendar_flip(db_session, day, None, "Typhoon suspension")

    assert section.name not in _names(outcome.flagged + outcome.reopened)
    assert status.changed_after_finalize_at is None


def test_the_flash_names_a_few_sections_and_counts_the_rest():
    labels = [f"SEC{i} — September 2026" for i in range(45)]
    message = Outcome(flagged=labels).message()
    assert "45 month(s)" in message and "and 42 more" in message
    assert "SEC3" not in message and len(message) < 400


def test_a_long_removal_cannot_overflow_the_automatic_reopen_reason():
    """Seven ticked movements used to make the composed reopen reason
    exceed COMPOSED_REASON_MAX and raise ReasonInvalid out of the page."""
    from app.finalized_month_changes import NOTE_MAX_CHARS, _clip

    assert len(_clip("x" * 5000)) == NOTE_MAX_CHARS
    learner = SimpleNamespace(last_name="DELA CRUZ", first_name="JUAN")
    movements = [SimpleNamespace(movement_type=EnrollmentStatus.DROPPED, effective_date=date(2026, 9, 1))] * 9
    note = removal_note(learner, movements, "Entered twice by mistake.")
    assert note.startswith("9 movements removed") and note.endswith("Entered twice by mistake.")


def test_a_single_removal_note_carries_the_typed_reason():
    learner = SimpleNamespace(last_name="DELA CRUZ", first_name="JUAN")
    m = SimpleNamespace(movement_type=EnrollmentStatus.DROPPED, effective_date=date(2026, 9, 1))
    assert "Reason: Duplicate entry" in removal_note(learner, [m], "Duplicate entry")


def test_only_the_shared_rule_writes_movements_and_class_days():
    """The reprint flag only works if every writer of a movement or a
    class-day flag goes through app/finalized_month_changes.py. This fails
    on a new writer that does not, the way tests/test_model_kwargs.py fails
    on a bad constructor keyword."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    # generate_calendar only creates days that do not exist yet, so no
    # finalized month can hold a mark on them.
    created_only = {"app/attendance_service.py", "app/attendance_engine.py"}
    for folder in ("app", "scripts"):
        for path in sorted((root / folder).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel.startswith("app/models/") or rel in created_only:
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            makes_movement = any(
                isinstance(n, ast.Call) and getattr(n.func, "id", None) == "LearnerMovement"
                for n in ast.walk(tree)
            )
            flips_day = any(
                isinstance(t, ast.Attribute) and t.attr == "is_default_class_day"
                for n in ast.walk(tree)
                if isinstance(n, ast.Assign)
                for t in n.targets
            )
            if makes_movement:
                assert "apply_movement_change(" in source, f"{rel} logs movements without it"
            if flips_day:
                assert "apply_calendar_flip(" in source, f"{rel} flips class days without it"
