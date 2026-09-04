"""Guards against N+1 query regressions.

The database is roughly 85ms away (Supabase in Tokyo), so a query issued
once per learner is the difference between a page that renders instantly
and one that takes a minute. Before batching, building the report-card
rows for a section cost about twelve queries *per learner* — forty
learners meant ~480 round trips, roughly 40 seconds.

These tests assert the *shape* of the data access rather than a wall
time, so they stay meaningful on a fast local database where the bug
would be invisible.
"""

import pytest
from sqlalchemy import event

from app.database import SessionLocal, engine
from app.models.academic_structure import Section
from app.models.learners import Enrollment
from app.models.organization import SchoolYear, Term
from app.report_card import (
    build_learning_area_rows,
    build_term_subject_rows,
    load_report_context,
)


class QueryCounter:
    def __init__(self):
        self.count = 0

    def __enter__(self):
        event.listen(engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def roster(session):
    school_year = session.query(SchoolYear).first()
    if school_year is None:
        pytest.skip("no school year in the database")
    section = session.query(Section).filter_by(school_year_id=school_year.id).first()
    if section is None:
        pytest.skip("no section in the database")
    enrollments = (
        session.query(Enrollment)
        .filter_by(section_id=section.id, school_year_id=school_year.id)
        .all()
    )
    if not enrollments:
        pytest.skip("section has no enrolled learners")
    return enrollments


def test_loading_a_roster_costs_a_fixed_number_of_queries(session, roster):
    """`load_report_context` must not scale with the roster — every fetch
    inside it is an IN(...) over the whole list."""
    with QueryCounter() as counter:
        load_report_context(session, roster)
    # Was 8; 9 since the context also resolves the section's averaging rules
    # (DO 017 s. 2026), which `build_term_subject_rows` needs to decide
    # whether the language pair prints as one row or two. One query for the
    # whole section — the number that must not move is the per-learner one,
    # asserted below.
    assert counter.count <= 9, (
        f"{counter.count} queries to load a {len(roster)}-learner roster; "
        "something inside load_report_context is querying per learner"
    )


def test_building_rows_from_a_context_issues_no_queries(session, roster):
    """This is the property that matters: once the context is loaded,
    rendering each learner is pure computation. If this starts issuing
    queries, the per-learner cost is back."""
    context = load_report_context(session, roster)
    with QueryCounter() as counter:
        for enrollment in roster:
            build_learning_area_rows(session, enrollment, context)
    assert counter.count == 0, (
        f"{counter.count} queries while building rows for {len(roster)} learners "
        "from a preloaded context — expected none"
    )


def test_term_card_rows_from_a_context_issue_no_queries(session, roster):
    context = load_report_context(session, roster)
    with QueryCounter() as counter:
        for enrollment in roster:
            build_term_subject_rows(session, enrollment, 1, context)
    assert counter.count == 0


def test_a_whole_section_costs_no_more_than_one_learner(session, roster):
    """The end-to-end guarantee: rendering forty learners must cost the
    same round trips as rendering one."""
    with QueryCounter() as whole_section:
        context = load_report_context(session, roster)
        for enrollment in roster:
            build_learning_area_rows(session, enrollment, context)

    with QueryCounter() as single:
        build_learning_area_rows(session, roster[0])

    assert whole_section.count <= single.count, (
        f"{len(roster)} learners cost {whole_section.count} queries but one learner "
        f"costs {single.count} — the batching isn't holding"
    )


def test_batch_sf9_costs_far_less_per_learner_than_a_single_card(session, roster):
    """Printing a whole section's report cards (§35) hit the same N+1 the
    report-card rows already had, one layer up: building one SF9 costs
    ~43 queries of its own — the school, calendar, offerings and summaries
    are all refetched per learner even though they're identical across the
    section. At 85ms a round trip that is ~3.6s per card, so forty cards
    took over two minutes.
    """
    from app.sf9_report import build_sf9_workbook, load_sf9_context

    with QueryCounter() as single:
        build_sf9_workbook(session, roster[0].id)

    with QueryCounter() as batched:
        context = load_sf9_context(session, roster)
        for enrollment in roster:
            build_sf9_workbook(session, enrollment.id, context)

    per_learner = (batched.count - 0) / len(roster)
    assert per_learner < single.count / 2, (
        f"{len(roster)} cards cost {batched.count} queries ({per_learner:.1f} each) "
        f"against {single.count} for one — the batch context isn't holding"
    )


def test_sf4_costs_a_fixed_number_of_queries_for_the_whole_school(session):
    """SF4 aggregates every learner in the school, so a per-learner query
    would be the worst offender in the app: 1,200 learners at ~85ms is
    minutes. The count must not depend on how many learners exist."""
    from app.attendance_service import months_with_class_days
    from app.models.organization import SchoolYear
    from app.sf4_report import build_sf4_workbook

    school_year = session.query(SchoolYear).first()
    months = months_with_class_days(session, school_year.id)
    if not months:
        pytest.skip("no months with class days")
    year, month = months[0]

    build_sf4_workbook(session, school_year.id, year, month)  # warm the metadata
    with QueryCounter() as counter:
        build_sf4_workbook(session, school_year.id, year, month)

    assert counter.count <= 15, (
        f"SF4 issued {counter.count} queries; it should be a flat handful "
        "regardless of school size"
    )


def test_roster_for_month_costs_a_fixed_number_of_queries(session, roster):
    """`roster_for_month` must not scale with the roster. It used to call
    `active_window_for` (a `LearnerMovement` query) and
    `session.get(Learner, ...)` once per enrollment — 2×N round trips,
    and it's called several times per attendance page action (seeding,
    the grid, saving, validating), which is what made preparing/
    refreshing a month's sheet take 60+ seconds. Batched the same way
    `analytics_service.attendance_risk()` already does (see CLAUDE.md's
    Insights section)."""
    from app.attendance_service import roster_for_month
    from app.models.organization import SchoolYear

    section_id = roster[0].section_id
    school_year_id = roster[0].school_year_id
    school_year = session.get(SchoolYear, school_year_id)

    with QueryCounter() as counter:
        roster_for_month(
            session, section_id, school_year_id,
            school_year.start_date.year, school_year.start_date.month,
        )
    assert counter.count <= 4, (
        f"{counter.count} queries for a {len(roster)}-learner roster; "
        "something inside roster_for_month is querying per enrollment"
    )


def test_summarize_month_batch_costs_a_fixed_number_of_queries(session, roster):
    """`summarize_month_batch` must not scale with the roster. Six call
    sites (the Attendance page's monthly summary and finalization report,
    SF2's preview and the printed form, and the attendance export) used
    to call `summarize_month` per learner — each one its own
    `records_for_month` query — turning one page render into 2×N round
    trips (`validate_month` also queried `LearnerMovement` per learner on
    top of that)."""
    from app.attendance_service import class_days_in_month, movements_by_enrollment, roster_for_month, summarize_month_batch
    from app.models.organization import SchoolYear

    section_id = roster[0].section_id
    school_year_id = roster[0].school_year_id
    school_year = session.get(SchoolYear, school_year_id)
    year, month = school_year.start_date.year, school_year.start_date.month

    full_roster = roster_for_month(session, section_id, school_year_id, year, month)
    class_days = class_days_in_month(session, school_year_id, year, month)

    with QueryCounter() as counter:
        summaries = summarize_month_batch(session, full_roster, class_days)
    assert counter.count <= 1, (
        f"{counter.count} queries to summarize a {len(full_roster)}-learner roster; "
        "something inside summarize_month_batch is querying per enrollment"
    )
    assert set(summaries) == {e.id for e, _, _ in full_roster}

    with QueryCounter() as counter:
        movements_by_enrollment(session, [e.id for e, _, _ in full_roster])
    assert counter.count <= 1, (
        f"{counter.count} queries for movements on a {len(full_roster)}-learner roster"
    )


def test_recompute_context_costs_a_fixed_number_of_queries(session, roster):
    """`_load_recompute_context` (the batch step behind
    `recompute_enrollment_grades_batch`) must not scale with how many
    enrollments are being recomputed — the whole point of batching Save/
    Submit's recompute instead of calling it once per learner."""
    from app.grading_service import _load_recompute_context

    with QueryCounter() as whole_section:
        _load_recompute_context(session, roster)
    with QueryCounter() as single:
        _load_recompute_context(session, [roster[0]])

    assert whole_section.count <= single.count, (
        f"{len(roster)} enrollments cost {whole_section.count} queries to load "
        f"a recompute context but one costs {single.count} — the batching isn't holding"
    )


def test_recompute_one_issues_no_queries_of_its_own(session, roster):
    """The property that matters: once `_load_recompute_context` has run,
    computing (not committing) each learner's derived grades is pure
    lookups against the preloaded context — no `session.query`/`session.get`
    calls. Read the AST rather than running it, since `_recompute_one`
    also writes rows via `session.add`, and this suite must never commit
    against the live database (see the module docstring)."""
    import ast
    import inspect

    from app import grading_service

    source = inspect.getsource(grading_service._recompute_one)
    tree = ast.parse(source)
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "session"
        and node.func.attr in ("query", "get", "commit")
    ]
    assert not calls, (
        f"_recompute_one calls session.{calls} — it should only read from the "
        "preloaded _RecomputeContext and session.add() new rows; a query or "
        "commit here is the per-learner N+1 (or an early commit) coming back"
    )


def test_connection_pool_is_sized_for_concurrent_teachers():
    """~40 teachers share this pool, and Streamlit runs each session in
    its own thread. The old 5+10 default queued requests as soon as a
    dozen were active."""
    assert engine.pool.size() >= 20
    assert engine.pool._max_overflow >= 10


def test_connections_are_checked_before_reuse():
    """Supabase closes idle connections server-side. Without pre_ping the
    first query on a dropped connection raises instead of reconnecting,
    which surfaces as random errors in a long-running app."""
    assert engine.pool._pre_ping is True
    assert engine.pool._recycle > 0
