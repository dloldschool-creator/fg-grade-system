"""Tests for irregular-learner subject substitutions
(app/enrollment_subject_overrides.py) — an override changes which subject
counts toward a learner's Term/General Average and what prints on their
report card, without the rest of their section's offering list changing
at all.

Every DB-touching test calls `_load_recompute_context`/`_recompute_one`
directly rather than `recompute_enrollment_grades_batch`, which commits
(see its own docstring and CLAUDE.md) — so the `session` fixture's
rollback undoes everything, live database included.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.database import SessionLocal
from app.enrollment_subject_overrides import apply_overrides
from app.grading_service import _load_recompute_context, _recompute_one
from app.models.grades import SubjectFinalGrade, TermGrade
from app.models.learners import Enrollment
from app.models.organization import SchoolYear, Term
from app.models.subjects import EnrollmentSubjectOverride, SectionSubjectOffering, Subject
from app.report_card import build_learning_area_rows, build_term_subject_rows, load_report_context


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# --- apply_overrides: pure function, no DB ----------------------------------


class _FakeOffering:
    def __init__(self, id_, subject_id, section_id="section"):
        self.id = id_
        self.subject_id = subject_id
        self.section_id = section_id


class _FakeOverride:
    def __init__(self, original_id, substitute_id):
        self.original_section_subject_offering_id = original_id
        self.substitute_section_subject_offering_id = substitute_id


def test_no_overrides_returns_the_same_list_unchanged():
    """The overwhelming common case must do no extra work — not even
    build a new list."""
    offerings = [_FakeOffering("a", "subj-a"), _FakeOffering("b", "subj-b")]
    assert apply_overrides(offerings, [], {}) is offerings


def test_override_removes_the_original_and_adds_the_substitute():
    offerings = [_FakeOffering("a", "subj-a"), _FakeOffering("b", "subj-b")]
    substitute = _FakeOffering("c", "subj-c", section_id="a-different-section")
    result = apply_overrides(offerings, [_FakeOverride("a", "c")], {"c": substitute})
    assert {o.id for o in result} == {"b", "c"}


def test_a_missing_substitute_is_skipped_rather_than_raising():
    """Shouldn't happen — callers batch-load every substitute referenced
    — but a dangling reference must not take down a whole roster's
    recompute."""
    offerings = [_FakeOffering("a", "subj-a")]
    result = apply_overrides(offerings, [_FakeOverride("a", "missing")], {})
    assert result == []


def test_two_overrides_can_replace_two_different_originals():
    offerings = [_FakeOffering("a", "subj-a"), _FakeOffering("b", "subj-b")]
    subs = {"c": _FakeOffering("c", "subj-c"), "d": _FakeOffering("d", "subj-d")}
    result = apply_overrides(
        offerings, [_FakeOverride("a", "c"), _FakeOverride("b", "d")], subs
    )
    assert {o.id for o in result} == {"c", "d"}


# --- End-to-end: grading + report card, against the live database ----------


def _pick_two_single_term_offerings(session, school_year_id):
    """(section_id, offering_a, offering_b): two different subjects that
    each run in exactly one term in the same section, sharing that term —
    so overriding either one fully removes it from the enrollment's
    applicable subjects (no other term of the same subject is left to
    keep it around), and the substitution is a clean same-term swap."""
    offerings = (
        session.query(SectionSubjectOffering).filter_by(school_year_id=school_year_id).all()
    )
    by_section: dict = {}
    for o in offerings:
        by_section.setdefault(o.section_id, []).append(o)
    for section_id, section_offerings in by_section.items():
        by_subject: dict = {}
        for o in section_offerings:
            by_subject.setdefault(o.subject_id, []).append(o)
        single_term = {sid: lst[0] for sid, lst in by_subject.items() if len(lst) == 1}
        by_term: dict = {}
        for o in single_term.values():
            by_term.setdefault(o.term_id, []).append(o)
        for same_term_offerings in by_term.values():
            if len(same_term_offerings) >= 2:
                return section_id, same_term_offerings[0], same_term_offerings[1]
    return None


@pytest.fixture
def override_fixture(session):
    """A real enrollment with a real override in place — created and
    flushed, never committed. Returns (enrollment, original, substitute,
    term_number)."""
    school_year = session.query(SchoolYear).order_by(SchoolYear.name.desc()).first()
    if school_year is None:
        pytest.skip("no school year in the database")
    picked = _pick_two_single_term_offerings(session, school_year.id)
    if picked is None:
        pytest.skip("no section has two single-term subjects sharing a term")
    section_id, original, substitute = picked

    enrollment = (
        session.query(Enrollment)
        .filter_by(section_id=section_id, school_year_id=school_year.id)
        .first()
    )
    if enrollment is None:
        pytest.skip("that section has no enrolled learners")

    # The live app may already have a real grade at this exact key —
    # deleted and reinserted in this same never-committed transaction
    # (see tests/test_analytics_service.py's _seed_offering_grades for
    # the same precaution, and why it's safe).
    session.query(TermGrade).filter_by(
        enrollment_id=enrollment.id,
        section_subject_offering_id=substitute.id,
        term_id=substitute.term_id,
    ).delete(synchronize_session=False)
    session.add(
        TermGrade(
            enrollment_id=enrollment.id,
            section_subject_offering_id=substitute.id,
            term_id=substitute.term_id,
            official_grade=Decimal(88),
        )
    )
    session.add(
        EnrollmentSubjectOverride(
            enrollment_id=enrollment.id,
            original_section_subject_offering_id=original.id,
            substitute_section_subject_offering_id=substitute.id,
            reason="test fixture",
        )
    )
    session.flush()

    term_number = session.get(Term, substitute.term_id).term_number
    return enrollment, original, substitute, term_number


def test_recompute_drops_the_original_final_and_computes_the_substitute(session, override_fixture):
    enrollment, original, substitute, _ = override_fixture

    context = _load_recompute_context(session, [enrollment])
    _recompute_one(session, enrollment, context, datetime.now(timezone.utc))
    session.flush()

    original_final = (
        session.query(SubjectFinalGrade)
        .filter_by(enrollment_id=enrollment.id, subject_id=original.subject_id)
        .first()
    )
    substitute_final = (
        session.query(SubjectFinalGrade)
        .filter_by(enrollment_id=enrollment.id, subject_id=substitute.subject_id)
        .first()
    )
    assert original_final is None, (
        "the original subject is a single-term elective the learner no longer "
        "takes at all — its final grade must be gone, not just stale"
    )
    assert substitute_final is not None
    assert substitute_final.final_grade == Decimal(88)


def test_a_stale_original_final_from_before_the_override_is_deleted(session, override_fixture):
    """The trap this exists for: an override added *after* the learner
    already had a final computed for the original subject must not leave
    that now-wrong row behind for the report card to also print."""
    enrollment, original, substitute, _ = override_fixture
    # Same live-data precaution as override_fixture's TermGrade: the
    # enrollment may already have a genuine final for the original
    # subject, and this test's own row would collide with it.
    session.query(SubjectFinalGrade).filter_by(
        enrollment_id=enrollment.id, subject_id=original.subject_id
    ).delete(synchronize_session=False)
    session.add(
        SubjectFinalGrade(
            enrollment_id=enrollment.id,
            subject_id=original.subject_id,
            school_year_id=enrollment.school_year_id,
            final_grade=Decimal(70),
        )
    )
    session.flush()

    context = _load_recompute_context(session, [enrollment])
    _recompute_one(session, enrollment, context, datetime.now(timezone.utc))
    session.flush()

    assert (
        session.query(SubjectFinalGrade)
        .filter_by(enrollment_id=enrollment.id, subject_id=original.subject_id)
        .first()
        is None
    )


def test_report_card_prints_the_substitute_in_place_of_the_original(session, override_fixture):
    enrollment, original, substitute, term_number = override_fixture

    context = _load_recompute_context(session, [enrollment])
    _recompute_one(session, enrollment, context, datetime.now(timezone.utc))
    session.flush()

    original_subject = session.get(Subject, original.subject_id)
    substitute_subject = session.get(Subject, substitute.subject_id)

    report_context = load_report_context(session, [enrollment])
    rows = build_learning_area_rows(session, enrollment, report_context)
    names = [r.name for r in rows]
    assert substitute_subject.official_name in names
    assert original_subject.official_name not in names

    term_rows = build_term_subject_rows(session, enrollment, term_number, report_context)
    term_names = [name for name, _grade in term_rows]
    assert substitute_subject.official_name in term_names
    assert original_subject.official_name not in term_names


def test_an_enrollment_with_no_override_is_completely_unaffected(session, override_fixture):
    """Sharing a context with an overridden enrollment must not leak the
    substitution onto anyone else's row — `_effective_offerings_by_subject`
    recomputes per learner, not once for the whole batch.

    Asserted directly on the resolved dict rather than on printed rows:
    both `original` and `substitute` are ordinary offerings of this same
    section, so a classmate legitimately has *both* subjects on their own
    card — that's correct, not a leak, and a row-name assertion couldn't
    tell the two apart. What must hold is that an un-overridden
    enrollment gets the shared section-wide dict back completely
    unchanged, not a recomputed one.
    """
    from app.report_card import _effective_offerings_by_subject

    enrollment, original, substitute, _ = override_fixture

    other = (
        session.query(Enrollment)
        .filter(
            Enrollment.section_id == enrollment.section_id,
            Enrollment.school_year_id == enrollment.school_year_id,
            Enrollment.id != enrollment.id,
        )
        .first()
    )
    if other is None:
        pytest.skip("that section only has one enrolled learner")

    report_context = load_report_context(session, [enrollment, other])
    assert _effective_offerings_by_subject(report_context, other.id) is report_context.offerings_by_subject
    assert (
        _effective_offerings_by_subject(report_context, enrollment.id)
        is not report_context.offerings_by_subject
    )
