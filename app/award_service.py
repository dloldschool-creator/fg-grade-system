"""Award eligibility computation (§24).

Two independent axes, deliberately not conflated:

**Scope** (`award_policy_versions.scope`) decides *what average* is
judged and *how often*:
  - ANNUAL — once a year against the General Average (§19/§20) and the
    lowest Final Grade, from `annual_grade_summaries`. The Academic
    Excellence Award works this way.
  - TERM — once per term against that term's Term Average (§17) and the
    lowest term grade, from `term_grade_summaries`. The legacy tiered
    Honors works this way, so a learner can be "With Honors" for Term 1
    and miss it for Term 2.

**Shape** (`tier_thresholds` set or not) decides *how* the threshold is
applied: a flat minimum, or a ladder where the highest cleared tier wins.

Either scope can use either shape — they're orthogonal. Both always
record *why*, never a bare "Not Eligible" (§24 requires the explanation),
and neither ever recomputes grades itself: the averages are read from the
already-computed summary tables.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import audit_service
from app.models.awards import AwardPolicy, AwardPolicyVersion, LearnerAward
from app.models.enums import AwardResult, AwardScope, CompletionStatus
from app.models.grades import AnnualGradeSummary, TermGradeSummary
from app.models.learners import Enrollment
from app.models.organization import Term


def _evaluate(
    version: AwardPolicyVersion,
    policy_name: str,
    summary,
    enrollment,
    average_label: str,
    record_label: str,
):
    """`summary` is an AnnualGradeSummary or a TermGradeSummary — this
    reads only the fields both expose (via `_average_of`/`_lowest_of`),
    so one function covers both scopes.

    Two labels, not one, because the natural phrasing differs: the annual
    scope reports a "General Average" but an "Annual record", while a term
    scope reports a "Term 1 Average" and a "Term 1 record"."""
    reasons: list[str] = []
    eligible = True

    average = _average_of(summary)
    lowest = _lowest_of(summary)
    award_name = None

    if version.require_complete_record and (
        summary is None or summary.completion_status != CompletionStatus.COMPLETE
    ):
        eligible = False
        reasons.append(f"{record_label} record is not COMPLETE.")

    if version.require_no_derogatory_record and enrollment.derogatory_record:
        eligible = False
        reasons.append("Learner has a derogatory record.")

    if version.require_no_failed_subject and summary and (summary.failed_subject_count or 0) > 0:
        eligible = False
        reasons.append(f"{summary.failed_subject_count} failed subject(s).")

    # NOTE: the tier dicts' threshold key is `min_general_average` for
    # every scope. It's historical (tiers were annual-only originally) and
    # kept as-is so already-seeded JSONB stays readable — under a TERM
    # scope it means "minimum Term Average".
    if version.tier_thresholds:
        if average is None:
            eligible = False
            reasons.append(f"{average_label} not yet computed.")
        elif eligible:
            for tier in sorted(version.tier_thresholds, key=lambda t: -t["min_general_average"]):
                if average >= tier["min_general_average"]:
                    award_name = tier["label"]
                    break
            if award_name is None:
                eligible = False
                reasons.append(
                    f"{average_label} {average} below the lowest tier threshold "
                    f"({min(t['min_general_average'] for t in version.tier_thresholds)})."
                )
    else:
        if version.min_general_average is not None:
            if average is None or average < version.min_general_average:
                eligible = False
                reasons.append(
                    f"{average_label} {average if average is not None else 'N/A'} "
                    f"below required {version.min_general_average}."
                )
        if version.min_lowest_final_grade is not None:
            if lowest is None or lowest < version.min_lowest_final_grade:
                eligible = False
                reasons.append(
                    f"Lowest grade {lowest if lowest is not None else 'N/A'} "
                    f"below required {version.min_lowest_final_grade}."
                )
        if eligible:
            award_name = policy_name

    reason = "; ".join(reasons) if reasons else "Meets all requirements."
    return eligible, award_name, reason


def _average_of(summary):
    """Dispatches on the summary's actual type, not on truthiness — an
    `or` chain here would treat a legitimately-zero average as missing
    and silently fall through to the other scope's attribute."""
    if summary is None:
        return None
    if isinstance(summary, TermGradeSummary):
        return summary.term_average
    return summary.general_average


def _lowest_of(summary):
    if summary is None:
        return None
    if isinstance(summary, TermGradeSummary):
        return summary.lowest_term_grade
    return summary.lowest_final_grade


def compute_award_eligibility(
    session: Session, enrollment_id, award_policy_version_id, term_id=None
) -> LearnerAward | None:
    """Computes and upserts the `learner_awards` row.

    `term_id` is required for a TERM-scoped policy and ignored for an
    ANNUAL one — passing it for the wrong scope returns None rather than
    silently writing a row that means something different from what the
    caller intended.

    A row with `is_override=True` is left untouched: an admin override
    persists until explicitly cleared (see `clear_award_override`), never
    silently overwritten by the next recompute.
    """
    version = session.get(AwardPolicyVersion, award_policy_version_id)
    if version is None:
        return None

    if version.scope == AwardScope.TERM:
        if term_id is None:
            return None
        summary = (
            session.query(TermGradeSummary)
            .filter_by(enrollment_id=enrollment_id, term_id=term_id)
            .one_or_none()
        )
        term = session.get(Term, term_id)
        record_label = term.name if term else "Term"
        average_label = f"{record_label} Average"
        effective_term_id = term_id
    else:
        summary = (
            session.query(AnnualGradeSummary).filter_by(enrollment_id=enrollment_id).one_or_none()
        )
        record_label = "Annual"
        average_label = "General Average"
        effective_term_id = None

    existing = (
        session.query(LearnerAward)
        .filter_by(
            enrollment_id=enrollment_id,
            award_policy_version_id=award_policy_version_id,
            term_id=effective_term_id,
        )
        .one_or_none()
    )
    if existing is not None and existing.is_override:
        return existing

    policy = session.get(AwardPolicy, version.award_policy_id)
    enrollment = session.get(Enrollment, enrollment_id)

    eligible, award_name, reason = _evaluate(
        version, policy.name, summary, enrollment, average_label, record_label
    )

    if existing is None:
        existing = LearnerAward(
            enrollment_id=enrollment_id,
            school_year_id=enrollment.school_year_id,
            award_policy_version_id=award_policy_version_id,
            term_id=effective_term_id,
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
    compute_award_eligibility calls leave this row alone.

    The audit entry is written here rather than in the page so that no
    caller can override an award without leaving one behind."""
    previous = {"award_result": learner_award.award_result, "award_name": learner_award.award_name}
    learner_award.award_result = award_result
    learner_award.award_name = award_name
    learner_award.is_override = True
    learner_award.override_by_user_id = override_by_user_id
    learner_award.override_reason = override_reason
    learner_award.reason = f"Manually overridden: {override_reason}"
    audit_service.record(
        session,
        action=audit_service.AWARD_OVERRIDDEN,
        object_type="learner_awards",
        object_id=learner_award.id,
        user_id=override_by_user_id,
        previous=previous,
        new={"award_result": award_result, "award_name": award_name},
        reason=override_reason,
    )
    session.commit()


def clear_award_override(session: Session, learner_award: LearnerAward, cleared_by_user_id=None) -> None:
    audit_service.record(
        session,
        action=audit_service.AWARD_OVERRIDE_CLEARED,
        object_type="learner_awards",
        object_id=learner_award.id,
        user_id=cleared_by_user_id,
        previous={
            "award_result": learner_award.award_result,
            "override_reason": learner_award.override_reason,
        },
        new={"is_override": False},
    )
    learner_award.is_override = False
    learner_award.override_by_user_id = None
    learner_award.override_reason = None
    session.commit()
