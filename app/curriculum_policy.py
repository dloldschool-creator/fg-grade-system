"""Which averaging rules apply to a learner, and how many units each
subject carries — DepEd Order 017 s. 2026 (Strengthened SHS Curriculum),
Annex E.

**Why this is a separate module.** `app/grading_engine.py` does the
arithmetic and knows nothing about grade levels, school years or policy
rows; it is deliberately dependency-free so importing it can never affect
the app's import order. Everything that needs the database to decide
*which* arithmetic applies lives here instead.

**Two curricula run at once.** DO 017 phases SSHS in by grade level —
Grade 11 in SY 2026-2027, Grade 12 not until SY 2027-2028, with Grade 12
staying on the 2016 K to 12 SHS curriculum in the meantime. So in
SY 2026-2027 a Grade 11 learner's General Average is unit-weighted and a
Grade 12 learner's is not, and both are correct. That is why the rules
are resolved per (school year, grade level) from a versioned policy row
rather than being a setting, a constant, or an `if grade_level == 11`.

**Nothing changes until a policy version says so** (CLAUDE.md rule 6).
Every column backing these rules defaults to the pre-DO-017 behaviour, and
`DEFAULT_RULES` is what you get when no policy version matches at all — so
a database that has never heard of DO 017 computes exactly what it always
computed.

**Query cost.** Both loaders take a whole section's offerings at once and
issue a fixed handful of queries, never one per subject or per learner —
the database is ~85ms away and `recompute_enrollment_grades` runs on every
grade save.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.grading_engine import DEFAULT_UNITS_PER_TERM, AveragingMethod
from app.models.enums import PolicyVersionStatus
from app.models.subjects import (
    GradingPolicyVersion,
    SectionSubjectOffering,
    Subject,
    SubjectCategory,
)


@dataclass(frozen=True)
class AveragingRules:
    """The averaging decisions in force for one (school year, grade level).

    Frozen because these are read many times while recomputing a learner and
    must not drift partway through.
    """

    method: AveragingMethod = AveragingMethod.UNWEIGHTED
    combine_language_pair_in_term_average: bool = False
    average_from_unrounded_finals: bool = False
    # The version these came from, for audit. Deliberately the id and not a
    # readable label: resolving the label means a second query, and this is
    # loaded on the report-card path where the query budget is tight.
    # `app/academic_record_service.py` resolves the label at capture time,
    # which happens once per finalized year rather than once per render.
    policy_version_id: uuid.UUID | None = None

    @property
    def is_unit_weighted(self) -> bool:
        return self.method is AveragingMethod.UNIT_WEIGHTED


# What applies when no ACTIVE policy version matches — the behaviour the app
# had before DO 017 existed, stated explicitly rather than left to fall out
# of column defaults.
DEFAULT_RULES = AveragingRules()


def _specificity(version: GradingPolicyVersion, school_year_id, grade_level_id) -> int | None:
    """How well a policy version matches, higher being better; None if it
    doesn't apply at all.

    A version scoped to *this* grade level beats one scoped to every grade
    level, which is what lets the Grade 11 SSHS version and a school-wide
    version coexist without the school-wide one having to be deleted.
    """
    if version.effective_school_year_id not in (None, school_year_id):
        return None
    if version.effective_grade_level_id not in (None, grade_level_id):
        return None
    score = 0
    if version.effective_school_year_id is not None:
        score += 2
    if version.effective_grade_level_id is not None:
        score += 1
    return score


def resolve_averaging_rules(session: Session, school_year_id, grade_level_id) -> AveragingRules:
    """The rules for one learner's year, most specific policy version wins.

    Ties on specificity — two versions scoped identically — go to the
    **highest `version_number`**, which is how a new curriculum supersedes
    the one before it without the older version having to be archived or
    deleted. That is the normal case: DO 017 applies to both grade levels
    here, so its version is scoped exactly like the baseline it replaces and
    wins only by being later.

    Reads every ACTIVE version in one query and ranks them in Python rather
    than issuing a query per fallback level — the table is small and the
    round trips are the expensive part.
    """
    versions = (
        session.query(GradingPolicyVersion)
        .filter_by(status=PolicyVersionStatus.ACTIVE)
        .all()
    )
    ranked = []
    for version in versions:
        score = _specificity(version, school_year_id, grade_level_id)
        if score is not None:
            ranked.append((score, version.version_number, version))
    if not ranked:
        return DEFAULT_RULES

    ranked.sort(key=lambda row: (row[0], row[1]))
    version = ranked[-1][2]
    return AveragingRules(
        method=version.averaging_method or AveragingMethod.UNWEIGHTED,
        combine_language_pair_in_term_average=bool(
            version.combine_language_pair_in_term_average
        ),
        average_from_unrounded_finals=bool(version.average_from_unrounded_finals),
        policy_version_id=version.id,
    )


def load_offering_units(
    session: Session, offerings: list[SectionSubjectOffering]
) -> dict:
    """`{offering_id: units per term}` for a whole section, in two queries.

    The resolution chain, narrowest first:

      1. `section_subject_offerings.units_per_term` — this section teaches it
         at unusual hours,
      2. `subjects.units_per_term` — the subject differs from its category
         (DO 017 gives a TechPro elective 4 units in Grade 11 and 12 in Grade
         12, and both are `TECHPRO_ELECTIVE`),
      3. `subject_categories.units_per_term` — the Table 19 default,
      4. `DEFAULT_UNITS_PER_TERM` (1) — nothing configured.

    Step 4 is 1 and not 0 deliberately: an unconfigured subject keeps
    counting exactly once, which is what it did before units existed. Zero
    would drop it out of the average silently.
    """
    if not offerings:
        return {}

    subject_ids = {o.subject_id for o in offerings}
    subjects = {
        s.id: s for s in session.query(Subject).filter(Subject.id.in_(subject_ids)).all()
    }
    categories = {c.id: c for c in session.query(SubjectCategory).all()}

    units: dict = {}
    for offering in offerings:
        value = offering.units_per_term
        if value is None:
            subject = subjects.get(offering.subject_id)
            value = subject.units_per_term if subject else None
        if value is None:
            # The offering carries its own category id, which is the one the
            # section actually confirmed — prefer it over the subject's.
            category = categories.get(offering.subject_category_id)
            if category is None and offering.subject_id in subjects:
                category = categories.get(subjects[offering.subject_id].subject_category_id)
            value = category.units_per_term if category else None
        units[offering.id] = (
            Decimal(str(value)) if value is not None else DEFAULT_UNITS_PER_TERM
        )
    return units


def combined_area_units_per_term(area, component_units: list[Decimal]) -> Decimal:
    """The units the combined language pair carries as ONE learning area.

    The area's own `units_per_term` if set (DO 017 says 2 — it is a single
    160-hour core subject). Otherwise one component's units, **not** their
    sum: summing would weight the pair twice, which is precisely the
    double-counting §19 exists to prevent.
    """
    if getattr(area, "units_per_term", None) is not None:
        return Decimal(str(area.units_per_term))
    if component_units:
        return max(component_units)
    return DEFAULT_UNITS_PER_TERM
