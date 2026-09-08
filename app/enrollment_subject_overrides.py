"""Irregular-learner subject substitutions.

Rule 5 (CLAUDE.md) makes Section Subject Offerings the source of truth
for what a *section* is graded on. This is the deliberate exception: an
irregular learner — a transferee who already passed the section's usual
elective, say — takes a different offering instead, possibly from
another section entirely. `EnrollmentSubjectOverride`
(app/models/subjects.py) stores it, one row per (learner, term) being
substituted.

**One implementation of "which offering counts instead"**, used by
`app/grading_service.py` (Term/General Average), `app/report_card.py`
(SF9/term card) and `app/admin_pages/gradebook.py` (who a teacher sees on
their roster) — the same trap as the §16/§17 language-pair rule: three
separate readings of "this learner's effective subjects" would eventually
disagree with each other, and the printed form is the one that goes home
to a parent.
"""

from app.models.subjects import EnrollmentSubjectOverride, SectionSubjectOffering


def load_overrides_by_enrollment(session, enrollment_ids: list) -> dict:
    """One batched query for a whole roster/batch — never call per
    learner in a loop. Most enrollments have none; the dict simply has no
    entry for them."""
    if not enrollment_ids:
        return {}
    overrides: dict = {}
    for ov in (
        session.query(EnrollmentSubjectOverride)
        .filter(EnrollmentSubjectOverride.enrollment_id.in_(enrollment_ids))
        .all()
    ):
        overrides.setdefault(ov.enrollment_id, []).append(ov)
    return overrides


def load_extra_offerings(session, overrides_by_enrollment: dict, known_ids: set) -> dict:
    """Batch-loads every substitute offering referenced by any override in
    the batch that isn't already in `known_ids` — e.g. because it belongs
    to a different section than the ones the caller already queried.
    Returns offering_id -> SectionSubjectOffering, to merge into the
    caller's own lookup."""
    all_ids = {
        ov.substitute_section_subject_offering_id
        for overrides in overrides_by_enrollment.values()
        for ov in overrides
    }
    missing_ids = all_ids - known_ids
    if not missing_ids:
        return {}
    return {
        o.id: o
        for o in session.query(SectionSubjectOffering)
        .filter(SectionSubjectOffering.id.in_(missing_ids))
        .all()
    }


def apply_overrides(offerings: list, overrides: list, offerings_by_id: dict) -> list:
    """This enrollment's effective offering list: `offerings` (their
    section's normal list for the relevant school year) with each
    override's original offering removed and its substitute — resolved
    through `offerings_by_id`, which the caller must have already
    extended with `load_extra_offerings` — added in its place.

    A substitute missing from `offerings_by_id` is skipped rather than
    raising, so a dangling reference can't take down grading for a whole
    roster; it shouldn't happen since callers batch-load it. Returns
    `offerings` unchanged when there are no overrides, so the
    overwhelming common case (no irregular learners) does no extra work
    and allocates nothing new.
    """
    if not overrides:
        return offerings
    excluded_ids = {ov.original_section_subject_offering_id for ov in overrides}
    result = [o for o in offerings if o.id not in excluded_ids]
    for ov in overrides:
        substitute = offerings_by_id.get(ov.substitute_section_subject_offering_id)
        if substitute is not None:
            result.append(substitute)
    return result
