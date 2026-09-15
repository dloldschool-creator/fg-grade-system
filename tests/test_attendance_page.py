"""Attendance page (app/admin_pages/attendance.py) — the Finalize gate and
the "changed after last save" note.

Everything here runs against the live database and rolls back (no
separate test DB — see `_enrollments_without_attendance`'s docstring in
test_analytics_service.py). `_save_grid`'s test patches `try_commit` to a
flush, so it can exercise the real write path — including the
`AttendanceMonthStatus.last_saved_at/by` stamp the 61679070270f migration
added — without a real commit ever reaching the database.
"""

from datetime import date, datetime, timezone, timedelta

import pytest

from app import audit_service
from app.admin_pages.attendance import (
    CODE_BY_STATUS,
    CODE_BY_STATUS_VALUE,
    _blocking_reasons,
    _grid_dataframe,
    _last_attendance_change,
    _naive_utc,
    _save_grid,
)
from app.attendance_service import (
    class_days_in_month,
    get_month_status,
    months_with_class_days,
    roster_for_month,
)
from app.database import SessionLocal
from app.models.attendance import AttendanceRecord
from app.models.enums import AttendanceStatus, FinalizationState
from app.models.organization import SchoolYear


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def school_year(session):
    year = session.query(SchoolYear).order_by(SchoolYear.name.desc()).first()
    if year is None:
        pytest.skip("no school year in the database")
    return year


def _any_advised_section(session, school_year):
    from app.models.academic_structure import Section

    section = (
        session.query(Section)
        .filter(
            Section.school_year_id == school_year.id,
            Section.adviser_user_id.isnot(None),
        )
        .first()
    )
    if section is None:
        pytest.skip("no section with an adviser")
    return section


def _enrollments_without_attendance(session, school_year, section, day_ids, limit):
    """Same helper as test_analytics_service.py — see its docstring for
    why "any learner" can't be assumed unmarked on a live database."""
    from app.models.learners import Enrollment

    encoded = (
        session.query(AttendanceRecord.enrollment_id)
        .join(Enrollment, Enrollment.id == AttendanceRecord.enrollment_id)
        .filter(
            Enrollment.school_year_id == school_year.id,
            Enrollment.section_id == section.id,
            AttendanceRecord.calendar_date_id.in_(day_ids),
        )
    )
    return (
        session.query(Enrollment)
        .filter(
            Enrollment.school_year_id == school_year.id,
            Enrollment.section_id == section.id,
            ~Enrollment.id.in_(encoded),
        )
        .limit(limit)
        .all()
    )


# --- _naive_utc --------------------------------------------------------


def test_naive_utc_leaves_a_naive_value_alone():
    value = datetime(2026, 9, 15, 10, 30)
    result = _naive_utc(value)
    assert result == value
    assert result.tzinfo is None


def test_naive_utc_converts_aware_to_naive_utc():
    manila = timezone(timedelta(hours=8))
    aware = datetime(2026, 9, 15, 18, 30, tzinfo=manila)
    result = _naive_utc(aware)
    assert result.tzinfo is None
    assert result == datetime(2026, 9, 15, 10, 30)


def test_naive_utc_round_trips_through_utc_itself():
    aware = datetime(2026, 9, 15, 10, 30, tzinfo=timezone.utc)
    assert _naive_utc(aware) == datetime(2026, 9, 15, 10, 30)


# --- CODE_BY_STATUS_VALUE ------------------------------------------------


def test_code_by_status_value_covers_every_status_and_agrees_with_code_by_status():
    for status, code in CODE_BY_STATUS.items():
        assert CODE_BY_STATUS_VALUE[status.value] == code
    assert set(CODE_BY_STATUS_VALUE) == {s.value for s in AttendanceStatus}


# --- _blocking_reasons ----------------------------------------------------
# Pure logic, no DB — but the exact expressions the Finalize button
# actually gates on, extracted from _finalization_panel for this reason.


class _FakeClassDay:
    def __init__(self, calendar_date):
        self.calendar_date = calendar_date


def test_blocking_reasons_is_empty_when_nothing_is_wrong():
    reasons = _blocking_reasons(
        {"problems": []},
        has_unsaved_edits=False,
        current_state=FinalizationState.OPEN,
        class_days=[_FakeClassDay(date(2026, 8, 28))],
        today=date(2026, 9, 15),
    )
    assert reasons == []


def test_blocking_reasons_flags_unresolved_validation_problems():
    reasons = _blocking_reasons(
        {"problems": ["someone: 1 class day(s) with no attendance encoded."]},
        has_unsaved_edits=False,
        current_state=FinalizationState.OPEN,
        class_days=[],
        today=date(2026, 9, 15),
    )
    assert any("Resolve everything in red" in r for r in reasons)


def test_blocking_reasons_flags_unsaved_grid_edits():
    reasons = _blocking_reasons(
        {"problems": []},
        has_unsaved_edits=True,
        current_state=FinalizationState.OPEN,
        class_days=[],
        today=date(2026, 9, 15),
    )
    assert any("unsaved edits" in r for r in reasons)


def test_blocking_reasons_flags_a_never_prepared_month():
    reasons = _blocking_reasons(
        {"problems": []},
        has_unsaved_edits=False,
        current_state=FinalizationState.NOT_STARTED,
        class_days=[],
        today=date(2026, 9, 15),
    )
    assert any("hasn't been prepared" in r for r in reasons)


def test_blocking_reasons_flags_a_month_still_in_progress():
    """Last class day today or later must block — not just "later"."""
    reasons = _blocking_reasons(
        {"problems": []},
        has_unsaved_edits=False,
        current_state=FinalizationState.OPEN,
        class_days=[_FakeClassDay(date(2026, 9, 15))],
        today=date(2026, 9, 15),
    )
    assert any("still in progress" in r for r in reasons)


def test_blocking_reasons_allows_a_month_whose_last_class_day_has_passed():
    reasons = _blocking_reasons(
        {"problems": []},
        has_unsaved_edits=False,
        current_state=FinalizationState.OPEN,
        class_days=[_FakeClassDay(date(2026, 8, 28))],
        today=date(2026, 9, 15),
    )
    assert reasons == []


def test_blocking_reasons_with_no_class_days_does_not_flag_ongoing():
    """A month validate_month already blocks for having no calendar days
    at all shouldn't also claim it's "still in progress" — there's no
    last class day to compare against."""
    reasons = _blocking_reasons(
        {"problems": ["No class days on the academic calendar..."]},
        has_unsaved_edits=False,
        current_state=FinalizationState.OPEN,
        class_days=[],
        today=date(2026, 9, 15),
    )
    assert not any("still in progress" in r for r in reasons)


def test_blocking_reasons_stacks_every_applicable_reason():
    reasons = _blocking_reasons(
        {"problems": ["x"]},
        has_unsaved_edits=True,
        current_state=FinalizationState.NOT_STARTED,
        class_days=[_FakeClassDay(date(2026, 9, 15))],
        today=date(2026, 9, 15),
    )
    assert len(reasons) == 4


# --- _last_attendance_change ----------------------------------------------


def test_last_attendance_change_returns_none_for_an_empty_roster_or_days():
    assert _last_attendance_change(None, [], []) is None


def test_last_attendance_change_finds_the_audit_entry_it_wrote(session, school_year):
    months = months_with_class_days(session, school_year.id)
    if not months:
        pytest.skip("no class days on the calendar")
    # The furthest-out month on the calendar, not the first: this suite
    # runs against the live database (no separate test DB — see
    # `_enrollments_without_attendance`'s docstring), and by mid-term the
    # earliest months are fully encoded for real, leaving no free slot to
    # write a synthetic record into without colliding.
    year, month = months[-1]
    days = class_days_in_month(session, school_year.id, year, month)
    if not days:
        pytest.skip("no class days this month")

    section = _any_advised_section(session, school_year)
    roster = roster_for_month(session, section.id, school_year.id, year, month)
    if not roster:
        pytest.skip("no roster this month")

    candidates = _enrollments_without_attendance(session, school_year, section, [days[0].id], 1)
    if not candidates:
        pytest.skip("no learner with this day unencoded")
    enrollment = candidates[0]

    record = AttendanceRecord(
        enrollment_id=enrollment.id,
        calendar_date_id=days[0].id,
        status=AttendanceStatus.PRESENT,
    )
    session.add(record)
    session.flush()  # visible to this transaction only; never committed

    entry = audit_service.record(
        session,
        action=audit_service.ATTENDANCE_CHANGED,
        object_type="attendance_records",
        object_id=record.id,
        previous={"status": "PRESENT"},
        new={"status": "ABSENT", "learner": "Test, Learner", "date": days[0].calendar_date},
    )
    session.flush()

    found = _last_attendance_change(session, roster, days)
    assert found is not None
    assert found.id == entry.id
    assert found.new_value["status"] == "ABSENT"


def test_last_attendance_change_ignores_a_different_months_records(session, school_year):
    """`_last_attendance_change` is filtered to the `class_days` it's
    given — an override logged for a different month must not surface as
    "recent" here just because it shares a `record_ids` query shape."""
    months = months_with_class_days(session, school_year.id)
    if len(months) < 2:
        pytest.skip("needs at least two months with class days")
    # Furthest-out months, for the same reason as the test above: more
    # likely to still have a free slot on a live, mid-term database.
    year, month = months[-1]
    other_year, other_month = months[-2]

    days = class_days_in_month(session, school_year.id, year, month)
    other_days = class_days_in_month(session, school_year.id, other_year, other_month)
    if not days or not other_days:
        pytest.skip("one of the two months has no class days")

    section = _any_advised_section(session, school_year)
    roster = roster_for_month(session, section.id, school_year.id, other_year, other_month)
    if not roster:
        pytest.skip("no roster in the other month")

    candidates = _enrollments_without_attendance(session, school_year, section, [days[0].id], 1)
    if not candidates:
        pytest.skip("no learner with this day unencoded")
    enrollment = candidates[0]

    record = AttendanceRecord(
        enrollment_id=enrollment.id,
        calendar_date_id=days[0].id,
        status=AttendanceStatus.PRESENT,
    )
    session.add(record)
    session.flush()
    audit_service.record(
        session,
        action=audit_service.ATTENDANCE_CHANGED,
        object_type="attendance_records",
        object_id=record.id,
        previous={"status": "PRESENT"},
        new={"status": "ABSENT", "learner": "Test, Learner", "date": days[0].calendar_date},
    )
    session.flush()

    # Asking about `other_month`'s roster/days must not find a change
    # logged against `month`'s record.
    found = _last_attendance_change(session, roster, other_days)
    assert found is None or found.object_id != record.id


# --- _save_grid: the last_saved stamp -------------------------------------


def test_save_grid_stamps_last_saved_on_a_real_change(session, school_year, monkeypatch):
    """The one test that needed the 61679070270f migration: `_save_grid`'s
    only real `session.commit()` is patched to a flush, so this exercises
    the actual write path — including the AttendanceMonthStatus stamp —
    without ever committing synthetic data to the live database."""

    def fake_try_commit(session, message):
        session.flush()
        return True

    monkeypatch.setattr("app.admin_pages.attendance.try_commit", fake_try_commit)

    months = months_with_class_days(session, school_year.id)
    if not months:
        pytest.skip("no class days on the calendar")
    year, month = months[-1]
    days = class_days_in_month(session, school_year.id, year, month)
    if not days:
        pytest.skip("no class days this month")

    section = _any_advised_section(session, school_year)
    roster = roster_for_month(session, section.id, school_year.id, year, month)
    if not roster:
        pytest.skip("no roster this month")

    # Find a learner/day pair that's both unencoded and inside the
    # learner's active window — a day outside the window is silently
    # skipped by _save_grid regardless of what the grid cell says, so
    # picking one at random risks a false "nothing changed".
    target = None
    for day in days[:10]:
        free_ids = {
            e.id for e in _enrollments_without_attendance(
                session, school_year, section, [day.id], len(roster)
            )
        }
        for enrollment, learner, window in roster:
            if enrollment.id in free_ids and window.contains(day.calendar_date):
                target = (day, enrollment)
                break
        if target:
            break
    if target is None:
        pytest.skip("no learner with a free, in-window day in this month")
    day, enrollment = target

    dataframe = _grid_dataframe(session, roster, days)
    edited = dataframe.copy()
    row_index = next(i for i, (e, _, _) in enumerate(roster) if e.id == enrollment.id)
    edited.loc[row_index, str(day.calendar_date.day)] = "X"

    before = get_month_status(session, section.id, year, month)
    before_saved_at = before.last_saved_at if before else None

    saved = _save_grid(
        session, section.id, school_year.id, year, month, roster, days, edited,
        section.adviser_user_id,
    )
    assert saved is True

    record = (
        session.query(AttendanceRecord)
        .filter_by(enrollment_id=enrollment.id, calendar_date_id=day.id)
        .one()
    )
    assert record.status == AttendanceStatus.ABSENT

    after = get_month_status(session, section.id, year, month)
    assert after is not None
    assert after.last_saved_at is not None
    if before_saved_at is not None:
        assert after.last_saved_at >= before_saved_at
    assert after.last_saved_by_user_id == section.adviser_user_id


def test_save_grid_does_not_stamp_on_a_no_op_save(session, school_year, monkeypatch):
    """Saving with nothing actually changed must not move last_saved_at —
    _finalization_panel's "changed after your last save" comparison only
    means anything if a no-op save can't fake a fresher baseline."""

    def fake_try_commit(session, message):
        session.flush()
        return True

    monkeypatch.setattr("app.admin_pages.attendance.try_commit", fake_try_commit)

    months = months_with_class_days(session, school_year.id)
    if not months:
        pytest.skip("no class days on the calendar")
    year, month = months[-1]
    days = class_days_in_month(session, school_year.id, year, month)
    if not days:
        pytest.skip("no class days this month")

    section = _any_advised_section(session, school_year)
    roster = roster_for_month(session, section.id, school_year.id, year, month)
    if not roster:
        pytest.skip("no roster this month")

    before = get_month_status(session, section.id, year, month)
    before_saved_at = before.last_saved_at if before else None

    # The grid handed back exactly what's already on record — nothing for
    # _save_grid to detect as a change.
    dataframe = _grid_dataframe(session, roster, days)
    saved = _save_grid(
        session, section.id, school_year.id, year, month, roster, days, dataframe.copy(),
        section.adviser_user_id,
    )
    assert saved is False

    after = get_month_status(session, section.id, year, month)
    after_saved_at = after.last_saved_at if after else None
    assert after_saved_at == before_saved_at
