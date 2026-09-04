"""DB-touching wrappers around `app/attendance_engine.py` (§28-33) —
same split as grading_engine/grading_service: every actual *rule* lives in
the engine and is unit-tested there; this module only reads and writes
rows.
"""

import calendar as _calendar
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.attendance_engine import (
    ActiveWindow,
    AttendanceSummary,
    Movement,
    appears_in_month,
    assign_class_day_sequence,
    compute_active_window,
    generate_calendar_days,
    philippine_regular_holidays,
    summarize_attendance,
)
from app.models.attendance import AcademicCalendarDate, AttendanceMonthStatus, AttendanceRecord
from app.models.enums import AttendanceStatus, FinalizationState
from app.models.learners import Enrollment, Learner, LearnerMovement
from app.models.organization import SchoolYear, Term
from app.roster_order import learner_sort_key

# §28's SY 2026-2027 workbook configuration, kept as reference targets the
# Academic Calendar page displays alongside the generated counts — NOT as
# application logic. The spec is explicit that these are initial
# configuration, not immutable code, so nothing validates against them.
WORKBOOK_CLASS_DAY_TARGETS = {
    (2026, 6): 16,
    (2026, 7): 23,
    (2026, 8): 19,
    (2026, 9): 22,
    (2026, 10): 22,
    (2026, 11): 19,
    (2026, 12): 13,
    (2027, 1): 20,
    (2027, 2): 20,
    (2027, 3): 21,
    (2027, 4): 6,
}


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


def generate_calendar(session: Session, school_year_id) -> tuple[int, int]:
    """Creates any missing `academic_calendar_dates` rows for the school
    year. Returns (created, skipped).

    Re-runnable: a date that already exists is left completely alone, so
    an admin's override (a suspension, a make-up class) is never silently
    reverted by regenerating. Removing an override means editing that date
    on the Academic Calendar page, not regenerating the year.
    """
    school_year = session.get(SchoolYear, school_year_id)
    terms = session.query(Term).filter_by(school_year_id=school_year_id).all()
    term_id_by_number = {t.term_number: t.id for t in terms}
    term_ranges = [(t.term_number, t.start_date, t.end_date) for t in terms]

    holidays: dict[date, str] = {}
    for year in range(school_year.start_date.year, school_year.end_date.year + 1):
        holidays.update(philippine_regular_holidays(year))

    specs = generate_calendar_days(
        start_date=school_year.start_date,
        end_date=school_year.end_date,
        term_ranges=term_ranges,
        holidays=holidays,
    )
    sequence = assign_class_day_sequence(specs)

    existing = {
        row.calendar_date
        for row in session.query(AcademicCalendarDate)
        .filter_by(school_year_id=school_year_id)
        .all()
    }

    created = 0
    for spec in specs:
        if spec.calendar_date in existing:
            continue
        session.add(
            AcademicCalendarDate(
                school_year_id=school_year_id,
                term_id=term_id_by_number.get(spec.term_number),
                calendar_date=spec.calendar_date,
                day_of_week=spec.day_of_week,
                is_default_class_day=spec.is_default_class_day,
                is_override=False,
                note=spec.note,
                class_day_sequence=sequence.get(spec.calendar_date),
            )
        )
        created += 1
    return created, len(existing)


def resequence_class_days(session: Session, school_year_id) -> None:
    """Renumbers `class_day_sequence` across the whole school year. Must
    be called after any override flips a date's class-day status,
    otherwise the sequence has gaps or duplicates."""
    rows = (
        session.query(AcademicCalendarDate)
        .filter_by(school_year_id=school_year_id)
        .order_by(AcademicCalendarDate.calendar_date)
        .all()
    )
    n = 0
    for row in rows:
        if row.is_default_class_day:
            n += 1
            row.class_day_sequence = n
        else:
            row.class_day_sequence = None


def class_days_in_month(session: Session, school_year_id, year: int, month: int) -> list[AcademicCalendarDate]:
    first = date(year, month, 1)
    last = date(year, month, _calendar.monthrange(year, month)[1])
    return (
        session.query(AcademicCalendarDate)
        .filter(
            AcademicCalendarDate.school_year_id == school_year_id,
            AcademicCalendarDate.is_default_class_day.is_(True),
            AcademicCalendarDate.calendar_date >= first,
            AcademicCalendarDate.calendar_date <= last,
        )
        .order_by(AcademicCalendarDate.calendar_date)
        .all()
    )


def months_with_class_days(session: Session, school_year_id) -> list[tuple[int, int]]:
    """(year, month) pairs that actually contain class days, in order —
    what the Attendance page's month picker offers."""
    rows = (
        session.query(AcademicCalendarDate)
        .filter_by(school_year_id=school_year_id, is_default_class_day=True)
        .order_by(AcademicCalendarDate.calendar_date)
        .all()
    )
    seen: list[tuple[int, int]] = []
    for row in rows:
        key = (row.calendar_date.year, row.calendar_date.month)
        if key not in seen:
            seen.append(key)
    return seen


# --------------------------------------------------------------------------
# Learner participation
# --------------------------------------------------------------------------


def active_window_for(session: Session, enrollment: Enrollment) -> ActiveWindow:
    """Reduces the enrollment's movement log to an active date range,
    defaulting the start to the school year's own start date."""
    movements = (
        session.query(LearnerMovement).filter_by(enrollment_id=enrollment.id).all()
    )
    school_year = session.get(SchoolYear, enrollment.school_year_id)
    return compute_active_window(
        [Movement(m.movement_type, m.effective_date) for m in movements],
        default_start=school_year.start_date if school_year else None,
    )


def roster_for_month(
    session: Session, section_id, school_year_id, year: int, month: int
) -> list[tuple[Enrollment, Learner, ActiveWindow]]:
    """Learners who belong on that month's sheet, sorted the way SF2 wants
    them (male then female, alphabetical within each — §34).

    Uses `appears_in_month`, not "is currently active": a learner who
    transferred out mid-month still belongs on that month's sheet with a
    remark, and only drops off the following month (§32, spec Test D).

    Batches movements and learners across the whole roster instead of
    calling `active_window_for`/`session.get(Learner, ...)` per enrollment
    — the same fix `analytics_service.attendance_risk()` already applies,
    and this function is called several times per page action (seeding,
    the grid, saving, validating), so the per-enrollment version compounds
    fast. See CLAUDE.md's Insights section for why `active_window_for` is
    avoided in a roster loop.
    """
    enrollments = (
        session.query(Enrollment)
        .filter_by(section_id=section_id, school_year_id=school_year_id)
        .all()
    )
    if not enrollments:
        return []

    enrollment_ids = [e.id for e in enrollments]
    movements: dict = {}
    for movement in (
        session.query(LearnerMovement)
        .filter(LearnerMovement.enrollment_id.in_(enrollment_ids))
        .all()
    ):
        movements.setdefault(movement.enrollment_id, []).append(movement)

    learners = {
        learner.id: learner
        for learner in session.query(Learner)
        .filter(Learner.id.in_([e.learner_id for e in enrollments]))
        .all()
    }

    school_year = session.get(SchoolYear, school_year_id)
    default_start = school_year.start_date if school_year else None

    rows = []
    for enrollment in enrollments:
        window = compute_active_window(
            [Movement(m.movement_type, m.effective_date) for m in movements.get(enrollment.id, [])],
            default_start=default_start,
        )
        if not appears_in_month(window, year, month):
            continue
        learner = learners.get(enrollment.learner_id)
        rows.append((enrollment, learner, window))
    # Not `r[1].sex.value` — the stored strings are "MALE" and "FEMALE",
    # so sorting on them alphabetically put FEMALE first and quietly
    # contradicted the docstring above.
    rows.sort(key=lambda r: learner_sort_key(r[1]))
    return rows


# --------------------------------------------------------------------------
# Attendance records
# --------------------------------------------------------------------------


def seed_month_records(
    session: Session, section_id, school_year_id, year: int, month: int, user_id
) -> int:
    """Materialises an explicit PRESENT row for every eligible
    learner/class-day pair in the month that doesn't have one yet, and
    returns how many were created.

    Why materialise rather than treat "no row" as present (which is what
    the paper form's blank cell means): §33 requires identifying *missing*
    daily attendance before finalizing, which is impossible if absent-of-
    row and present are the same thing. Creating the rows up front makes
    "not encoded" a real, detectable state — and re-running this after a
    late enrollee joins or a new class day is added backfills only the
    genuinely missing pairs.
    """
    class_days = class_days_in_month(session, school_year_id, year, month)
    if not class_days:
        return 0
    roster = roster_for_month(session, section_id, school_year_id, year, month)

    existing = {
        (row.enrollment_id, row.calendar_date_id)
        for row in session.query(AttendanceRecord)
        .filter(
            AttendanceRecord.calendar_date_id.in_([d.id for d in class_days]),
            AttendanceRecord.enrollment_id.in_([e.id for e, _, _ in roster]),
        )
        .all()
    } if roster else set()

    created = 0
    for enrollment, _, window in roster:
        for day in class_days:
            if not window.contains(day.calendar_date):
                continue
            if (enrollment.id, day.id) in existing:
                continue
            session.add(
                AttendanceRecord(
                    enrollment_id=enrollment.id,
                    calendar_date_id=day.id,
                    status=AttendanceStatus.PRESENT,
                    encoded_by_user_id=user_id,
                )
            )
            created += 1
    return created


def records_for_month(
    session: Session, enrollment_ids: list, class_day_ids: list
) -> dict[tuple, AttendanceRecord]:
    if not enrollment_ids or not class_day_ids:
        return {}
    rows = (
        session.query(AttendanceRecord)
        .filter(
            AttendanceRecord.enrollment_id.in_(enrollment_ids),
            AttendanceRecord.calendar_date_id.in_(class_day_ids),
        )
        .all()
    )
    return {(row.enrollment_id, row.calendar_date_id): row for row in rows}


def summarize_month(
    session: Session,
    enrollment: Enrollment,
    window: ActiveWindow,
    class_days: list[AcademicCalendarDate],
) -> AttendanceSummary:
    by_id = {d.id: d.calendar_date for d in class_days}
    records = records_for_month(session, [enrollment.id], list(by_id))
    statuses = {
        by_id[calendar_date_id]: record.status
        for (_, calendar_date_id), record in records.items()
    }
    return summarize_attendance([d.calendar_date for d in class_days], window, statuses)


# --------------------------------------------------------------------------
# Monthly finalization (§33)
# --------------------------------------------------------------------------


def bump_version(row) -> None:
    """Increments an optimistic-concurrency `version`, safely on a row
    that hasn't been flushed yet.

    `VersionMixin` declares `default=1`, but that's an **INSERT-time**
    default applied by SQLAlchemy during flush — not a Python attribute
    default. On a row just built by `get_or_create_month_status` and only
    `session.add()`-ed, `row.version` is still None, so a bare
    `row.version += 1` raises TypeError. Starting from 0 leaves a brand-new
    row at version 1 and an existing one at n+1, which is what both cases
    should be.

    Every other `.version += 1` in the codebase runs on a row loaded from
    the database (inside an `else:` after a `one_or_none()` miss), so this
    only matters where a get-or-create result is modified in the same
    request.
    """
    row.version = (row.version or 0) + 1


def get_month_status(
    session: Session, section_id, year: int, month: int
) -> AttendanceMonthStatus | None:
    """Read-only lookup. Returns None for a month nobody has started —
    callers treat that as NOT_STARTED. Kept separate from
    `get_or_create_month_status` so simply *viewing* a month doesn't
    INSERT a row on every page render (which would leave an uncommitted
    insert open, and race two advisers into a unique violation on the
    section/year_month constraint)."""
    return (
        session.query(AttendanceMonthStatus)
        .filter_by(section_id=section_id, year_month=date(year, month, 1))
        .one_or_none()
    )


def get_or_create_month_status(
    session: Session, section_id, school_year_id, year: int, month: int
) -> AttendanceMonthStatus:
    """Use only on paths that go on to commit (prepare sheet, mark for
    review, finalize) — see `get_month_status` for the view path."""
    year_month = date(year, month, 1)
    status = get_month_status(session, section_id, year, month)
    if status is None:
        status = AttendanceMonthStatus(
            section_id=section_id,
            school_year_id=school_year_id,
            year_month=year_month,
            status=FinalizationState.NOT_STARTED,
        )
        session.add(status)
    return status


def validate_month(
    session: Session, section_id, school_year_id, year: int, month: int
) -> dict:
    """The §33 pre-finalization report. Returns blocking `problems`
    (missing attendance, impossible movement dates), non-blocking
    `warnings` (five-consecutive-absence runs), and the `totals` the spec
    wants displayed (male/female enrollment, movement summary)."""
    problems: list[str] = []
    warnings: list[str] = []

    class_days = class_days_in_month(session, school_year_id, year, month)
    if not class_days:
        problems.append(
            f"No class days on the academic calendar for {year}-{month:02d} — "
            "generate or adjust the calendar first."
        )

    roster = roster_for_month(session, section_id, school_year_id, year, month)
    school_year = session.get(SchoolYear, school_year_id)
    month_start = date(year, month, 1)
    month_end = date(year, month, _calendar.monthrange(year, month)[1])

    sex_totals: dict[str, int] = {}
    movement_totals: dict[str, int] = {}

    for enrollment, learner, window in roster:
        sex_totals[learner.sex.value] = sex_totals.get(learner.sex.value, 0) + 1

        summary = summarize_month(session, enrollment, window, class_days)
        name = f"{learner.last_name}, {learner.first_name}"
        if summary.unencoded_days:
            problems.append(
                f"{name}: {summary.unencoded_days} class day(s) with no attendance encoded."
            )
        for run_start, run_end in summary.consecutive_absence_runs:
            warnings.append(
                f"{name}: absent {run_start:%b %d} through {run_end:%b %d} "
                "(five or more consecutive class days)."
            )

        for movement in (
            session.query(LearnerMovement).filter_by(enrollment_id=enrollment.id).all()
        ):
            movement_totals[movement.movement_type.value] = (
                movement_totals.get(movement.movement_type.value, 0) + 1
            )
            # "Impossible movement dates" (§33) — outside the school year
            # entirely is a data-entry error worth blocking on.
            if school_year and not (
                school_year.start_date <= movement.effective_date <= school_year.end_date
            ):
                problems.append(
                    f"{name}: {movement.movement_type.value} effective "
                    f"{movement.effective_date:%Y-%m-%d} falls outside the school year."
                )

    return {
        "problems": problems,
        "warnings": warnings,
        "class_day_count": len(class_days),
        "sex_totals": sex_totals,
        "movement_totals": movement_totals,
        "roster_size": len(roster),
        "month_start": month_start,
        "month_end": month_end,
    }


def finalize_month(session: Session, status: AttendanceMonthStatus, user_id) -> None:
    status.status = FinalizationState.FINALIZED
    status.finalized_by_user_id = user_id
    status.finalized_at = datetime.now(timezone.utc)
    bump_version(status)


def reopen_month(
    session: Session, status: AttendanceMonthStatus, user_id, reason: str
) -> None:
    status.status = FinalizationState.OPEN
    status.reopened_by_user_id = user_id
    status.reopened_at = datetime.now(timezone.utc)
    status.reopen_reason = reason
    bump_version(status)
