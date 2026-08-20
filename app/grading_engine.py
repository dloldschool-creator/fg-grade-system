"""Deterministic grade calculations (CLAUDE.md rule 1 — never an LLM,
never approximated). Pure functions, no DB access, so they're directly
unit-testable — see tests/test_grading_engine.py, which implements the
required tests from master-spec.md §68, and tests/test_do17_unit_system.py,
which reproduces every worked example in DepEd Order 017 s. 2026 Annex E.

Every function treats `None` as "not yet encoded" and propagates it
rather than defaulting to 0 (§65, §44) — a missing required input makes
the result `None` ("Incomplete"), never a number computed from partial
data.

Rounding is explicit round-half-up (Decimal + ROUND_HALF_UP), not
Python's built-in `round()`, which rounds half-to-even ("banker's
rounding"). This matters: the spec's own worked example rounds 92.5 to
93 (`ROUND((93 + 92) / 2) = 93`, master-spec.md §14), but Python's
`round(92.5)` returns 92. Excel's ROUND() — what the source workbook
this system replaces actually uses — rounds half away from zero, matching
what's implemented here.

**Units (DepEd Order 017 s. 2026, Annex E).** The Strengthened SHS
Curriculum weights each subject by equivalent units before averaging, so
a 12-unit Grade 12 TechPro elective counts six times a 2-unit core. The
weighted and unweighted rules are the *same arithmetic* — unweighted is
just every unit equal to 1 — so `weighted_average` is the only averaging
implementation here and `AveragingMethod` decides whether the caller's
units are used or flattened to 1. Nothing in this module knows which
method applies to which learner; that is `app/curriculum_policy.py`'s
job, resolved from a versioned policy record (CLAUDE.md rule 6).
"""

import enum
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple


class AveragingMethod(str, enum.Enum):
    """How a Term Average / General Average combines subject grades.

    UNWEIGHTED — the plain mean of the applicable grades. What the school's
    Excel workbook did and what the 2016 K to 12 SHS curriculum assumes, so
    it stays the default: a policy version that says nothing keeps the old
    arithmetic (CLAUDE.md rule 6).
    UNIT_WEIGHTED — DepEd Order 017 s. 2026, Annex E: each grade is weighted
    by the subject's equivalent units before averaging.

    **Defined here rather than in `app/models/enums.py`, which re-exports
    it.** This module is deliberately dependency-free — importing it must
    never pull in `app.models` and so must never influence the app's import
    order (see CLAUDE.md on the Python 3.14 outage, and `app/section_access.py`
    for the same deliberate choice). The arrow points models -> engine for
    that reason and no other.
    """

    UNWEIGHTED = "UNWEIGHTED"
    UNIT_WEIGHTED = "UNIT_WEIGHTED"


# A subject with no unit weighting configured anywhere. Deliberately 1 and
# not 0: an unconfigured subject must still count once, exactly as it did
# before units existed, rather than silently vanishing from the average.
DEFAULT_UNITS_PER_TERM = Decimal(1)

# DO 017 s. 2026 Table 19 expresses units as a flat rate on instructional
# hours: every row in that table is 3 units per 80 hours in a term (80h -> 3,
# 160h -> 6, 320h -> 12; a 160-hour core spread over 3 terms is 53.3h per
# term -> 2). Kept as the two literals rather than a single 0.0375 so the
# table is readable back out of the code.
UNITS_PER_HOURS_NUMERATOR = Decimal(3)
UNITS_PER_HOURS_DENOMINATOR = Decimal(80)


class GradeUnits(NamedTuple):
    """One entry in a weighted average: a grade (or None if not yet
    encoded) and the units it carries.

    A NamedTuple rather than a dataclass so a plain `(grade, units)` tuple
    is interchangeable with it in tests and callers.
    """

    grade: Decimal | None
    units: Decimal = DEFAULT_UNITS_PER_TERM


def round_half_up(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _average(values: list[Decimal]) -> Decimal:
    return sum(values) / Decimal(len(values))


def units_from_hours(hours_per_year, terms_offered: int) -> Decimal | None:
    """The DO 017 Table 19 unit value for a subject running `hours_per_year`
    across `terms_offered` terms.

    Every row of Table 19 falls out of this, which is why it's worth having:
    a subject DepEd adds later needs its hours entered, not a code change.
    `tests/test_do17_unit_system.py` asserts it reproduces all five rows.

    Returns None rather than guessing when hours aren't known.
    """
    if hours_per_year is None or not terms_offered:
        return None
    # Multiply before dividing. Hours per term is not always exact in decimal
    # — 320 across 3 terms is 106.66… — and dividing first leaves that
    # inexactness in the result, so a Grade 11 TechPro elective comes out at
    # 4.000000000000000000000000001 units instead of 4.
    hours = Decimal(str(hours_per_year))
    return (hours * UNITS_PER_HOURS_NUMERATOR) / (
        Decimal(terms_offered) * UNITS_PER_HOURS_DENOMINATOR
    )


def _as_entries(entries) -> list[GradeUnits]:
    """Accepts a bare list of grades (each weighted 1) or a list of
    (grade, units) pairs, so callers that predate units keep working
    unchanged and mean the same thing."""
    coerced: list[GradeUnits] = []
    for entry in entries:
        if isinstance(entry, tuple):
            grade, units = entry
            coerced.append(GradeUnits(grade, Decimal(str(units))))
        else:
            coerced.append(GradeUnits(entry, DEFAULT_UNITS_PER_TERM))
    return coerced


def weighted_average(
    entries, method: AveragingMethod = AveragingMethod.UNIT_WEIGHTED
) -> Decimal | None:
    """Σ(grade × units) ÷ Σ(units), rounded half-up to a whole number —
    DO 017 s. 2026 Annex E, which specifies both the Term Average and the
    General Weighted Average in exactly this form and says each is
    "expressed to the nearest whole number".

    Under `AveragingMethod.UNWEIGHTED` every entry's units are forced to 1,
    which reduces the same expression to the plain mean. That is the point:
    the two policies cannot disagree about anything except the weights.

    Returns None if there are no entries, if any grade is still un-encoded,
    or if the entries carry no units at all (which would be a division by
    zero, not a valid average of zero).
    """
    coerced = _as_entries(entries)
    if not coerced or any(e.grade is None for e in coerced):
        return None
    if method is AveragingMethod.UNWEIGHTED:
        coerced = [GradeUnits(e.grade, DEFAULT_UNITS_PER_TERM) for e in coerced]
    total_units = sum((e.units for e in coerced), Decimal(0))
    if total_units <= 0:
        return None
    weighted = sum((e.grade * e.units for e in coerced), Decimal(0))
    return round_half_up(weighted / total_units)


def total_units(entries, method: AveragingMethod = AveragingMethod.UNIT_WEIGHTED) -> Decimal:
    """The denominator `weighted_average` used, for display and for freezing
    into the permanent record — a wrong unit value is invisible in the
    average alone but obvious next to it."""
    coerced = _as_entries(entries)
    if method is AveragingMethod.UNWEIGHTED:
        return Decimal(len(coerced))
    return sum((e.units for e in coerced), Decimal(0))


def compute_subject_final_grade_exact(
    term_grades: dict[int, Decimal | None], required_terms: set[int]
) -> Decimal | None:
    """`compute_subject_final_grade` without the final rounding.

    DO 017's own worked examples average the **unrounded** subject finals:
    General Science on 76 / 78 / 82 prints a Final Grade of 78 but enters
    the year's total as 78.666…, which is the only way Annex E's 3348 ÷ 39
    = 85.84 reconciles. (DepEd's annex is not self-consistent about the
    printed value — the same subject shows as 78 on p. 84 and 79 on p. 86.)

    Which of the two feeds the average is therefore a policy switch, not a
    fact: see `average_from_unrounded_finals` on `grading_policy_versions`.
    """
    if not required_terms:
        return None
    values = [term_grades.get(t) for t in required_terms]
    if any(v is None for v in values):
        return None
    if len(values) == 1:
        return values[0]
    return _average(values)


def compute_subject_final_grade(
    term_grades: dict[int, Decimal | None], required_terms: set[int]
) -> Decimal | None:
    """§18. `required_terms` is which term numbers this subject is actually
    offered in for this section — driven by `section_subject_offerings`, not
    assumed from the subject alone (§8 NOTE, "elective subjects must not be
    averaged across terms in which they were not offered"). Any number of
    terms works, so a fourth would need no change here.

    A subject offered in exactly one term returns that term's grade
    unchanged (already an official whole-number grade, nothing to
    average/round). Two or more required terms average and round.
    Any required term missing its grade returns None (Incomplete) —
    never silently averages just the terms that happen to be filled in.

    This is the **reported** Final Grade — the whole number that prints on
    SF9 and is stored in `subject_final_grades`. Use
    `compute_subject_final_grade_exact` where the unrounded value is wanted.
    """
    exact = compute_subject_final_grade_exact(term_grades, required_terms)
    if exact is None:
        return None
    return round_half_up(exact)


def compute_combined_language_term_grade(
    component1_grade: Decimal | None, component2_grade: Decimal | None
) -> Decimal | None:
    """§15 — combined Term N display for SF9 (Grade 11 Effective
    Communication / Mabisang Komunikasyon).

    Whether the Term Average uses this or the two components separately is
    a policy switch (`combine_language_pair_in_term_average`): §17 says
    keep them separate, DO 017 Table 1 makes the pair a single 160-hour
    core subject. Both readings are implemented; neither is hardcoded."""
    if component1_grade is None or component2_grade is None:
        return None
    return round_half_up(_average([component1_grade, component2_grade]))


def compute_combined_language_final_grade(
    component1_final: Decimal | None, component2_final: Decimal | None
) -> Decimal | None:
    """§14, §62 — the combined parent learning area's Final Grade, from
    the two components' own Final Grades (each already computed via
    compute_subject_final_grade)."""
    if component1_final is None or component2_final is None:
        return None
    return round_half_up(_average([component1_final, component2_final]))


def compute_term_average(
    term_subject_grades, method: AveragingMethod = AveragingMethod.UNWEIGHTED
) -> Decimal | None:
    """§17 / DO 017 Annex E section A. The average of every active subject
    grade actually encoded for that term.

    `term_subject_grades` is either a list of grades (each counting once —
    the §17 rule) or a list of `GradeUnits` carrying each subject's units
    per term. `method` decides which is honoured, and defaults to
    UNWEIGHTED so a caller that hasn't been told otherwise keeps the old
    arithmetic.

    Under §17 the Grade 11 language pair counts as **two separate
    entries** here — "Do not substitute the combined language grade when
    calculating the Term Average" — which is the opposite of the General
    Average rule (§19). Under DO 017 the pair is one 2-unit core subject
    and appears once. Callers assemble the list either way; this function
    just averages what it is given.

    Returns None if any grade in the term is still un-encoded, rather
    than averaging the subset — a partial Term Average would understate
    or overstate depending on which subjects happen to be in yet.
    """
    return weighted_average(term_subject_grades, method)


def compute_general_average(
    applicable_finals, method: AveragingMethod = AveragingMethod.UNWEIGHTED
) -> Decimal | None:
    """§19, §20, §61 / DO 017 Annex E section B. `applicable_finals` must
    already be assembled correctly by the caller — for Grade 11, the two
    combined-language component finals are replaced by the ONE combined
    final (§19); for Grade 12, it's every applicable subject's Final Grade.

    Entries are grades, or `GradeUnits` whose units are the subject's
    **annual** units (units per term × the number of terms it ran), which is
    how DO 017 gets 6 for a three-term core and 3 for a one-term academic
    elective.

    This function doesn't know about grade levels or combined areas — it
    just averages whatever list it's given, and returns None (not a partial
    average) if anything in that list is still missing.
    """
    return weighted_average(applicable_finals, method)


def determine_pass_fail(final_grade: Decimal | None, passing_grade: Decimal) -> str:
    """§21. Returns "INCOMPLETE" / "PASSED" / "FAILED" — never compares
    None against the threshold as if it were a number."""
    if final_grade is None:
        return "INCOMPLETE"
    return "PASSED" if final_grade >= passing_grade else "FAILED"
