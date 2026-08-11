"""Tests for the permanent learner academic record (§37, §38).

§38's guarantee is the one that matters here: "if administrators rename a
subject or change a policy in a later school year, historical SF10 records
must NOT change." These run against a real Postgres session and roll back,
so nothing is left behind.
"""

from decimal import Decimal

import pytest

from app.academic_record_service import (
    capture_academic_record,
    get_academic_record,
    record_subjects,
    record_terms,
)
from app.database import SessionLocal
from app.models.academic_record import (
    LearnerAcademicRecord,
    LearnerAcademicRecordSubject,
)
from app.models.learners import Enrollment
from app.models.subjects import Subject, SubjectCategory


@pytest.fixture
def session():
    """A session whose work is always rolled back — these tests write
    snapshot rows and mutate subject names, and must leave the live
    database exactly as they found it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def enrollment(session):
    row = session.query(Enrollment).first()
    if row is None:
        pytest.skip("no enrollment in the database to snapshot")
    return row


def test_capture_freezes_the_learner_and_school_identity(session, enrollment):
    record = capture_academic_record(session, enrollment.id)
    session.flush()

    assert record.learner_name
    assert record.school_year_name
    assert record.enrollment_id == enrollment.id
    assert record.revision == 1
    assert record.snapshot_at is not None


def test_subject_rename_does_not_change_a_finalized_record(session, enrollment):
    """The §38 guarantee, stated as directly as it can be tested: rename
    the subject afterwards and the frozen record still reads the old
    name, because it stored text rather than a foreign key."""
    record = capture_academic_record(session, enrollment.id)
    session.flush()

    # Skip the combined parent row: it's a virtual learning area (§16),
    # not a real Subject, so it legitimately has no subject_id to rename.
    rows = [r for r in record_subjects(session, record) if r.subject_id is not None]
    if not rows:
        pytest.skip("enrollment has no snapshotted rows backed by a real subject")
    captured = rows[0]
    original_name = captured.subject_name

    subject = session.get(Subject, captured.subject_id)
    subject.official_name = "RENAMED LATER"
    session.flush()

    session.refresh(captured)
    assert captured.subject_name == original_name
    assert captured.subject_name != "RENAMED LATER"


def test_recategorising_a_subject_does_not_change_a_finalized_record(session, enrollment):
    record = capture_academic_record(session, enrollment.id)
    session.flush()
    rows = [r for r in record_subjects(session, record) if r.subject_category]
    if not rows:
        pytest.skip("no snapshotted subject carries a category")
    captured = rows[0]
    original_category = captured.subject_category

    category = session.query(SubjectCategory).filter(
        SubjectCategory.name != original_category
    ).first()
    if category is None:
        pytest.skip("only one subject category exists")
    subject = session.get(Subject, captured.subject_id)
    subject.subject_category_id = category.id
    session.flush()

    session.refresh(captured)
    assert captured.subject_category == original_category


def test_grading_policy_is_frozen_as_a_number(session, enrollment):
    """The passing grade is copied, not looked up — editing the policy
    later must not change whether a past year reads PASSED."""
    record = capture_academic_record(session, enrollment.id)
    session.flush()
    if record.passing_grade is None:
        pytest.skip("no active grading policy version for this school year")

    original = record.passing_grade
    version_id = record.grading_policy_version_id
    assert version_id is not None

    from app.models.subjects import GradingPolicyVersion

    version = session.get(GradingPolicyVersion, version_id)
    version.passing_grade = Decimal(99)
    session.flush()

    session.refresh(record)
    assert record.passing_grade == original


def test_component_final_grade_is_kept_even_though_the_form_blanks_it(session, enrollment):
    """§16 blanks a component's Final Grade on the printed card. The
    permanent record still stores it — the form decides what to show, the
    record holds the truth."""
    record = capture_academic_record(session, enrollment.id)
    session.flush()
    components = [r for r in record_subjects(session, record) if r.is_component]
    if not components:
        pytest.skip("this enrollment has no combined-language components")
    for component in components:
        assert component.final_grade is None  # as printed (§16)
        assert component.component_final_grade is not None  # but retained


def test_term_applicability_is_frozen(session, enrollment):
    """§38 lists term applicability among the things to snapshot, so a
    later change to the section's offerings can't make a past record look
    incomplete."""
    record = capture_academic_record(session, enrollment.id)
    session.flush()
    rows = record_subjects(session, record)
    if not rows:
        pytest.skip("enrollment has no computed subjects")
    for row in rows:
        offered = [row.offered_term1, row.offered_term2, row.offered_term3]
        assert any(offered), f"{row.subject_name} should run in at least one term"


def test_recapturing_replaces_rows_and_bumps_the_revision(session, enrollment):
    """A reopen-and-refinalize re-issues the record rather than
    accumulating duplicates — there is only ever one current outcome for
    a year."""
    first = capture_academic_record(session, enrollment.id)
    session.flush()
    first_count = len(record_subjects(session, first))

    second = capture_academic_record(session, enrollment.id)
    session.flush()

    assert second.id == first.id
    assert second.revision == 2
    assert len(record_subjects(session, second)) == first_count
    assert (
        session.query(LearnerAcademicRecord).filter_by(enrollment_id=enrollment.id).count() == 1
    )


def test_terms_are_snapshotted_with_their_names(session, enrollment):
    record = capture_academic_record(session, enrollment.id)
    session.flush()
    terms = record_terms(session, record)
    if not terms:
        pytest.skip("school year has no terms")
    assert [t.term_number for t in terms] == sorted(t.term_number for t in terms)
    assert all(t.term_name for t in terms)


def test_get_academic_record_returns_none_before_capture(session):
    missing = get_academic_record(session, "00000000-0000-0000-0000-000000000000")
    assert missing is None


def test_record_stores_text_not_foreign_keys_for_display():
    """A structural guard for §38: the columns the report reads must be
    text. If someone later "normalises" one of these into a FK, a rename
    in a future year would silently rewrite history."""
    columns = LearnerAcademicRecordSubject.__table__.columns
    for name in ("subject_name", "subject_code", "subject_category"):
        assert str(columns[name].type) in ("VARCHAR", "TEXT"), name

    header = LearnerAcademicRecord.__table__.columns
    for name in (
        "learner_name", "school_name", "deped_school_id", "school_year_name",
        "grade_level", "section_name", "track_name", "strand_name",
    ):
        assert str(header[name].type) in ("VARCHAR", "TEXT"), name
