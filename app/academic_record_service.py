"""Capturing the permanent learner academic record (§37, §38).

Called when an enrollment's grades are finalized. Everything the record
shows is **copied** at that moment — subject names, categories, the
grading policy's passing grade, the school's name — so that later edits
to any of those leave the finalized year untouched (§38).

Nothing in here recomputes a grade. It reads the already-computed derived
tables (`subject_final_grades`, `combined_learning_area_results`,
`term_grade_summaries`, `annual_grade_summaries`) exactly as the Grade
Summary screen and the SF9 do, via `app/report_card.py` for the §16 row
ordering. If those haven't been recomputed, the snapshot would freeze
stale numbers — which is why capture is wired to finalization, and
finalization is gated on the record being COMPLETE (§23).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.academic_record import (
    LearnerAcademicRecord,
    LearnerAcademicRecordSubject,
    LearnerAcademicRecordTerm,
)
from app.models.academic_structure import GradeLevel, Section, Strand, Track
from app.models.awards import LearnerAward
from app.models.enums import AwardResult, CompletionStatus, PolicyVersionStatus
from app.models.grades import AnnualGradeSummary, TermGradeSummary
from app.models.learners import Enrollment, Learner
from app.models.organization import School, SchoolYear, Term
from app.models.subjects import GradingPolicy, GradingPolicyVersion, Subject, SubjectCategory
from app.report_card import build_learning_area_rows


def _learner_name(learner: Learner) -> str:
    middle = f" {learner.middle_name}" if learner.middle_name else ""
    extension = f" {learner.extension_name}" if learner.extension_name else ""
    return f"{learner.last_name}, {learner.first_name}{middle}{extension}".strip()


def _resolve_policy(session: Session, school_year_id):
    """The grading policy version in force for this year, and a readable
    label for it. Both are frozen into the record — a later edit to the
    policy must not change how a finalized year reads (§38)."""
    version = (
        session.query(GradingPolicyVersion)
        .filter_by(effective_school_year_id=school_year_id, status=PolicyVersionStatus.ACTIVE)
        .order_by(GradingPolicyVersion.version_number.desc())
        .first()
    )
    if version is None:
        return None, None, None
    policy = session.get(GradingPolicy, version.grading_policy_id)
    label = f"{policy.name} v{version.version_number}" if policy else f"v{version.version_number}"
    return version, label, version.passing_grade


def _subject_lookup(session: Session):
    categories = {c.id: c.name for c in session.query(SubjectCategory).all()}
    subjects = {}
    for subject in session.query(Subject).all():
        subjects[subject.official_name] = (
            subject.id,
            subject.code,
            categories.get(subject.subject_category_id),
        )
    return subjects


def capture_academic_record(session: Session, enrollment_id, user_id=None) -> LearnerAcademicRecord:
    """Freezes one finalized school year for one learner.

    Re-capturing (after an audited reopen and re-finalize) replaces the
    previous rows and bumps `revision`, rather than accumulating
    duplicates — the record describes the year's outcome, and there is
    only ever one current outcome.
    """
    enrollment = session.get(Enrollment, enrollment_id)
    learner = session.get(Learner, enrollment.learner_id)
    section = session.get(Section, enrollment.section_id)
    school = session.query(School).one_or_none()
    school_year = session.get(SchoolYear, enrollment.school_year_id)
    grade_level = (
        session.get(GradeLevel, enrollment.grade_level_id) if enrollment.grade_level_id else None
    )
    track = session.get(Track, section.track_id) if section and section.track_id else None
    strand = session.get(Strand, section.strand_id) if section and section.strand_id else None
    summary = (
        session.query(AnnualGradeSummary).filter_by(enrollment_id=enrollment_id).one_or_none()
    )
    version, policy_label, passing_grade = _resolve_policy(session, enrollment.school_year_id)

    record = (
        session.query(LearnerAcademicRecord).filter_by(enrollment_id=enrollment_id).one_or_none()
    )
    if record is None:
        record = LearnerAcademicRecord(enrollment_id=enrollment_id, learner_id=learner.id)
        session.add(record)
    else:
        record.revision = (record.revision or 0) + 1
        _clear_children(session, record.id)

    record.lrn = learner.lrn
    record.learner_name = _learner_name(learner)
    record.school_name = school.school_name if school else None
    record.deped_school_id = school.deped_school_id if school else None
    record.school_year_name = school_year.name if school_year else ""
    record.grade_level = (grade_level.code or grade_level.name) if grade_level else None
    record.section_name = section.name if section else None
    record.track_name = track.name if track else None
    record.strand_name = strand.name if strand else None
    record.general_average = summary.general_average if summary else None
    record.completion_status = (
        summary.completion_status if summary else CompletionStatus.INCOMPLETE
    )
    record.general_average_remark = _general_average_remark(summary, passing_grade)
    record.award_name = _annual_award_name(session, enrollment_id)
    record.passing_grade = passing_grade
    record.grading_policy_version_id = version.id if version else None
    record.grading_policy_label = policy_label
    record.snapshot_at = datetime.now(timezone.utc)
    record.snapshot_by_user_id = user_id

    # Flush only once the NOT NULL columns are populated — and before the
    # child rows, which need record.id. Flushing straight after add()
    # would fire the INSERT with every field still None.
    session.flush()

    _capture_subjects(session, record, enrollment)
    _capture_terms(session, record, enrollment)
    return record


def _clear_children(session: Session, record_id) -> None:
    session.query(LearnerAcademicRecordSubject).filter_by(
        learner_academic_record_id=record_id
    ).delete(synchronize_session=False)
    session.query(LearnerAcademicRecordTerm).filter_by(
        learner_academic_record_id=record_id
    ).delete(synchronize_session=False)


def _general_average_remark(summary, passing_grade) -> str | None:
    if summary is None or summary.general_average is None or passing_grade is None:
        return None
    return "PASSED" if summary.general_average >= passing_grade else "FAILED"


def _annual_award_name(session: Session, enrollment_id) -> str | None:
    award = (
        session.query(LearnerAward)
        .filter_by(enrollment_id=enrollment_id, term_id=None, award_result=AwardResult.ELIGIBLE_AWARDED)
        .first()
    )
    return award.award_name if award else None


def _capture_subjects(session: Session, record: LearnerAcademicRecord, enrollment: Enrollment) -> None:
    """One row per learning area, in the §16 print order.

    The names and categories are resolved here, once, and stored as text.
    A component keeps its own Final Grade in `component_final_grade` even
    though §16 blanks that cell on the printed card — the record holds the
    truth, the form decides what to show.
    """
    lookup = _subject_lookup(session)
    for order, row in enumerate(build_learning_area_rows(session, enrollment)):
        subject_id, code, category = lookup.get(row.name, (None, None, None))
        session.add(
            LearnerAcademicRecordSubject(
                learner_academic_record_id=record.id,
                display_order=order,
                subject_name=row.name,
                subject_code=code,
                subject_category=category,
                subject_id=subject_id,
                offered_term1=row.is_offered(1),
                offered_term2=row.is_offered(2),
                offered_term3=row.is_offered(3),
                term1_grade=row.term_grades.get(1),
                term2_grade=row.term_grades.get(2),
                term3_grade=row.term_grades.get(3),
                final_grade=row.final_grade,
                remark=row.remark,
                is_combined_parent=not row.is_component and "/" in row.name,
                is_component=row.is_component,
                component_final_grade=_component_final_grade(session, enrollment, subject_id)
                if row.is_component
                else None,
            )
        )


def _component_final_grade(session: Session, enrollment: Enrollment, subject_id):
    if subject_id is None:
        return None
    from app.models.grades import SubjectFinalGrade

    final = (
        session.query(SubjectFinalGrade)
        .filter_by(enrollment_id=enrollment.id, subject_id=subject_id)
        .one_or_none()
    )
    return final.final_grade if final else None


def _capture_terms(session: Session, record: LearnerAcademicRecord, enrollment: Enrollment) -> None:
    terms = (
        session.query(Term)
        .filter_by(school_year_id=enrollment.school_year_id)
        .order_by(Term.term_number)
        .all()
    )
    comments = {
        1: enrollment.term1_adviser_comment,
        2: enrollment.term2_adviser_comment,
        3: enrollment.term3_adviser_comment,
    }
    for term in terms:
        summary = (
            session.query(TermGradeSummary)
            .filter_by(enrollment_id=enrollment.id, term_id=term.id)
            .one_or_none()
        )
        award = (
            session.query(LearnerAward)
            .filter_by(
                enrollment_id=enrollment.id,
                term_id=term.id,
                award_result=AwardResult.ELIGIBLE_AWARDED,
            )
            .first()
        )
        session.add(
            LearnerAcademicRecordTerm(
                learner_academic_record_id=record.id,
                term_number=term.term_number,
                term_name=term.name,
                term_average=summary.term_average if summary else None,
                completion_status=(
                    summary.completion_status if summary else CompletionStatus.INCOMPLETE
                ),
                award_name=award.award_name if award else None,
                adviser_comment=comments.get(term.term_number),
            )
        )


def get_academic_record(session: Session, enrollment_id) -> LearnerAcademicRecord | None:
    return (
        session.query(LearnerAcademicRecord).filter_by(enrollment_id=enrollment_id).one_or_none()
    )


def record_subjects(session: Session, record: LearnerAcademicRecord) -> list[LearnerAcademicRecordSubject]:
    return (
        session.query(LearnerAcademicRecordSubject)
        .filter_by(learner_academic_record_id=record.id)
        .order_by(LearnerAcademicRecordSubject.display_order)
        .all()
    )


def record_terms(session: Session, record: LearnerAcademicRecord) -> list[LearnerAcademicRecordTerm]:
    return (
        session.query(LearnerAcademicRecordTerm)
        .filter_by(learner_academic_record_id=record.id)
        .order_by(LearnerAcademicRecordTerm.term_number)
        .all()
    )
