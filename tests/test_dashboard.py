"""The School Dashboard (§3E "view dashboards", "review section summaries")."""

import pytest

from app.admin_pages.dashboard import DISPLAY_COLUMNS, _enrollment_overview
from app.database import SessionLocal
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
