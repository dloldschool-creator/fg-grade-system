"""Recomputes the derived grade tables (`subject_final_grades`,
`combined_learning_area_results`, `annual_grade_summaries`) from
`term_grades` for one enrollment, using the pure functions in
app/grading_engine.py. These derived tables are caches, never entered
directly (docs/schema.md) — call `recompute_enrollment_grades` after
anything that changes a `term_grades` row.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.grading_engine import (
    compute_combined_language_final_grade,
    compute_combined_language_term_grade,
    compute_general_average,
    compute_subject_final_grade,
    determine_pass_fail,
)
from app.models.enums import CompletionStatus, PolicyVersionStatus, SubjectRemark
from app.models.grades import AnnualGradeSummary, CombinedLearningAreaResult, SubjectFinalGrade, TermGrade
from app.models.learners import Enrollment
from app.models.organization import Term
from app.models.subjects import (
    CombinedLearningArea,
    CombinedLearningAreaComponent,
    GradingPolicyVersion,
    SectionSubjectOffering,
)

DEFAULT_PASSING_GRADE = Decimal(75)


def _resolve_passing_grade(session: Session, school_year_id, offering: SectionSubjectOffering | None) -> Decimal:
    """Per-offering override wins if set; otherwise the ACTIVE policy
    version effective for this school year; otherwise any ACTIVE version;
    otherwise the hardcoded DepEd default (documented, not silently
    assumed) — see docs/schema.md `grading_policy_versions`."""
    if offering is not None and offering.grading_policy_version_id is not None:
        version = session.get(GradingPolicyVersion, offering.grading_policy_version_id)
        if version is not None:
            return Decimal(version.passing_grade)

    version = (
        session.query(GradingPolicyVersion)
        .filter_by(effective_school_year_id=school_year_id, status=PolicyVersionStatus.ACTIVE)
        .order_by(GradingPolicyVersion.version_number.desc())
        .first()
    )
    if version is None:
        version = (
            session.query(GradingPolicyVersion)
            .filter_by(status=PolicyVersionStatus.ACTIVE)
            .order_by(GradingPolicyVersion.version_number.desc())
            .first()
        )
    return Decimal(version.passing_grade) if version else DEFAULT_PASSING_GRADE


def _remark(final_grade, passing_grade) -> SubjectRemark:
    return SubjectRemark(determine_pass_fail(final_grade, passing_grade))


def recompute_enrollment_grades(session: Session, enrollment_id) -> None:
    enrollment = session.get(Enrollment, enrollment_id)
    if enrollment is None:
        return

    now = datetime.now(timezone.utc)

    offerings = (
        session.query(SectionSubjectOffering)
        .filter_by(section_id=enrollment.section_id, school_year_id=enrollment.school_year_id)
        .all()
    )
    terms = {t.id: t for t in session.query(Term).filter_by(school_year_id=enrollment.school_year_id).all()}

    # subject_id -> {term_number: offering}
    offerings_by_subject: dict = {}
    for offering in offerings:
        term = terms.get(offering.term_id)
        if term is None:
            continue
        offerings_by_subject.setdefault(offering.subject_id, {})[term.term_number] = offering

    term_grades = {
        (tg.section_subject_offering_id, tg.term_id): tg
        for tg in session.query(TermGrade).filter_by(enrollment_id=enrollment_id).all()
    }

    # subject_id -> computed final grade (Decimal | None)
    subject_finals: dict = {}
    for subject_id, term_offerings in offerings_by_subject.items():
        required_terms = set(term_offerings.keys())
        grades_by_term = {}
        for term_number, offering in term_offerings.items():
            tg = term_grades.get((offering.id, offering.term_id))
            grades_by_term[term_number] = tg.official_grade if tg else None

        final_grade = compute_subject_final_grade(grades_by_term, required_terms)
        subject_finals[subject_id] = final_grade

        any_offering = next(iter(term_offerings.values()))
        passing_grade = _resolve_passing_grade(session, enrollment.school_year_id, any_offering)

        record = (
            session.query(SubjectFinalGrade)
            .filter_by(
                enrollment_id=enrollment_id, subject_id=subject_id, school_year_id=enrollment.school_year_id
            )
            .one_or_none()
        )
        if record is None:
            record = SubjectFinalGrade(
                enrollment_id=enrollment_id, subject_id=subject_id, school_year_id=enrollment.school_year_id
            )
            session.add(record)
        else:
            record.version += 1
        record.final_grade = final_grade
        record.remark = _remark(final_grade, passing_grade)
        record.computed_at = now

    # Combined learning areas (§14-16, §62) — Grade 11 language pair.
    combined_component_subject_ids: set = set()
    combined_area_finals: dict = {}  # combined_learning_area_id -> Decimal | None
    combined_areas = (
        session.query(CombinedLearningArea)
        .filter_by(grade_level_id=enrollment.grade_level_id)
        .all()
    )
    for area in combined_areas:
        components = (
            session.query(CombinedLearningAreaComponent)
            .filter_by(combined_learning_area_id=area.id)
            .order_by(CombinedLearningAreaComponent.display_order)
            .all()
        )
        if len(components) != 2:
            continue  # not fully configured — skip rather than guess
        comp1_id, comp2_id = components[0].subject_id, components[1].subject_id
        if comp1_id not in offerings_by_subject or comp2_id not in offerings_by_subject:
            continue  # this section doesn't offer both components — not applicable here

        combined_component_subject_ids.update([comp1_id, comp2_id])

        def term_grade_for(subject_id, term_number):
            offering = offerings_by_subject.get(subject_id, {}).get(term_number)
            if offering is None:
                return None
            tg = term_grades.get((offering.id, offering.term_id))
            return tg.official_grade if tg else None

        term_combined = {
            n: compute_combined_language_term_grade(term_grade_for(comp1_id, n), term_grade_for(comp2_id, n))
            for n in (1, 2, 3)
        }
        combined_final = compute_combined_language_final_grade(
            subject_finals.get(comp1_id), subject_finals.get(comp2_id)
        )
        combined_area_finals[area.id] = combined_final

        any_offering = next(iter(offerings_by_subject[comp1_id].values()))
        passing_grade = _resolve_passing_grade(session, enrollment.school_year_id, any_offering)

        result = (
            session.query(CombinedLearningAreaResult)
            .filter_by(
                enrollment_id=enrollment_id,
                combined_learning_area_id=area.id,
                school_year_id=enrollment.school_year_id,
            )
            .one_or_none()
        )
        if result is None:
            result = CombinedLearningAreaResult(
                enrollment_id=enrollment_id,
                combined_learning_area_id=area.id,
                school_year_id=enrollment.school_year_id,
            )
            session.add(result)
        else:
            result.version += 1
        result.term1_combined = term_combined[1]
        result.term2_combined = term_combined[2]
        result.term3_combined = term_combined[3]
        result.final_grade = combined_final
        result.remark = _remark(combined_final, passing_grade)
        result.computed_at = now

    # Annual General Average (§19, §20, §61) — every subject's final,
    # EXCEPT combined-language components counted individually; the
    # combined area's own final substitutes for its pair, once.
    applicable_finals = [
        final for subject_id, final in subject_finals.items() if subject_id not in combined_component_subject_ids
    ] + list(combined_area_finals.values())

    general_average = compute_general_average(applicable_finals)
    completion_status = (
        CompletionStatus.COMPLETE
        if applicable_finals and all(f is not None for f in applicable_finals)
        else CompletionStatus.INCOMPLETE
    )
    non_null_finals = [f for f in applicable_finals if f is not None]
    lowest_final_grade = min(non_null_finals) if completion_status == CompletionStatus.COMPLETE else None
    default_passing = _resolve_passing_grade(session, enrollment.school_year_id, None)
    failed_subject_count = sum(1 for f in non_null_finals if f < default_passing)

    summary = session.query(AnnualGradeSummary).filter_by(enrollment_id=enrollment_id).one_or_none()
    if summary is None:
        summary = AnnualGradeSummary(enrollment_id=enrollment_id, school_year_id=enrollment.school_year_id)
        session.add(summary)
    else:
        summary.version += 1
    summary.general_average = general_average
    summary.lowest_final_grade = lowest_final_grade
    summary.failed_subject_count = failed_subject_count
    summary.completion_status = completion_status
    summary.computed_at = now

    session.commit()
