"""The batch academic-record capture must write exactly what the old
per-learner function wrote, and cost a fixed number of queries.

`tests/legacy_academic_record.py` is a verbatim copy of the per-learner
`capture_academic_record` as it stood before batching — kept as the
reference to compare against. Everything runs inside sessions that are
rolled back; nothing here commits.
"""

import pytest

from app import academic_record_service as new
from app.database import SessionLocal
from app.models.academic_record import (
    LearnerAcademicRecord,
    LearnerAcademicRecordSubject,
    LearnerAcademicRecordTerm,
)
from app.models.awards import LearnerAward
from app.models.enums import AwardResult
from app.models.grades import CombinedLearningAreaResult
from app.models.learners import Enrollment
from app.models.organization import Term
from tests import legacy_academic_record as legacy
from tests.test_query_cost import QueryCounter

IGNORED = {"id", "learner_academic_record_id", "snapshot_at", "created_at", "updated_at", "version"}


def _dump(session, enrollment_id):
    """Every stored column of the record and its children, minus ids and
    timestamps, in a stable order."""
    session.flush()
    record = session.query(LearnerAcademicRecord).filter_by(enrollment_id=enrollment_id).one()

    def row(obj):
        return {
            c.name: getattr(obj, c.key if hasattr(obj, c.key) else c.name)
            for c in obj.__table__.columns
            if c.name not in IGNORED
        }

    subjects = (
        session.query(LearnerAcademicRecordSubject)
        .filter_by(learner_academic_record_id=record.id)
        .order_by(LearnerAcademicRecordSubject.display_order)
        .all()
    )
    terms = (
        session.query(LearnerAcademicRecordTerm)
        .filter_by(learner_academic_record_id=record.id)
        .order_by(LearnerAcademicRecordTerm.term_number)
        .all()
    )
    return row(record), [row(s) for s in subjects], [row(t) for t in terms]


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


def _ambiguous_awards(session, roster):
    """(enrollment_id, term_id) pairs holding several ELIGIBLE_AWARDED rows
    (a computed award plus overrides). The old `.first()` had no ORDER BY,
    so which of them it returned varies from run to run — nothing to compare
    exactly. Returns {pair: {candidate award names}}."""
    found: dict = {}
    for a in session.query(LearnerAward).filter(
        LearnerAward.enrollment_id.in_([e.id for e in roster]),
        LearnerAward.award_result == AwardResult.ELIGIBLE_AWARDED,
    ):
        found.setdefault((a.enrollment_id, a.term_id), []).append(a.award_name)
    return {k: set(v) | {None} for k, v in found.items() if len(v) > 1}


def _legacy_dumps(roster):
    s = SessionLocal()
    try:
        out = {}
        for e in roster:
            legacy.capture_academic_record(s, e.id, None)
            out[e.id] = _dump(s, e.id)
        return out
    finally:
        s.rollback()
        s.close()


@pytest.mark.parametrize("with_pair", [False, True], ids=["any-section", "grade11-language-pair"])
def test_batch_writes_what_the_per_learner_function_wrote(session, with_pair):
    roster = _section_roster(session, with_pair)[:12]
    expected = _legacy_dumps(roster)

    ambiguous = _ambiguous_awards(session, roster)
    new.capture_academic_records_batch(session, [e.id for e in roster], None)
    for e in roster:
        got = _dump(session, e.id)
        want = expected[e.id]
        if any(eid == e.id for eid, _ in ambiguous):
            # Compare everything except the award names, which must still
            # be one of the candidates the data holds.
            terms_by_id = {t.term_number: t.id for t in session.query(Term).filter_by(
                school_year_id=e.school_year_id)}
            for part, rows in ((0, [(None, got[0])]),
                               (2, [(terms_by_id[r["term_number"]], r) for r in got[2]])):
                for term_id, r in rows:
                    names = ambiguous.get((e.id, term_id))
                    if names is not None:
                        assert r["award_name"] in names
                        r["award_name"] = None
            for r_old in [want[0]] + want[2]:
                r_old["award_name"] = None
            got[0]["award_name"] = want[0]["award_name"] = None
            for r in got[2] + want[2]:
                r["award_name"] = None
        assert got == want, f"enrollment {e.id} differs"

    if with_pair:
        subjects = expected[roster[0].id][1] if with_pair else []
        # at least one learner in this section carries the pair rows
        assert any(
            s["is_component"] for e in roster for s in expected[e.id][1]
        ), "expected a combined-language component row to be compared"


def test_recapture_over_an_existing_record_matches_and_bumps_revision(session):
    roster = _section_roster(session, True)[:5]
    expected = _legacy_dumps(roster)
    ids = [e.id for e in roster]
    new.capture_academic_records_batch(session, ids, None)
    session.flush()
    new.capture_academic_records_batch(session, ids, None)
    for e in roster:
        record, subjects, terms = _dump(session, e.id)
        assert record.pop("revision") == 2
        want = expected[e.id]
        want[0].pop("revision")
        assert (record, subjects, terms) == want
        assert (
            session.query(LearnerAcademicRecord).filter_by(enrollment_id=e.id).count() == 1
        )


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
