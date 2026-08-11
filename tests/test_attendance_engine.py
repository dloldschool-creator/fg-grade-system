"""Tests for app/attendance_engine.py — the §28-33 calendar/attendance
rules, incl. master-spec.md §68 Test D (learner movement and SF2
visibility)."""

from datetime import date

import pytest

from app.attendance_engine import (
    ActiveWindow,
    Movement,
    appears_in_month,
    assign_class_day_sequence,
    compute_active_window,
    easter_sunday,
    eligible_class_days,
    generate_calendar_days,
    philippine_regular_holidays,
    summarize_attendance,
)
from app.models.enums import AttendanceStatus, EnrollmentStatus

# The seeded SY 2026-2027 (app/seed.py) — reused across calendar tests.
SY_START = date(2026, 6, 8)
SY_END = date(2027, 4, 8)
TERM_RANGES = [
    (1, date(2026, 6, 8), date(2026, 9, 15)),
    (2, date(2026, 9, 16), date(2026, 12, 18)),
    (3, date(2027, 1, 4), date(2027, 4, 8)),
]

PRESENT = AttendanceStatus.PRESENT
ABSENT = AttendanceStatus.ABSENT
LATE = AttendanceStatus.LATE
CUTTING = AttendanceStatus.CUTTING


def _sy_days():
    holidays = {
        **philippine_regular_holidays(2026),
        **philippine_regular_holidays(2027),
    }
    return generate_calendar_days(
        start_date=SY_START, end_date=SY_END, term_ranges=TERM_RANGES, holidays=holidays
    )


# --------------------------------------------------------------------------
# Calendar generation (§28, §29)
# --------------------------------------------------------------------------


def test_national_heroes_day_is_last_monday_of_august():
    holidays = philippine_regular_holidays(2026)
    assert holidays[date(2026, 8, 31)] == "National Heroes Day"
    assert date(2026, 8, 31).weekday() == 0
    # 2027's last Monday is a different date — confirms it's computed, not
    # hardcoded.
    assert philippine_regular_holidays(2027)[date(2027, 8, 30)] == "National Heroes Day"


@pytest.mark.parametrize(
    "year, expected",
    [
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2038, date(2038, 4, 25)),  # latest-possible-Easter edge case
    ],
)
def test_easter_sunday_matches_known_dates(year, expected):
    assert easter_sunday(year) == expected
    assert easter_sunday(year).weekday() == 6  # always a Sunday


def test_holy_week_is_derived_from_easter():
    """Maundy Thursday and Good Friday are movable but exactly
    computable, unlike Eid — which is why these two are auto-marked and
    Eid isn't."""
    holidays = philippine_regular_holidays(2027)
    assert holidays[date(2027, 3, 25)] == "Maundy Thursday"
    assert holidays[date(2027, 3, 26)] == "Good Friday"
    # A different year moves them, proving they're computed not hardcoded.
    holidays_2026 = philippine_regular_holidays(2026)
    assert holidays_2026[date(2026, 4, 2)] == "Maundy Thursday"
    assert holidays_2026[date(2026, 4, 3)] == "Good Friday"


def test_lunar_and_proclaimed_holidays_are_never_guessed():
    """Guard against someone later 'helpfully' hardcoding an Eid or
    Chinese New Year date — a wrong guess silently changes every
    learner's eligible class-day count."""
    holidays = philippine_regular_holidays(2027)
    assert date(2027, 2, 6) not in holidays  # Chinese New Year 2027
    assert date(2027, 2, 25) not in holidays  # EDSA anniversary
    assert date(2027, 11, 2) not in holidays  # All Souls' Day (special, not regular)


def test_weekends_and_holidays_are_not_class_days():
    by_date = {d.calendar_date: d for d in _sy_days()}
    assert by_date[date(2026, 6, 13)].is_default_class_day is False  # Saturday
    assert by_date[date(2026, 6, 14)].is_default_class_day is False  # Sunday
    assert by_date[date(2026, 6, 12)].is_default_class_day is False  # Independence Day
    assert by_date[date(2026, 6, 12)].note == "Independence Day"
    assert by_date[date(2026, 6, 11)].is_default_class_day is True  # ordinary Thursday


def test_september_split_assigns_term_per_date_not_per_month():
    """§29 — the whole point of storing term_id per date."""
    by_date = {d.calendar_date: d for d in _sy_days()}
    assert by_date[date(2026, 9, 15)].term_number == 1
    assert by_date[date(2026, 9, 16)].term_number == 2


def test_dates_outside_every_term_are_not_class_days():
    """The Christmas break falls between Term 2's end and Term 3's start,
    so it belongs to no term and generates no class days."""
    by_date = {d.calendar_date: d for d in _sy_days()}
    gap_day = by_date[date(2026, 12, 23)]  # a Wednesday, but between terms
    assert gap_day.term_number is None
    assert gap_day.is_default_class_day is False


@pytest.mark.parametrize(
    "year, month, expected",
    [
        (2026, 6, 16),
        (2026, 7, 23),
        (2026, 8, 19),
        (2026, 9, 22),
        (2026, 10, 22),
        (2027, 1, 20),
        (2027, 2, 20),
        (2027, 3, 21),  # only correct once Holy Week is auto-marked
        (2027, 4, 6),
    ],
)
def test_generated_class_days_match_the_workbook_counts(year, month, expected):
    """§28 lists the workbook's monthly instructional-day counts.
    Generation lands on them unaided for these months. November and
    December are deliberately absent: their remaining gap is All Souls'
    Day and Immaculate Conception, both *special* non-working days set by
    proclamation rather than regular holidays, so the admin marks them."""
    count = sum(
        1
        for d in _sy_days()
        if d.is_default_class_day
        and d.calendar_date.year == year
        and d.calendar_date.month == month
    )
    assert count == expected


def test_class_day_sequence_skips_non_class_days():
    days = generate_calendar_days(
        start_date=date(2026, 6, 8),
        end_date=date(2026, 6, 14),
        term_ranges=TERM_RANGES,
        holidays={date(2026, 6, 12): "Independence Day"},
    )
    sequence = assign_class_day_sequence(days)
    assert sequence == {
        date(2026, 6, 8): 1,
        date(2026, 6, 9): 2,
        date(2026, 6, 10): 3,
        date(2026, 6, 11): 4,
    }
    assert date(2026, 6, 12) not in sequence  # holiday
    assert date(2026, 6, 13) not in sequence  # Saturday


# --------------------------------------------------------------------------
# Active window and SF2 visibility (§32 — spec Test D)
# --------------------------------------------------------------------------


def test_spec_test_d_transferred_out_in_september():
    """§68 Test D: Transferred Out effective September →
    appears on September SF2 with remark; not active in October."""
    window = compute_active_window(
        [Movement(EnrollmentStatus.TRANSFERRED_OUT, date(2026, 9, 12))]
    )
    assert appears_in_month(window, 2026, 9) is True
    assert appears_in_month(window, 2026, 10) is False
    # Still counted through the effective date itself, not cut off before it.
    assert window.contains(date(2026, 9, 12)) is True
    assert window.contains(date(2026, 9, 14)) is False


def test_late_enrollee_appears_from_their_effective_month_onward():
    window = compute_active_window(
        [Movement(EnrollmentStatus.LATE_ENROLLMENT, date(2026, 8, 17))]
    )
    assert appears_in_month(window, 2026, 7) is False
    assert appears_in_month(window, 2026, 8) is True
    assert appears_in_month(window, 2026, 9) is True
    assert window.contains(date(2026, 8, 14)) is False
    assert window.contains(date(2026, 8, 17)) is True


def test_no_movements_means_active_all_year():
    window = compute_active_window([], default_start=SY_START)
    assert window.end is None
    assert appears_in_month(window, 2027, 3) is True
    assert window.contains(date(2027, 3, 2)) is True
    assert window.contains(date(2026, 6, 1)) is False  # before the school year


def test_conflicting_movements_take_latest_entry_and_earliest_exit():
    window = compute_active_window(
        [
            Movement(EnrollmentStatus.TRANSFERRED_IN, date(2026, 6, 15)),
            Movement(EnrollmentStatus.TRANSFERRED_IN, date(2026, 7, 1)),
            Movement(EnrollmentStatus.DROPPED, date(2027, 1, 20)),
            Movement(EnrollmentStatus.NLS, date(2026, 11, 5)),
        ]
    )
    assert window.start == date(2026, 7, 1)
    assert window.end == date(2026, 11, 5)


def test_eligible_days_exclude_dates_outside_the_active_window():
    class_days = [date(2026, 9, d) for d in (7, 8, 9, 10, 11, 14, 15)]
    window = compute_active_window(
        [Movement(EnrollmentStatus.TRANSFERRED_OUT, date(2026, 9, 10))]
    )
    assert eligible_class_days(class_days, window) == [
        date(2026, 9, 7),
        date(2026, 9, 8),
        date(2026, 9, 9),
        date(2026, 9, 10),
    ]


# --------------------------------------------------------------------------
# Attendance counts (§31)
# --------------------------------------------------------------------------


def _open_window():
    return ActiveWindow(start=None, end=None)


def test_late_and_cutting_still_count_as_present():
    class_days = [date(2026, 7, d) for d in (6, 7, 8, 9, 10)]
    records = {
        date(2026, 7, 6): PRESENT,
        date(2026, 7, 7): LATE,
        date(2026, 7, 8): CUTTING,
        date(2026, 7, 9): ABSENT,
        date(2026, 7, 10): PRESENT,
    }
    summary = summarize_attendance(class_days, _open_window(), records)
    assert summary.eligible_days == 5
    assert summary.days_present == 4
    assert summary.days_absent == 1
    assert summary.late_count == 1
    assert summary.cutting_count == 1
    assert summary.unencoded_days == 0


def test_unencoded_day_is_not_counted_as_present_or_absent():
    """The attendance analogue of the NULL-is-not-zero grading rule, and
    what makes §33's missing-attendance check possible."""
    class_days = [date(2026, 7, d) for d in (6, 7, 8)]
    summary = summarize_attendance(
        class_days, _open_window(), {date(2026, 7, 6): PRESENT}
    )
    assert summary.days_present == 1
    assert summary.days_absent == 0
    assert summary.unencoded_days == 2


def test_five_consecutive_absences_warn_across_a_weekend():
    """The run is counted in class days, so an intervening weekend
    doesn't reset it."""
    class_days = [date(2026, 7, d) for d in (9, 10, 13, 14, 15)]  # Thu,Fri,Mon,Tue,Wed
    summary = summarize_attendance(
        class_days, _open_window(), {day: ABSENT for day in class_days}
    )
    assert summary.has_consecutive_absence_warning is True
    assert summary.consecutive_absence_runs == [(date(2026, 7, 9), date(2026, 7, 15))]


def test_four_absences_do_not_warn():
    class_days = [date(2026, 7, d) for d in (6, 7, 8, 9, 10)]
    records = {day: ABSENT for day in class_days}
    records[date(2026, 7, 8)] = PRESENT  # breaks the run
    summary = summarize_attendance(class_days, _open_window(), records)
    assert summary.has_consecutive_absence_warning is False


def test_summary_respects_the_active_window():
    """A learner transferred out mid-month isn't marked absent for the
    rest of it — the days simply aren't eligible."""
    class_days = [date(2026, 9, d) for d in (7, 8, 9, 10, 11)]
    window = compute_active_window(
        [Movement(EnrollmentStatus.TRANSFERRED_OUT, date(2026, 9, 9))]
    )
    summary = summarize_attendance(
        class_days, window, {day: PRESENT for day in class_days[:3]}
    )
    assert summary.eligible_days == 3
    assert summary.days_present == 3
    assert summary.unencoded_days == 0
    assert summary.attendance_end == date(2026, 9, 9)


# --------------------------------------------------------------------------
# Optimistic-concurrency version bumping
# --------------------------------------------------------------------------


def test_bump_version_handles_an_unflushed_row():
    """`VersionMixin` sets `default=1` as an INSERT-time default, not a
    Python attribute default, so a row that's only been session.add()-ed
    still has version None. A bare `+= 1` raised TypeError there — the
    crash hit on Attendance's "Prepare / refresh this month's sheet",
    which creates the month-status row and updates it in one go."""
    from app.attendance_service import bump_version
    from app.models.attendance import AttendanceMonthStatus

    fresh = AttendanceMonthStatus()
    assert fresh.version is None  # not 1, until the INSERT happens
    bump_version(fresh)
    assert fresh.version == 1  # a brand-new row lands on 1, not 2

    existing = AttendanceMonthStatus()
    existing.version = 4
    bump_version(existing)
    assert existing.version == 5
