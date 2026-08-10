"""Award eligibility computation (§24). Two policy shapes share one
function: a single-tier policy (e.g. Academic Excellence — flat
min_general_average/min_lowest_final_grade thresholds) and a tiered
policy (e.g. Legacy Honors — `tier_thresholds` picks the highest
General-Average tier the learner clears). Always records *why*, never
just "Not Eligible" (§24 explicitly requires an explanation) and reuses
`annual_grade_summaries` — it never recomputes grades itself.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.awards import AwardPolicy, AwardPolicyVersion, LearnerAward
from app.models.enums import AwardResult, CompletionStatus
from app.models.grades import AnnualGradeSummary
from app.models.learners import Enrollment


def _evaluate(version: AwardPolicyVersion, policy_name: str, summary, enrollment):
    reasons: list[str] = []
    eligible = True

    if version.require_complete_record and (
        summary is None or summary.completion_status != CompletionStatus.COMPLETE
    ):
        eligible = False
        reasons.append("Annual record is not COMPLETE.")

    if version.require_no_derogatory_record and enrollment.derogatory_record:
        eligible = False
        reasons.append("Learner has a derogatory record.")

    if version.require_no_failed_subject and summary and (summary.failed_subject_count or 0) > 0:
        eligible = False
        reasons.append(f"{summary.failed_subject_count} failed subject(s).")

    general_average = summary.general_average if summary else None
    lowest_final_grade = summary.lowest_final_grade if summary else None
    award_name = None

    if version.tier_thresholds:
        if general_average is None:
            eligible = False
            reasons.append("General Average not yet computed.")
        elif eligible:
            for tier in sorted(
                version.tier_thresholds, key=lambda t: -t["min_general_average"]
            ):
                if general_average >= tier["min_general_average"]:
                    award_name = tier["label"]
                    break
            if award_name is None:
                eligible = False
                reasons.append(
                    f"General Average {general_average} below the lowest tier threshold "
                    f"({min(t['min_general_average'] for t in version.tier_thresholds)})."
                )
    else:
        if version.min_general_average is not None:
            if general_average is None or general_average < version.min_general_average:
                eligible = False
                reasons.append(
                    f"General Average {general_average if general_average is not None else 'N/A'} "
                    f"below required {version.min_general_average}."
                )
        if version.min_lowest_final_grade is not None:
            if lowest_final_grade is None or lowest_final_grade < version.min_lowest_final_grade:
                eligible = False
                reasons.append(
                    f"Lowest Final Grade {lowest_final_grade if lowest_final_grade is not None else 'N/A'} "
                    f"below required {version.min_lowest_final_grade}."
                )
        if eligible:
            award_name = policy_name

    reason = "; ".join(reasons) if reasons else "Meets all requirements."
    return eligible, award_name, reason


def compute_award_eligibility(session: Session, enrollment_id, award_policy_version_id) -> LearnerAward:
    """Computes and upserts the `learner_awards` row. A row with
    `is_override=True` is left untouched — an admin override persists
    until explicitly cleared (see clear_award_override), not silently
    overwritten by the next recompute."""
    existing = (
        session.query(LearnerAward)
        .filter_by(enrollment_id=enrollment_id, award_policy_version_id=award_policy_version_id)
        .one_or_none()
    )
    if existing is not None and existing.is_override:
        return existing

    version = session.get(AwardPolicyVersion, award_policy_version_id)
    policy = session.get(AwardPolicy, version.award_policy_id)
    enrollment = session.get(Enrollment, enrollment_id)
    summary = session.query(AnnualGradeSummary).filter_by(enrollment_id=enrollment_id).one_or_none()

    eligible, award_name, reason = _evaluate(version, policy.name, summary, enrollment)

    if existing is None:
        existing = LearnerAward(
            enrollment_id=enrollment_id,
            school_year_id=enrollment.school_year_id,
            award_policy_version_id=award_policy_version_id,
        )
        session.add(existing)
    existing.award_result = AwardResult.ELIGIBLE_AWARDED if eligible else AwardResult.NOT_ELIGIBLE
    existing.award_name = award_name
    existing.reason = reason
    existing.computed_at = datetime.now(timezone.utc)
    session.commit()
    return existing


def set_award_override(
    session: Session,
    learner_award: LearnerAward,
    award_result: AwardResult,
    award_name: str | None,
    override_by_user_id,
    override_reason: str,
) -> None:
    """Manual override (§40, §67 — administrator overrides require an
    audit-log reason). Marking is_override=True is what makes future
    compute_award_eligibility calls leave this row alone."""
    learner_award.award_result = award_result
    learner_award.award_name = award_name
    learner_award.is_override = True
    learner_award.override_by_user_id = override_by_user_id
    learner_award.override_reason = override_reason
    learner_award.reason = f"Manually overridden: {override_reason}"
    session.commit()


def clear_award_override(session: Session, learner_award: LearnerAward) -> None:
    learner_award.is_override = False
    learner_award.override_by_user_id = None
    learner_award.override_reason = None
    session.commit()
