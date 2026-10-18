"""The batch academic-record capture: tie-break rule, re-capture, and a
fixed query count.

Everything runs inside sessions that are rolled back; nothing here commits.

Equivalence with the old per-learner function (every stored column of the
record, its subject rows and its term rows, for an ordinary section and a
Grade 11 language-pair section) was proven on 2026-10-18 against a verbatim
copy of that function before the batch replaced it, and the copy was then
deleted rather than left to drift from the model. The per-learner wrapper is
still covered by tests/test_academic_record.py.
"""

import pytest

from app import academic_record_service as new
from app.database import SessionLocal
from app.models.academic_record import (
    LearnerAcademicRecord,
    LearnerAcademicRecordSubject,
)
from app.models.grades import CombinedLearningAreaResult
from app.models.learners import Enrollment
from tests.test_query_cost import QueryCounter


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _section_roster(session, with_pair: bool):
    q = session.query(Enrollment)
    if with_pair:
        q = q.join(
            CombinedLearningAreaResult, CombinedLearningAreaResult.enrollment_id == Enrollment.id
        )
    first = q.first()
    if first is None:
        pytest.skip("no suitable enrollment in the database")
    return (
        session.query(Enrollment)
        .filter_by(section_id=first.section_id, school_year_id=first.school_year_id)
        .all()
    )


def test_award_tie_break_prefers_an_override_then_a_named_award():
    """The record holds one award name per year/term, so when several
    ELIGIBLE_AWARDED rows compete the choice is fixed: an administrator's
    override (§40) wins over a computed award, a named award over an
    unnamed one, and the name decides what is left."""
    from types import SimpleNamespace as Row

    computed = Row(is_override=False, award_name="With Honors")
    override = Row(is_override=True, award_name="Academic Excellence")
    unnamed_override = Row(is_override=True, award_name=None)
    other = Row(is_override=False, award_name="Academic Excellence")

    assert min([computed, override], key=new._award_rank) is override
    assert min([override, computed], key=new._award_rank) is override
    assert min([unnamed_override, override], key=new._award_rank) is override
    assert min([computed, other], key=new._award_rank) is other  # name breaks the tie


def test_recapture_over_an_existing_record_bumps_revision_and_keeps_one_record(session):
    roster = _section_roster(session, True)[:3]
    ids = [e.id for e in roster]
    new.capture_academic_records_batch(session, ids, None)
    session.flush()
    first_rows = {
        e_id: session.query(LearnerAcademicRecordSubject)
        .join(LearnerAcademicRecord)
        .filter(LearnerAcademicRecord.enrollment_id == e_id)
        .count()
        for e_id in ids
    }
    new.capture_academic_records_batch(session, ids, None)
    session.flush()
    for e_id in ids:
        records = session.query(LearnerAcademicRecord).filter_by(enrollment_id=e_id).all()
        assert len(records) == 1
        assert records[0].revision == 2
        again = (
            session.query(LearnerAcademicRecordSubject)
            .filter_by(learner_academic_record_id=records[0].id)
            .count()
        )
        assert again == first_rows[e_id], "children must be replaced, not accumulated"


def test_batch_cost_does_not_grow_with_the_number_of_enrollments(session):
    roster = _section_roster(session, False)
    if len(roster) < 3:
        pytest.skip("section too small to show growth")
    # Read the ids first: rollback() expires the objects, and touching
    # `.id` afterwards would refresh each one — a per-learner query that
    # belongs to the test, not the function under test.
    ids = [e.id for e in roster]
    with QueryCounter() as small:
        new.capture_academic_records_batch(session, ids[:2], None)
    session.rollback()
    with QueryCounter() as whole:
        new.capture_academic_records_batch(session, ids, None)
    session.rollback()
    print(f"queries: 2 learners={small.count}, {len(ids)} learners={whole.count}")
    # Inserts are executemany-batched, so the two should be near-equal; the
    # slack covers insertmanyvalues chunking, not a per-learner term.
    assert whole.count <= small.count + 6, (small.count, whole.count)
    assert whole.count <= 40, whole.count


def test_finalize_all_eligible_costs_a_fixed_number_of_queries(session):
    """The section-wide Finalize writes (flush only — rolled back here)
    cost the same for 2 learners as for the whole section."""
    from types import SimpleNamespace

    from app.admin_pages.grade_summary import finalize_eligible_enrollments
    from app.models.rbac import User
    from app.models.enums import CompletionStatus

    roster = _section_roster(session, False)
    if len(roster) < 3:
        pytest.skip("section too small to show growth")
    user = session.query(User).first()
    actor = SimpleNamespace(id=user.id)
    ids = [e.id for e in roster]
    summary = SimpleNamespace(completion_status=CompletionStatus.COMPLETE, general_average=None)

    def run(subset_ids):
        rows = [session.get(Enrollment, i) for i in subset_ids]
        panel = {
            "finalization_open": True,
            "finalization": {},
            "annual": {i: summary for i in subset_ids},
        }
        with QueryCounter() as counter:
            result = finalize_eligible_enrollments(session, actor, rows, panel)
            session.flush()
        session.rollback()
        return counter.count, result

    session.query(Enrollment).filter(Enrollment.id.in_(ids)).all()  # warm identity map
    small, result_small = run(ids[:2])
    whole, result_whole = run(ids)
    print(f"finalize queries: 2 learners={small}, {len(ids)} learners={whole}")
    assert result_small == (2, 0, 0) and result_whole == (len(ids), 0, 0)
    assert whole <= small + 6, (small, whole)
