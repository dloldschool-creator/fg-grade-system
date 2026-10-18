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
from app.report_card import ReportCardContext, build_learning_area_rows, load_report_context


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

    A 1-element wrapper over `capture_academic_records_batch`, so the
    single Finalize button and the section-wide one share one
    implementation. Flushes, never commits.
    """
    return capture_academic_records_batch(session, [enrollment_id], user_id)[0]


def _award_rank(award) -> tuple:
    """Which of several ELIGIBLE_AWARDED rows for one enrollment/term is
    frozen into the record. There can be several (one per award policy
    version, plus overrides), the record has room for one name, and the old
    `.first()` had no ORDER BY, so it was Postgres's whim. The lowest rank
    wins: an administrator's override (§40 — a deliberate decision) over a
    computed award, then a named award over an unnamed one, then the name
    alphabetically so the choice is stable."""
    return (not bool(award.is_override), award.award_name is None, award.award_name or "")


def capture_academic_records_batch(
    session: Session, enrollment_ids, user_id=None
) -> list[LearnerAcademicRecord]:
    """`capture_academic_record` for many enrollments in a fixed number of
    queries (the same split as `load_report_context` /
    `recompute_enrollment_grades_batch`): shared lookups are loaded once,
    per-enrollment rows with one `IN (...)` each, and the report-card rows
    come from one `load_report_context` per section. The maths and the §16
    language-pair rule stay in `build_learning_area_rows`.

    Returns the records in the order of `enrollment_ids`. Flushes, never
    commits — the caller owns the transaction.
    """
    enrollment_ids = list(enrollment_ids)
    if not enrollment_ids:
        return []

    enrollments = {
        e.id: e for e in session.query(Enrollment).filter(Enrollment.id.in_(enrollment_ids)).all()
    }
    ordered = [enrollments[i] for i in enrollment_ids]  # KeyError == unknown enrollment

    learner_ids = {e.learner_id for e in ordered}
    section_ids = {e.section_id for e in ordered}
    grade_level_ids = {e.grade_level_id for e in ordered if e.grade_level_id}
    year_ids = {e.school_year_id for e in ordered}

    learners = {l.id: l for l in session.query(Learner).filter(Learner.id.in_(learner_ids)).all()}
    sections = {s.id: s for s in session.query(Section).filter(Section.id.in_(section_ids)).all()}
    grade_levels = (
        {g.id: g for g in session.query(GradeLevel).filter(GradeLevel.id.in_(grade_level_ids)).all()}
        if grade_level_ids
        else {}
    )
    track_ids = {s.track_id for s in sections.values() if s.track_id}
    strand_ids = {s.strand_id for s in sections.values() if s.strand_id}
    tracks = (
        {t.id: t for t in session.query(Track).filter(Track.id.in_(track_ids)).all()}
        if track_ids
        else {}
    )
    strands = (
        {t.id: t for t in session.query(Strand).filter(Strand.id.in_(strand_ids)).all()}
        if strand_ids
        else {}
    )
    school = session.query(School).one_or_none()
    school_years = {
        y.id: y for y in session.query(SchoolYear).filter(SchoolYear.id.in_(year_ids)).all()
    }
    summaries = {
        s.enrollment_id: s
        for s in session.query(AnnualGradeSummary)
        .filter(AnnualGradeSummary.enrollment_id.in_(enrollment_ids))
        .all()
    }
    policies = {year_id: _resolve_policy(session, year_id) for year_id in year_ids}

    existing = {
        r.enrollment_id: r
        for r in session.query(LearnerAcademicRecord)
        .filter(LearnerAcademicRecord.enrollment_id.in_(enrollment_ids))
        .all()
    }
    if existing:
        _clear_children_batch(session, [r.id for r in existing.values()])

    # Awards: one query for both the annual (term_id NULL) and per-term ones.
    awards: dict = {}
    for award in (
        session.query(LearnerAward)
        .filter(
            LearnerAward.enrollment_id.in_(enrollment_ids),
            LearnerAward.award_result == AwardResult.ELIGIBLE_AWARDED,
        )
        .all()
    ):
        key = (award.enrollment_id, award.term_id)
        rank = _award_rank(award)
        if key not in awards or rank < awards[key][0]:
            awards[key] = (rank, award)

    records: dict = {}
    for enrollment in ordered:
        if enrollment.id in records:
            continue
        learner = learners[enrollment.learner_id]
        section = sections.get(enrollment.section_id)
        school_year = school_years.get(enrollment.school_year_id)
        grade_level = grade_levels.get(enrollment.grade_level_id) if enrollment.grade_level_id else None
        track = tracks.get(section.track_id) if section and section.track_id else None
        strand = strands.get(section.strand_id) if section and section.strand_id else None
        summary = summaries.get(enrollment.id)
        version, policy_label, passing_grade = policies[enrollment.school_year_id]

        record = existing.get(enrollment.id)
        if record is None:
            record = LearnerAcademicRecord(enrollment_id=enrollment.id, learner_id=learner.id)
            session.add(record)
        else:
            record.revision = (record.revision or 0) + 1

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
        # How that average was reached, frozen as text and a number (§38). A
        # later revision of DO 017's Table 19 must not re-explain — or worse,
        # re-derive — a General Average that has already been issued.
        record.averaging_method = (
            summary.averaging_method.value
            if summary and summary.averaging_method is not None
            else None
        )
        record.total_units = summary.total_units if summary else None
        record.completion_status = (
            summary.completion_status if summary else CompletionStatus.INCOMPLETE
        )
        record.general_average_remark = _general_average_remark(summary, passing_grade)
        annual_award = awards.get((enrollment.id, None))
        record.award_name = annual_award[1].award_name if annual_award else None
        record.passing_grade = passing_grade
        record.grading_policy_version_id = version.id if version else None
        record.grading_policy_label = policy_label
        record.snapshot_at = datetime.now(timezone.utc)
        record.snapshot_by_user_id = user_id
        records[enrollment.id] = record

    # Flush only once the NOT NULL columns are populated — and before the
    # child rows, which need record.id. Flushing straight after add()
    # would fire the INSERT with every field still None.
    session.flush()

    lookup = _subject_lookup(session)
    terms_by_year = _terms_by_year(session, year_ids)
    term_summaries = {
        (s.enrollment_id, s.term_id): s
        for s in session.query(TermGradeSummary)
        .filter(TermGradeSummary.enrollment_id.in_(enrollment_ids))
        .all()
    }

    # `load_report_context` shares offerings and combined areas, so it takes
    # one section's learners at a time.
    groups: dict = {}
    for enrollment in enrollments.values():
        key = (enrollment.section_id, enrollment.school_year_id, enrollment.grade_level_id)
        groups.setdefault(key, []).append(enrollment)
    for group in groups.values():
        context = load_report_context(session, group)
        for enrollment in group:
            record = records[enrollment.id]
            _capture_subjects(session, record, enrollment, context, lookup)
            _capture_terms(
                record, enrollment, terms_by_year[enrollment.school_year_id],
                term_summaries, awards, session,
            )
    return [records[i] for i in enrollment_ids]


def _clear_children_batch(session: Session, record_ids) -> None:
    session.query(LearnerAcademicRecordSubject).filter(
        LearnerAcademicRecordSubject.learner_academic_record_id.in_(record_ids)
    ).delete(synchronize_session=False)
    session.query(LearnerAcademicRecordTerm).filter(
        LearnerAcademicRecordTerm.learner_academic_record_id.in_(record_ids)
    ).delete(synchronize_session=False)


def _terms_by_year(session: Session, year_ids) -> dict:
    by_year: dict = {year_id: [] for year_id in year_ids}
    for term in (
        session.query(Term)
        .filter(Term.school_year_id.in_(year_ids))
        .order_by(Term.term_number)
        .all()
    ):
        by_year[term.school_year_id].append(term)
    return by_year


def _general_average_remark(summary, passing_grade) -> str | None:
    if summary is None or summary.general_average is None or passing_grade is None:
        return None
    return "PASSED" if summary.general_average >= passing_grade else "FAILED"


def _capture_subjects(
    session: Session,
    record: LearnerAcademicRecord,
    enrollment: Enrollment,
    context: ReportCardContext,
    lookup: dict,
) -> None:
    """One row per learning area, in the §16 print order.

    The names and categories are resolved here, once, and stored as text.
    A component keeps its own Final Grade in `component_final_grade` even
    though §16 blanks that cell on the printed card — the record holds the
    truth, the form decides what to show.
    """
    for order, row in enumerate(build_learning_area_rows(session, enrollment, context)):
        subject_id, code, category = lookup.get(row.name, (None, None, None))
        component_final = None
        if row.is_component and subject_id is not None:
            final = context.finals.get((enrollment.id, subject_id))
            component_final = final.final_grade if final else None
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
                units_per_term=row.units_per_term,
                units=row.units,
                unrounded_final_grade=row.unrounded_final_grade,
                remark=row.remark,
                is_combined_parent=not row.is_component and "/" in row.name,
                is_component=row.is_component,
                component_final_grade=component_final,
            )
        )


def _capture_terms(record, enrollment, terms, term_summaries: dict, awards: dict, session) -> None:
    comments = {
        1: enrollment.term1_adviser_comment,
        2: enrollment.term2_adviser_comment,
        3: enrollment.term3_adviser_comment,
    }
    for term in terms:
        summary = term_summaries.get((enrollment.id, term.id))
        award = awards.get((enrollment.id, term.id))
        session.add(
            LearnerAcademicRecordTerm(
                learner_academic_record_id=record.id,
                term_number=term.term_number,
                term_name=term.name,
                term_average=summary.term_average if summary else None,
                averaging_method=(
                    summary.averaging_method.value
                    if summary and summary.averaging_method is not None
                    else None
                ),
                total_units=summary.total_units if summary else None,
                completion_status=(
                    summary.completion_status if summary else CompletionStatus.INCOMPLETE
                ),
                award_name=award[1].award_name if award else None,
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
