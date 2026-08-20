"""Recomputes the derived grade tables (`subject_final_grades`,
`combined_learning_area_results`, `annual_grade_summaries`) from
`term_grades` for one enrollment, using the pure functions in
app/grading_engine.py. These derived tables are caches, never entered
directly (docs/schema.md) — call `recompute_enrollment_grades` after
anything that changes a `term_grades` row.

**Which averaging rules apply is resolved, not assumed.** DepEd Order 017
s. 2026 makes the Term Average and General Average unit-weighted under the
Strengthened SHS Curriculum, and phases that in by grade level, so this
module asks `app/curriculum_policy.py` what is in force for the learner's
(school year, grade level) and passes the answer to the engine. A database
with no DO 017 policy version resolves to the pre-DO-017 rules and computes
exactly what it always did (CLAUDE.md rule 6).
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.curriculum_policy import (
    combined_area_units_per_term as resolve_combined_area_units,
)
from app.curriculum_policy import (
    load_offering_units,
    resolve_averaging_rules,
)
from app.grading_engine import (
    GradeUnits,
    compute_combined_language_final_grade,
    compute_combined_language_term_grade,
    compute_general_average,
    compute_subject_final_grade,
    compute_subject_final_grade_exact,
    compute_term_average,
    determine_pass_fail,
    total_units,
)
from app.models.enums import CompletionStatus, PolicyVersionStatus, SubjectRemark
from app.models.grades import (
    AnnualGradeSummary,
    CombinedLearningAreaResult,
    SubjectFinalGrade,
    TermGrade,
    TermGradeSummary,
)
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

    # The averaging rules in force for this learner's year and grade level,
    # and the units every offering carries. Both are resolved once per
    # recompute — two extra queries for the whole enrollment, not one per
    # subject.
    rules = resolve_averaging_rules(
        session, enrollment.school_year_id, enrollment.grade_level_id
    )
    units_by_offering = load_offering_units(session, offerings)

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
    # The same finals without the reported rounding, plus the annual units
    # each subject carries. DO 017 weights the General Average by units per
    # term × the terms the subject actually ran — 6 for a three-term core, 3
    # for a one-term academic elective — which is summed here from the real
    # offerings rather than assumed from the subject's category.
    subject_finals_exact: dict = {}
    subject_annual_units: dict = {}
    subject_units_per_term: dict = {}
    for subject_id, term_offerings in offerings_by_subject.items():
        required_terms = set(term_offerings.keys())
        grades_by_term = {}
        for term_number, offering in term_offerings.items():
            tg = term_grades.get((offering.id, offering.term_id))
            grades_by_term[term_number] = tg.official_grade if tg else None

        final_grade = compute_subject_final_grade(grades_by_term, required_terms)
        subject_finals[subject_id] = final_grade
        subject_finals_exact[subject_id] = compute_subject_final_grade_exact(
            grades_by_term, required_terms
        )
        subject_annual_units[subject_id] = sum(
            (units_by_offering.get(o.id, Decimal(1)) for o in term_offerings.values()),
            Decimal(0),
        )
        first_term = min(term_offerings)
        subject_units_per_term[subject_id] = units_by_offering.get(
            term_offerings[first_term].id, Decimal(1)
        )

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
        record.units_per_term = subject_units_per_term[subject_id]
        record.units = subject_annual_units[subject_id]
        record.unrounded_final_grade = subject_finals_exact[subject_id]
        record.remark = _remark(final_grade, passing_grade)
        record.computed_at = now

    # Combined learning areas (§14-16, §62) — Grade 11 language pair.
    combined_component_subject_ids: set = set()
    combined_area_finals: dict = {}  # combined_learning_area_id -> Decimal | None
    combined_area_finals_exact: dict = {}
    combined_area_units: dict = {}          # area_id -> annual units
    combined_area_units_per_term: dict = {}  # area_id -> units per term
    # area_id -> {term_number: combined grade}, kept so the Term Average can
    # substitute the pair for its two components when the policy says to.
    combined_area_term_grades: dict = {}
    # component subject_id -> area_id, for the same substitution.
    component_to_area: dict = {}
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
        combined_area_term_grades[area.id] = term_combined
        component_to_area[comp1_id] = area.id
        component_to_area[comp2_id] = area.id

        # The pair as ONE learning area: one unit weight, and one unrounded
        # final built from the components' unrounded finals, so a
        # unit-weighted General Average is reproducible from its own record.
        exact1, exact2 = subject_finals_exact.get(comp1_id), subject_finals_exact.get(comp2_id)
        combined_area_finals_exact[area.id] = (
            (exact1 + exact2) / Decimal(2) if exact1 is not None and exact2 is not None else None
        )
        per_term = resolve_combined_area_units(
            area,
            [
                subject_units_per_term.get(comp1_id, Decimal(1)),
                subject_units_per_term.get(comp2_id, Decimal(1)),
            ],
        )
        combined_area_units_per_term[area.id] = per_term
        # The pair runs in whichever terms either component runs in — counted
        # once, not once per component.
        pair_terms = set(offerings_by_subject.get(comp1_id, {})) | set(
            offerings_by_subject.get(comp2_id, {})
        )
        combined_area_units[area.id] = per_term * Decimal(len(pair_terms))

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
        result.units_per_term = combined_area_units_per_term[area.id]
        result.units = combined_area_units[area.id]
        result.unrounded_final_grade = combined_area_finals_exact[area.id]
        result.remark = _remark(combined_final, passing_grade)
        result.computed_at = now

    # Per-term summaries (§17 Term Average, §22 Term Completion Check;
    # DO 017 s. 2026 Annex E section A).
    #
    # By default this uses raw per-subject term grades, NOT the combined
    # language grade — §17 keeps the Grade 11 pair as two separate entries,
    # the opposite of the General Average rule below. DO 017 makes the pair a
    # single core subject and so counts it once; a policy version that sets
    # `combine_language_pair_in_term_average` switches to that reading, and
    # `app/report_card.build_term_subject_rows` follows the same switch so the
    # printed term card always itemises exactly what its average is made of.
    default_passing = _resolve_passing_grade(session, enrollment.school_year_id, None)
    for term in terms.values():
        grades_this_term: list = []
        entries_this_term: list = []
        areas_counted: set = set()
        for subject_id, term_offerings in offerings_by_subject.items():
            offering = term_offerings.get(term.term_number)
            if offering is None:
                continue  # subject not offered this term — not an omission
            tg = term_grades.get((offering.id, offering.term_id))
            grade = tg.official_grade if tg else None
            grades_this_term.append(grade)

            area_id = component_to_area.get(subject_id)
            if rules.combine_language_pair_in_term_average and area_id is not None:
                # The pair contributes one entry, at the parent's weight.
                if area_id in areas_counted:
                    continue
                areas_counted.add(area_id)
                entries_this_term.append(
                    GradeUnits(
                        combined_area_term_grades.get(area_id, {}).get(term.term_number),
                        combined_area_units_per_term.get(area_id, Decimal(1)),
                    )
                )
                continue

            entries_this_term.append(
                GradeUnits(grade, units_by_offering.get(offering.id, Decimal(1)))
            )

        term_average = compute_term_average(entries_this_term, rules.method)
        term_total_units = total_units(entries_this_term, rules.method)
        encoded = [g for g in grades_this_term if g is not None]
        term_completion = (
            CompletionStatus.COMPLETE
            if grades_this_term and term_average is not None
            else CompletionStatus.INCOMPLETE
        )

        term_summary = (
            session.query(TermGradeSummary)
            .filter_by(enrollment_id=enrollment_id, term_id=term.id)
            .one_or_none()
        )
        if term_summary is None:
            term_summary = TermGradeSummary(
                enrollment_id=enrollment_id,
                school_year_id=enrollment.school_year_id,
                term_id=term.id,
            )
            session.add(term_summary)
        else:
            term_summary.version += 1
        term_summary.term_average = term_average
        term_summary.averaging_method = rules.method
        term_summary.total_units = term_total_units
        term_summary.lowest_term_grade = min(encoded) if encoded else None
        term_summary.failed_subject_count = sum(1 for g in encoded if g < default_passing)
        term_summary.completion_status = term_completion
        term_summary.computed_at = now

    # Annual General Average (§19, §20, §61) — every subject's final,
    # EXCEPT combined-language components counted individually; the
    # combined area's own final substitutes for its pair, once.
    applicable_finals = [
        final for subject_id, final in subject_finals.items() if subject_id not in combined_component_subject_ids
    ] + list(combined_area_finals.values())

    # The same list as (grade, annual units) pairs for the weighted rule.
    # `average_from_unrounded_finals` picks which final is weighted: DO 017's
    # worked examples use the unrounded one, its printed tables show the
    # rounded one, and the two do not always agree to the whole number.
    def _weighted_final(rounded, exact):
        return exact if (rules.average_from_unrounded_finals and exact is not None) else rounded

    weighted_finals = [
        GradeUnits(
            _weighted_final(final, subject_finals_exact.get(subject_id)),
            subject_annual_units.get(subject_id, Decimal(1)),
        )
        for subject_id, final in subject_finals.items()
        if subject_id not in combined_component_subject_ids
    ] + [
        GradeUnits(
            _weighted_final(final, combined_area_finals_exact.get(area_id)),
            combined_area_units.get(area_id, Decimal(1)),
        )
        for area_id, final in combined_area_finals.items()
    ]

    general_average = compute_general_average(weighted_finals, rules.method)
    annual_total_units = total_units(weighted_finals, rules.method)
    completion_status = (
        CompletionStatus.COMPLETE
        if applicable_finals and all(f is not None for f in applicable_finals)
        else CompletionStatus.INCOMPLETE
    )
    non_null_finals = [f for f in applicable_finals if f is not None]
    lowest_final_grade = min(non_null_finals) if completion_status == CompletionStatus.COMPLETE else None
    failed_subject_count = sum(1 for f in non_null_finals if f < default_passing)

    summary = session.query(AnnualGradeSummary).filter_by(enrollment_id=enrollment_id).one_or_none()
    if summary is None:
        summary = AnnualGradeSummary(enrollment_id=enrollment_id, school_year_id=enrollment.school_year_id)
        session.add(summary)
    else:
        summary.version += 1
    summary.general_average = general_average
    summary.averaging_method = rules.method
    summary.total_units = annual_total_units
    summary.lowest_final_grade = lowest_final_grade
    summary.failed_subject_count = failed_subject_count
    summary.completion_status = completion_status
    summary.computed_at = now

    session.commit()
