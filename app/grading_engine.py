"""Deterministic grade calculations (CLAUDE.md rule 1 — never an LLM,
never approximated). Pure functions, no DB access, so they're directly
unit-testable — see tests/test_grading_engine.py, which implements the
required tests from master-spec.md §68.

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
"""

from decimal import ROUND_HALF_UP, Decimal


def round_half_up(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _average(values: list[Decimal]) -> Decimal:
    return sum(values) / Decimal(len(values))


def compute_subject_final_grade(
    term_grades: dict[int, Decimal | None], required_terms: set[int]
) -> Decimal | None:
    """§18. `required_terms` is which term numbers (subset of {1,2,3}) this
    subject is actually offered in for this section — driven by
    `section_subject_offerings`, not assumed from the subject alone (§8
    NOTE, "elective subjects must not be averaged across terms in which
    they were not offered").

    A subject offered in exactly one term returns that term's grade
    unchanged (already an official whole-number grade, nothing to
    average/round). Two or three required terms average and round.
    Any required term missing its grade returns None (Incomplete) —
    never silently averages just the terms that happen to be filled in.
    """
    if not required_terms:
        return None
    values = [term_grades.get(t) for t in required_terms]
    if any(v is None for v in values):
        return None
    if len(values) == 1:
        return values[0]
    return round_half_up(_average(values))


def compute_combined_language_term_grade(
    component1_grade: Decimal | None, component2_grade: Decimal | None
) -> Decimal | None:
    """§15 — combined Term N display for SF9 (Grade 11 Effective
    Communication / Mabisang Komunikasyon). Not used for the Term Average
    (§17 keeps the two components separate for that)."""
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


def compute_term_average(term_subject_grades: list[Decimal | None]) -> Decimal | None:
    """§17. The average of every active subject grade actually encoded for
    that term, rounded.

    Critically, the Grade 11 language pair counts as **two separate
    entries** here — §17 says outright "Do not substitute the combined
    language grade when calculating the Term Average", and its worked
    example lists seven term-grade entries including both Effective
    Communication and Mabisang Komunikasyon. That is the exact opposite
    of the General Average rule (§19), where the pair collapses into one
    combined learning area. Callers assemble the list; this function just
    averages it.

    Returns None if any grade in the term is still un-encoded, rather
    than averaging the subset — a partial Term Average would understate
    or overstate depending on which subjects happen to be in yet.
    """
    if not term_subject_grades or any(g is None for g in term_subject_grades):
        return None
    return round_half_up(_average(term_subject_grades))


def compute_general_average(applicable_finals: list[Decimal | None]) -> Decimal | None:
    """§19, §20, §61. `applicable_finals` must already be assembled
    correctly by the caller — for Grade 11, the two combined-language
    component finals are replaced by the ONE combined final (§19); for
    Grade 12, it's every applicable subject's Final Grade. This function
    doesn't know about grade levels or combined areas — it just averages
    whatever list it's given, and returns None (not a partial average) if
    anything in that list is still missing."""
    if not applicable_finals or any(f is None for f in applicable_finals):
        return None
    return round_half_up(_average(applicable_finals))


def determine_pass_fail(final_grade: Decimal | None, passing_grade: Decimal) -> str:
    """§21. Returns "INCOMPLETE" / "PASSED" / "FAILED" — never compares
    None against the threshold as if it were a number."""
    if final_grade is None:
        return "INCOMPLETE"
    return "PASSED" if final_grade >= passing_grade else "FAILED"
