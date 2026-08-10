"""Attendance and academic-calendar rules (§28-33) — pure functions, no
DB access, mirroring `app/grading_engine.py`'s split so every rule here is
unit-testable in isolation. The DB-touching wrappers live in
`app/attendance_service.py`.

The two rules most likely to be got wrong, and so the most heavily
tested:

1. **Eligible class days are per-learner, not per-section** (§31). A late
   enrollee has no eligible days before their effective date; a learner
   transferred out has none after theirs. Counting a section-wide class-day
   total against every learner inflates absences for anyone who wasn't
   there the whole month.
2. **A learner who leaves stays on the effective month's SF2** and only
   disappears the month *after* (§32, tested as spec Test D). "Not active
   any more" and "don't show them" are different questions, so they're
   two different functions here: `is_active_on` and `appears_in_month`.
"""

import calendar as _calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.models.enums import AttendanceStatus, EnrollmentStatus

# Movements that END a learner's participation, effective on the movement
# date. §32 lists exactly these four as "must remain visible in the
# effective month's SF2, then no longer appear as active".
EXIT_MOVEMENTS = frozenset(
    {
        EnrollmentStatus.TRANSFERRED_OUT,
        EnrollmentStatus.NLS,
        EnrollmentStatus.DROPPED,
        EnrollmentStatus.SHIFTED_OUT,
    }
)

# Movements that START participation partway through the year — the
# learner has no eligible class days before the effective date.
ENTRY_MOVEMENTS = frozenset(
    {
        EnrollmentStatus.LATE_ENROLLMENT,
        EnrollmentStatus.TRANSFERRED_IN,
        EnrollmentStatus.SHIFTED_IN,
    }
)

# Consecutive-absence threshold that triggers the §31 warning.
CONSECUTIVE_ABSENCE_WARNING = 5


# --------------------------------------------------------------------------
# Academic calendar generation (§28, §29)
# --------------------------------------------------------------------------


def philippine_regular_holidays(year: int) -> dict[date, str]:
    """The Philippine national **regular** holidays that can be derived
    from the year alone — the fixed-date ones, National Heroes Day (last
    Monday of August), and Maundy Thursday/Good Friday (computed from
    Easter).

    Still deliberately excluded, because they genuinely can't be derived:
    Eid'l Fitr and Eid'l Adha (set by lunar observation and proclaimed
    each year), Chinese New Year and the EDSA anniversary (special
    non-working days, not regular ones), and anything a given year's
    presidential proclamation adds, moves, or drops. Those stay for the
    admin to mark by hand on the Academic Calendar page — a wrong guess
    there silently shifts every learner's eligible class-day count.

    Note this returns holidays by the *rule*, not by that year's actual
    proclamation: a holiday falling on a weekend is returned on its real
    date and is not "moved" to a Monday, since whether it gets moved is a
    proclamation decision. It costs nothing either way — a weekend date
    is not a class day regardless.
    """
    holidays = {
        date(year, 1, 1): "New Year's Day",
        date(year, 4, 9): "Araw ng Kagitingan",
        date(year, 5, 1): "Labor Day",
        date(year, 6, 12): "Independence Day",
        date(year, 8, 21): "Ninoy Aquino Day",
        date(year, 11, 1): "All Saints' Day",
        date(year, 11, 30): "Bonifacio Day",
        date(year, 12, 25): "Christmas Day",
        date(year, 12, 30): "Rizal Day",
    }
    holidays[_last_monday_of_august(year)] = "National Heroes Day"

    # Holy Week. Black Saturday is a special non-working day rather than a
    # regular holiday, and always lands on a Saturday, so it would never
    # change a class-day count — only these two can.
    easter = easter_sunday(year)
    holidays[easter - timedelta(days=3)] = "Maundy Thursday"
    holidays[easter - timedelta(days=2)] = "Good Friday"
    return holidays


def easter_sunday(year: int) -> date:
    """Western (Gregorian) Easter, via the anonymous Gregorian algorithm
    — fully deterministic arithmetic, no table and no lookup. Holy Week
    is "movable" only in the sense that it isn't a fixed calendar date;
    it's still exactly computable, which is why Maundy Thursday and Good
    Friday can be derived while Eid can't."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _last_monday_of_august(year: int) -> date:
    last_day = _calendar.monthrange(year, 8)[1]
    d = date(year, 8, last_day)
    return d - timedelta(days=d.weekday())  # date.weekday(): Monday == 0


@dataclass
class CalendarDaySpec:
    """One generated row for `academic_calendar_dates`, before it's
    written. `term_number` is None for a date that falls in no term (e.g.
    the Christmas break gap between Term 2's end and Term 3's start)."""

    calendar_date: date
    day_of_week: int
    term_number: int | None
    is_default_class_day: bool
    note: str | None = None


def generate_calendar_days(
    *,
    start_date: date,
    end_date: date,
    term_ranges: list[tuple[int, date, date]],
    holidays: dict[date, str] | None = None,
) -> list[CalendarDaySpec]:
    """Builds one spec per date from `start_date` to `end_date` inclusive.

    A date is a default class day when it's a weekday, falls inside a
    term, and isn't a holiday. `term_ranges` is [(term_number, start,
    end)] — the term is looked up per date rather than derived from the
    month, which is what makes the September Term 1/Term 2 split
    representable (§29).
    """
    holidays = holidays or {}
    days: list[CalendarDaySpec] = []
    current = start_date
    while current <= end_date:
        term_number = next(
            (n for n, t_start, t_end in term_ranges if t_start <= current <= t_end), None
        )
        is_weekday = current.weekday() < 5
        holiday_name = holidays.get(current)
        days.append(
            CalendarDaySpec(
                calendar_date=current,
                day_of_week=current.isoweekday(),
                term_number=term_number,
                is_default_class_day=(
                    is_weekday and term_number is not None and holiday_name is None
                ),
                note=holiday_name,
            )
        )
        current += timedelta(days=1)
    return days


def assign_class_day_sequence(days: list[CalendarDaySpec]) -> dict[date, int]:
    """1-based running count of class days across the school year, used
    for the SF2 day columns. Non-class days get no number at all rather
    than repeating the previous one."""
    sequence: dict[date, int] = {}
    n = 0
    for day in sorted(days, key=lambda d: d.calendar_date):
        if day.is_default_class_day:
            n += 1
            sequence[day.calendar_date] = n
    return sequence


# --------------------------------------------------------------------------
# Learner participation window (§31 "active", §32 SF2 visibility)
# --------------------------------------------------------------------------


@dataclass
class Movement:
    """The subset of a `learner_movements` row these rules need."""

    movement_type: EnrollmentStatus
    effective_date: date


@dataclass
class ActiveWindow:
    """Half-open in spirit but inclusive in both bounds: a learner is
    active on `start` and still active on `end`. `end` is None when they
    never left."""

    start: date | None
    end: date | None

    def contains(self, day: date) -> bool:
        if self.start is not None and day < self.start:
            return False
        if self.end is not None and day > self.end:
            return False
        return True


def compute_active_window(
    movements: list[Movement], *, default_start: date | None = None
) -> ActiveWindow:
    """Reduces a learner's movement log to the date range they count for
    attendance.

    The *latest* entry movement wins as the start and the *earliest* exit
    movement wins as the end — a learner who transfers in twice (a data
    error, but it happens) shouldn't get credit for days before the
    correction, and one with two exit rows stops at the first.

    An exit is inclusive of its effective date: a learner transferred out
    effective the 15th is still counted as present-or-absent through the
    15th. That matches §32's "remains visible in the effective month".
    """
    starts = [m.effective_date for m in movements if m.movement_type in ENTRY_MOVEMENTS]
    ends = [m.effective_date for m in movements if m.movement_type in EXIT_MOVEMENTS]
    return ActiveWindow(
        start=max(starts) if starts else default_start,
        end=min(ends) if ends else None,
    )


def is_active_on(window: ActiveWindow, day: date) -> bool:
    return window.contains(day)


def appears_in_month(window: ActiveWindow, year: int, month: int) -> bool:
    """Whether the learner shows on that month's SF2 at all — which is a
    *wider* question than being active (§32). A learner who transferred
    out on 12 September is not active for most of September but still
    appears on the September SF2 with a remark; they drop off from
    October. This is spec Test D."""
    month_start = date(year, month, 1)
    month_end = date(year, month, _calendar.monthrange(year, month)[1])
    if window.start is not None and window.start > month_end:
        return False
    if window.end is not None and window.end < month_start:
        return False
    return True


def eligible_class_days(
    class_days: list[date], window: ActiveWindow
) -> list[date]:
    """The class days that actually count against this learner (§31):
    weekends/holidays/non-class days are already excluded by `class_days`
    itself, and this drops dates outside their active window."""
    return [day for day in class_days if window.contains(day)]


# --------------------------------------------------------------------------
# Attendance counts (§31)
# --------------------------------------------------------------------------


@dataclass
class AttendanceSummary:
    eligible_days: int = 0
    days_present: int = 0
    days_absent: int = 0
    late_count: int = 0
    cutting_count: int = 0
    unencoded_days: int = 0
    attendance_start: date | None = None
    attendance_end: date | None = None
    consecutive_absence_runs: list[tuple[date, date]] = field(default_factory=list)

    @property
    def has_consecutive_absence_warning(self) -> bool:
        return bool(self.consecutive_absence_runs)


def summarize_attendance(
    class_days: list[date],
    window: ActiveWindow,
    records: dict[date, AttendanceStatus],
) -> AttendanceSummary:
    """Counts one learner over one period (typically a month).

    `records` holds only the days actually encoded. An eligible day with
    no record is counted as `unencoded_days`, **not** silently as
    present — the same NULL-is-not-a-value discipline the grading side
    uses, and what makes §33's "identify missing daily attendance"
    pre-finalization check possible. (Note this differs from the *paper*
    form's convention, where a blank cell means present; the encoding UI
    materialises an explicit PRESENT row per day so blank can keep meaning
    "nobody has said yet".)

    LATE and CUTTING both still count as days present — the learner was in
    school. They're tracked as separate counters on top, not as a third
    presence state.
    """
    eligible = eligible_class_days(class_days, window)
    summary = AttendanceSummary(eligible_days=len(eligible))
    if not eligible:
        return summary

    summary.attendance_start = min(eligible)
    summary.attendance_end = max(eligible)

    absence_run_start: date | None = None
    previous_absence: date | None = None

    for day in sorted(eligible):
        status = records.get(day)
        if status is None:
            summary.unencoded_days += 1
        elif status == AttendanceStatus.ABSENT:
            summary.days_absent += 1
        else:
            summary.days_present += 1
            if status == AttendanceStatus.LATE:
                summary.late_count += 1
            elif status == AttendanceStatus.CUTTING:
                summary.cutting_count += 1

        # A run is consecutive in *class days*, not calendar days — a
        # weekend or holiday between two absences doesn't break it, but a
        # day the learner showed up does. An unencoded day is treated as
        # breaking the run, since we don't know what happened.
        if status == AttendanceStatus.ABSENT:
            if absence_run_start is None:
                absence_run_start = day
            previous_absence = day
        else:
            _close_absence_run(summary, absence_run_start, previous_absence, eligible)
            absence_run_start = None
            previous_absence = None

    _close_absence_run(summary, absence_run_start, previous_absence, eligible)
    return summary


def _close_absence_run(
    summary: AttendanceSummary,
    run_start: date | None,
    run_end: date | None,
    eligible: list[date],
) -> None:
    if run_start is None or run_end is None:
        return
    length = sum(1 for day in eligible if run_start <= day <= run_end)
    if length >= CONSECUTIVE_ABSENCE_WARNING:
        summary.consecutive_absence_runs.append((run_start, run_end))
