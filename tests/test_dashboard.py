"""The School Dashboard (§3E "view dashboards", "review section summaries")."""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.admin_pages.dashboard import DISPLAY_COLUMNS, _attendance_status, _enrollment_overview
from app.database import SessionLocal
from app.models.academic_structure import Section
from app.models.attendance import AttendanceMonthStatus
from app.models.enums import FinalizationState
from app.models.organization import SchoolYear


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_internal_sort_keys_are_not_displayed():
    """`_grade` and `_grade_order` exist to group and order the rows, and
    would be noise in the table. Leaking them is the easy mistake when a
    column is added."""
    assert not [name for name in DISPLAY_COLUMNS if name.startswith("_")]
    assert "Track" in DISPLAY_COLUMNS and "Strand" in DISPLAY_COLUMNS


def test_sections_come_back_grouped_by_grade_then_track_then_strand(session):
    """The page renders one table per grade level and relies on the rows
    already being in order — sorting in the view instead would put the
    two apart, and they would drift."""
    school_year = session.query(SchoolYear).first()
    if school_year is None:
        pytest.skip("no school year")
    rows = _enrollment_overview(session, school_year.id)
    if len(rows) < 2:
        pytest.skip("needs at least two sections")

    ordered = [(r["_grade_order"], r["Track"], r["Strand"], r["Section"]) for r in rows]
    assert ordered == sorted(ordered)

    # Grade levels must appear in contiguous blocks, or a table per grade
    # would repeat a heading.
    seen = []
    for row in rows:
        if not seen or seen[-1] != row["_grade"]:
            seen.append(row["_grade"])
    assert len(seen) == len(set(seen)), "grade levels are not contiguous"


def test_every_display_column_is_present_on_each_row(session):
    school_year = session.query(SchoolYear).first()
    if school_year is None:
        pytest.skip("no school year")
    rows = _enrollment_overview(session, school_year.id)
    if not rows:
        pytest.skip("no sections")
    for row in rows:
        for column in DISPLAY_COLUMNS:
            assert column in row, f"{column} missing"


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    filter_by = filter

    def all(self):
        return self._rows


class _FakeSession:
    """Just enough session for `_attendance_status`: sections, then their
    month statuses. No reprint is pending, so `User` is never queried."""

    def __init__(self, sections, statuses):
        self._rows = {Section: sections, AttendanceMonthStatus: statuses}

    def query(self, model):
        return _FakeQuery(self._rows[model])


def _month(section_id, year_month):
    return SimpleNamespace(
        id=uuid.uuid4(),
        section_id=section_id,
        year_month=year_month,
        status=FinalizationState.OPEN,
        reopened_at=None,
        changed_after_finalize_at=None,
        reprint_acknowledged_at=None,
    )


def test_attendance_months_are_latest_first_then_by_section():
    """Latest month on top, sections alphabetical within a month — and
    Month stays a date, so clicking its header sorts by date rather than
    alphabetically (April before October)."""
    a, b = uuid.uuid4(), uuid.uuid4()
    sections = [SimpleNamespace(id=b, name="BEZOS"), SimpleNamespace(id=a, name="ARISTOTLE")]
    statuses = [
        _month(b, date(2026, 8, 1)),
        _month(a, date(2026, 10, 1)),
        _month(a, date(2026, 8, 1)),
        _month(b, date(2026, 10, 1)),
        _month(a, date(2026, 9, 1)),
    ]

    rows = _attendance_status(_FakeSession(sections, statuses), sy_id=None)

    assert [(r["Month"], r["Section"]) for r in rows] == [
        (date(2026, 10, 1), "ARISTOTLE"),
        (date(2026, 10, 1), "BEZOS"),
        (date(2026, 9, 1), "ARISTOTLE"),
        (date(2026, 8, 1), "ARISTOTLE"),
        (date(2026, 8, 1), "BEZOS"),
    ]


def test_the_overview_cost_does_not_scale_with_sections(session):
    """Thirty sections must cost the same round trips as one — the
    database is ~85ms away."""
    from tests.test_query_cost import QueryCounter

    school_year = session.query(SchoolYear).first()
    if school_year is None:
        pytest.skip("no school year")

    _enrollment_overview(session, school_year.id)  # warm
    with QueryCounter() as counter:
        _enrollment_overview(session, school_year.id)
    assert counter.count <= 8, f"{counter.count} queries for the section overview"
