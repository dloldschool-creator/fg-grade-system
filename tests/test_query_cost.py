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
    assert counter.count <= 8, (
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
