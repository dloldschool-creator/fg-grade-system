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

**Shape** decides *how* eligibility is judged, and is one of three,
mutually exclusive (`manual_only` wins over `tier_thresholds` over the
flat fields, checked in that order):
  - **manual_only** — no computable rule at all (Leadership, Best in
    Subject). Every learner defaults to Not Eligible; only an explicit
    override on the Awards page grants it. require_* checks still apply.
  - **tier_thresholds set** — a ladder where the highest cleared tier wins.
  - **neither** — a flat minimum (or no threshold at all, which awards
    everyone who clears the require_* checks — this is the shape to avoid
    for a nominative award, which is what manual_only is for).

Scope and shape are orthogonal — any scope can use any shape. All three
always record *why*, never a bare "Not Eligible" (§24 requires the
explanation), and none ever recomputes grades itself: the averages are
read from the already-computed summary tables.

`require_perfect_attendance` is a fourth, independent gate (like
`require_complete_record`) rather than a fourth shape: zero absences and
zero tardies/cutting over the scope's own period, with attendance fully
encoded first. It combines with any shape — a pure attendance award sets
nothing else, but a school could equally require both a grade threshold
and perfect attendance on the same version. Reading it means a real,
separately-batched query against `attendance_records`, so
`compute_award_eligibility_batch` only fetches it when the version
actually asks for it.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import attendance_service, audit_service
from app.attendance_engine import AttendanceSummary, Movement, compute_active_window
from app.models.awards import AwardPolicy, AwardPolicyVersion, LearnerAward
from app.models.enums import AwardResult, AwardScope, CompletionStatus
from app.models.grades import AnnualGradeSummary, TermGradeSummary
from app.models.learners import Enrollment
from app.models.organization import SchoolYear, Term


def _evaluate(
    version: AwardPolicyVersion,
    policy_name: str,
    summary,
    enrollment,
    average_label: str,
    record_label: str,
    attendance: AttendanceSummary | None = None,
):
    """`summary` is an AnnualGradeSummary or a TermGradeSummary — this
    reads only the fields both expose (via `_average_of`/`_lowest_of`),
    so one function covers both scopes.

    Two labels, not one, because the natural phrasing differs: the annual
    scope reports a "General Average" but an "Annual record", while a term
    scope reports a "Term 1 Average" and a "Term 1 record".

    `attendance` is only read when `version.require_perfect_attendance` is
    set — the caller (`compute_award_eligibility_batch`) only bothers
    building it in that case, since it's a genuinely separate, batched
    data fetch from the grade summaries every other check reads."""
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

    if version.require_perfect_attendance:
        if attendance is None or attendance.eligible_days == 0:
            eligible = False
            reasons.append(f"No eligible class days recorded ({record_label}).")
        elif attendance.unencoded_days > 0:
            eligible = False
            reasons.append(
                f"{attendance.unencoded_days} day(s) of attendance not yet encoded "
                f"({record_label})."
            )
        else:
            broken = []
            if attendance.days_absent:
                broken.append(f"{attendance.days_absent} absence(s)")
            if attendance.late_count:
                broken.append(f"{attendance.late_count} tardy(ies)")
            if attendance.cutting_count:
                broken.append(f"{attendance.cutting_count} cutting(s)")
            if broken:
                eligible = False
                reasons.append("Not perfect attendance — " + ", ".join(broken) + ".")

    if version.require_no_derogatory_record and enrollment.derogatory_record:
        eligible = False
        reasons.append("Learner has a derogatory record.")

    if version.require_no_failed_subject and summary and (summary.failed_subject_count or 0) > 0:
        eligible = False
        reasons.append(f"{summary.failed_subject_count} failed subject(s).")

    # A nominative award (Leadership, Best in Subject) has no computable
    # rule at all — checked before either threshold shape, since a
    # manual-only version is expected to carry no thresholds and would
    # otherwise fall into the single-tier branch's "nothing configured,
    # nothing to fail" case and silently award everyone. require_* checks
    # above still apply, so a manual-only award can still be blocked by a
    # derogatory record, for example.
    if version.manual_only:
        if eligible:
            eligible = False
            reasons.append(
                "No automatic rule for this award — it's judged individually. "
                "Use the override control on the Awards page to grant it."
            )
    # NOTE: the tier dicts' threshold key is `min_general_average` for
    # every scope. It's historical (tiers were annual-only originally) and
    # kept as-is so already-seeded JSONB stays readable — under a TERM
    # scope it means "minimum Term Average".
    elif version.tier_thresholds:
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


def _attendance_by_enrollment(
    session: Session, version: AwardPolicyVersion, enrollments: dict, term_id
) -> dict:
    """`{enrollment_id: AttendanceSummary}` for the version's scope
    period, batched the same way `attendance_service.summarize_month_batch`
    already batches a whole roster for one month — the only difference
    here is the `class_days` list spans a term or the whole year instead
    of one calendar month, which the function already supports since it
    was never actually month-specific, just month-named. Only called when
    `require_perfect_attendance` is set — every other check reads grade
    summaries instead, which the caller already has."""
    if not enrollments:
        return {}
    class_days = (
        attendance_service.class_days_in_term(session, term_id)
        if version.scope == AwardScope.TERM
        else attendance_service.class_days_in_school_year(session, version.effective_school_year_id)
    )
    movements = attendance_service.movements_by_enrollment(session, list(enrollments))
    school_year = session.get(SchoolYear, version.effective_school_year_id)
    default_start = school_year.start_date if school_year else None
    roster = [
        (
            enrollment,
            None,  # summarize_month_batch never reads the Learner slot
            compute_active_window(
                [Movement(m.movement_type, m.effective_date) for m in movements.get(enrollment.id, [])],
                default_start=default_start,
            ),
        )
        for enrollment in enrollments.values()
    ]
    return attendance_service.summarize_month_batch(session, roster, class_days)


def compute_award_eligibility_batch(
    session: Session, enrollment_ids: list, award_policy_version_id, term_id=None
) -> dict:
    """Computes and upserts `learner_awards` for a whole roster in one
    pass — the same split `recompute_enrollment_grades_batch` uses for
    grades (CLAUDE.md's Performance section), and for the same reason:
    the Awards page's "Compute eligibility for all" used to call the
    single-enrollment version in a loop, which meant one full commit per
    learner, not just one query. `compute_award_eligibility` below is now
    a 1-element wrapper over this — call this directly for anything that
    loops.

    `term_id` is required for a TERM-scoped policy and ignored for an
    ANNUAL one — passing it for the wrong scope returns {} rather than
    silently writing rows that mean something different from what the
    caller intended.

    A row with `is_override=True` is left untouched: an admin override
    persists until explicitly cleared (see `clear_award_override`), never
    silently overwritten by the next recompute.
    """
    version = session.get(AwardPolicyVersion, award_policy_version_id)
    if version is None:
        return {}

    if version.scope == AwardScope.TERM:
        if term_id is None:
            return {}
        summaries = {
            row.enrollment_id: row
            for row in session.query(TermGradeSummary)
            .filter(
                TermGradeSummary.enrollment_id.in_(enrollment_ids),
                TermGradeSummary.term_id == term_id,
            )
            .all()
        }
        term = session.get(Term, term_id)
        record_label = term.name if term else "Term"
        average_label = f"{record_label} Average"
        effective_term_id = term_id
    else:
        summaries = {
            row.enrollment_id: row
            for row in session.query(AnnualGradeSummary)
            .filter(AnnualGradeSummary.enrollment_id.in_(enrollment_ids))
            .all()
        }
        record_label = "Annual"
        average_label = "General Average"
        effective_term_id = None

    enrollments = {
        e.id: e for e in session.query(Enrollment).filter(Enrollment.id.in_(enrollment_ids)).all()
    }
    existing_rows = {
        row.enrollment_id: row
        for row in session.query(LearnerAward)
        .filter(
            LearnerAward.enrollment_id.in_(enrollment_ids),
            LearnerAward.award_policy_version_id == award_policy_version_id,
            LearnerAward.term_id == effective_term_id,
        )
        .all()
    }
    policy = session.get(AwardPolicy, version.award_policy_id)

    attendance_by_enrollment = (
        _attendance_by_enrollment(session, version, enrollments, term_id)
        if version.require_perfect_attendance
        else {}
    )

    now = datetime.now(timezone.utc)
    results: dict = {}
    for enrollment_id in enrollment_ids:
        enrollment = enrollments.get(enrollment_id)
        if enrollment is None:
            continue
        existing = existing_rows.get(enrollment_id)
        if existing is not None and existing.is_override:
            results[enrollment_id] = existing
            continue

        eligible, award_name, reason = _evaluate(
            version,
            policy.name,
            summaries.get(enrollment_id),
            enrollment,
            average_label,
            record_label,
            attendance=attendance_by_enrollment.get(enrollment_id),
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
        existing.computed_at = now
        results[enrollment_id] = existing

    session.commit()
    return results


def compute_award_eligibility(
    session: Session, enrollment_id, award_policy_version_id, term_id=None
) -> LearnerAward | None:
    """Single-enrollment wrapper over `compute_award_eligibility_batch` —
    same 1-element-call pattern as `recompute_enrollment_grades`. Prefer
    the batch function directly for anything that loops."""
    return compute_award_eligibility_batch(
        session, [enrollment_id], award_policy_version_id, term_id
    ).get(enrollment_id)


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
