"""Every worked example in DepEd Order 017 s. 2026, Annex E, reproduced.

Annex E prints seven complete tables — subject grades, units, term averages
and a General Average for each of the SSHS track/grade-level combinations.
That makes them gold fixtures in the strongest sense available here: not
values this project chose, but values DepEd published, which the engine
either reproduces exactly or does not.

They are worth having because a unit-weighting bug does not raise. It
produces a slightly different plausible number — 87 where the answer is 89 —
and the only way to catch that is to check against arithmetic someone else
did. `tests/test_grading_engine.py` covers the pre-DO-017 rules; this file
covers the rules that replace them. FGNMHS is a DO 017 pilot school, so
both grade levels move to SSHS in SY 2026-2027 — the ¶7 exemption that
would have kept Grade 12 on the 2016 curriculum does not apply here.

Source: DO 017 s. 2026, Annex E, pp. 82-87 of the enclosure.
"""

from decimal import Decimal

import pytest

from app.grading_engine import (
    AveragingMethod,
    GradeUnits,
    compute_general_average,
    compute_subject_final_grade,
    compute_subject_final_grade_exact,
    compute_term_average,
    total_units,
    units_from_hours,
)

D = Decimal
WEIGHTED = AveragingMethod.UNIT_WEIGHTED
UNWEIGHTED = AveragingMethod.UNWEIGHTED


# --- Table 19 ------------------------------------------------------------


@pytest.mark.parametrize(
    "classification, hours_per_year, terms, expected_units",
    [
        ("Core Subject", 160, 3, 2),
        ("Academic Elective", 80, 1, 3),
        ("Arts Elective (Apprenticeship / Creative Production)", 160, 1, 6),
        ("TechPro Elective, Grade 11", 320, 3, 4),
        ("TechPro Elective, Grade 12", 320, 1, 12),
    ],
)
def test_table_19_units_fall_out_of_the_prescribed_hours(
    classification, hours_per_year, terms, expected_units
):
    """All five rows of Table 19 are one rate: 3 units per 80 hours in a term.

    Worth asserting because it means a subject DepEd adds later needs its
    hours entered, not a new branch in the code.
    """
    assert units_from_hours(hours_per_year, terms) == expected_units, classification


def test_units_from_hours_declines_to_guess():
    assert units_from_hours(None, 3) is None
    assert units_from_hours(160, 0) is None


# --- Grade 11 Academic Track (Annex E, p. 84) ----------------------------
#
# Five cores at 2 units per term across 3 terms, plus three 3-unit academic
# electives running one term each. Total 39 units.

G11_ACADEMIC = {
    # name: (term grades by term number, units per term)
    "Effective Comm / Mabisang Kom": ({1: D(80), 2: D(85), 3: D(90)}, D(2)),
    "General Math": ({1: D(90), 2: D(92), 3: D(91)}, D(2)),
    "General Science": ({1: D(76), 2: D(78), 3: D(82)}, D(2)),
    "Life and Career Skills": ({1: D(80), 2: D(80), 3: D(80)}, D(2)),
    "Pag-aaral ng Kasaysayan at Lipunang Pilipino": ({1: D(93), 2: D(95), 3: D(98)}, D(2)),
    "Acad Elec1": ({1: D(76)}, D(3)),
    "Acad Elec2": ({2: D(88)}, D(3)),
    "Acad Elec3": ({3: D(92)}, D(3)),
}


def _term_entries(table, term_number):
    return [
        GradeUnits(grades[term_number], units)
        for grades, units in table.values()
        if term_number in grades
    ]


def _annual_entries(table, *, unrounded: bool):
    entries = []
    for grades, units_per_term in table.values():
        required = set(grades)
        final = (
            compute_subject_final_grade_exact(grades, required)
            if unrounded
            else compute_subject_final_grade(grades, required)
        )
        entries.append(GradeUnits(final, units_per_term * D(len(required))))
    return entries


@pytest.mark.parametrize(
    "term_number, expected_average, expected_units",
    [(1, 82, 13), (2, 86, 13), (3, 89, 13)],
)
def test_g11_academic_term_averages(term_number, expected_average, expected_units):
    """Annex E prints 82 / 86.46 -> 86 / 89.07 -> 89, on 13 units per term
    (five cores at 2 plus one academic elective at 3)."""
    entries = _term_entries(G11_ACADEMIC, term_number)
    assert total_units(entries, WEIGHTED) == expected_units
    assert compute_term_average(entries, WEIGHTED) == expected_average


def test_g11_academic_general_average():
    """Annex E: 39 units, 85.84, reported as 86."""
    entries = _annual_entries(G11_ACADEMIC, unrounded=True)
    assert total_units(entries, WEIGHTED) == 39
    assert compute_general_average(entries, WEIGHTED) == 86


def test_g11_academic_term_1_differs_from_the_plain_mean():
    """The whole point, on DepEd's own numbers.

    Term 1's six grades average to 82.5, which rounds to 83. Weighted by
    units they come to exactly 82 — the number Annex E prints. One mark, and
    it is the mark an honors cut-off can turn on.
    """
    entries = _term_entries(G11_ACADEMIC, 1)
    assert compute_term_average(entries, WEIGHTED) == 82
    assert compute_term_average(entries, UNWEIGHTED) == 83


# --- Grade 11 Technical-Professional Track (Annex E, p. 86) --------------

# The same five cores, with one TechPro elective in place of the three
# academic ones: 320 hours across 3 terms -> 4 units per term, 12 for the year.
G11_TECHPRO = {
    name: value
    for name, value in G11_ACADEMIC.items()
    if not name.startswith("Acad Elec")
} | {"Tech-Pro Elective 1": ({1: D(86), 2: D(90), 3: D(92)}, D(4))}


@pytest.mark.parametrize(
    "term_number, expected_average", [(1, 84), (2, 87), (3, 89)]
)
def test_g11_techpro_term_averages(term_number, expected_average):
    """Annex E: 84.43 / 87.14 / 89.29 on 14 units per term."""
    entries = _term_entries(G11_TECHPRO, term_number)
    assert total_units(entries, WEIGHTED) == 14
    assert compute_term_average(entries, WEIGHTED) == expected_average


def test_g11_techpro_general_average():
    """Annex E: 42 units, 86.95, reported as 87."""
    entries = _annual_entries(G11_TECHPRO, unrounded=True)
    assert total_units(entries, WEIGHTED) == 42
    assert compute_general_average(entries, WEIGHTED) == 87


# --- Grade 12 Academic Track (Annex E, p. 85) ---------------------------
#
# Twelve academic electives at 3 units, four per term. Every subject in a
# term carries the same units, so the term averages are plain means — which
# is a useful negative check that weighting doesn't disturb them.

G12_ACADEMIC = {
    "Acad Elec4": ({1: D(76)}, D(3)),
    "Acad Elec5": ({1: D(88)}, D(3)),
    "Acad Elec6": ({1: D(90)}, D(3)),
    "Acad Elec7": ({1: D(93)}, D(3)),
    "Acad Elec8": ({2: D(95)}, D(3)),
    "Acad Elec9": ({2: D(86)}, D(3)),
    "Acad Elec10": ({2: D(78)}, D(3)),
    "Acad Elec11": ({2: D(81)}, D(3)),
    "Acad Elec12": ({3: D(77)}, D(3)),
    "Acad Elec13": ({3: D(85)}, D(3)),
    "Acad Elec14": ({3: D(99)}, D(3)),
    "Acad Elec15": ({3: D(92)}, D(3)),
}


def test_g12_academic_general_average():
    """Annex E: 36 units, 86.67, reported as 87."""
    entries = _annual_entries(G12_ACADEMIC, unrounded=True)
    assert total_units(entries, WEIGHTED) == 36
    assert compute_general_average(entries, WEIGHTED) == 87


@pytest.mark.parametrize("term_number, expected", [(1, 87), (2, 85), (3, 88)])
def test_g12_academic_term_averages_match_the_plain_mean(term_number, expected):
    """86.75 / 85 / 88.25. Equal units within a term, so weighting changes
    nothing — the weighted rule must not perturb this case."""
    entries = _term_entries(G12_ACADEMIC, term_number)
    assert compute_term_average(entries, WEIGHTED) == expected
    assert compute_term_average(entries, UNWEIGHTED) == expected


# --- Grade 12 Academic with cross-track (Annex E, p. 85) ----------------
#
# The widest divergence DepEd publishes: eight 3-unit academic electives and
# one 12-unit TechPro elective.

G12_CROSS_TRACK = {
    "Acad Elec4": ({1: D(76)}, D(3)),
    "Acad Elec5": ({1: D(88)}, D(3)),
    "Acad Elec6": ({1: D(90)}, D(3)),
    "Acad Elec7": ({1: D(93)}, D(3)),
    "Acad Elec8": ({2: D(95)}, D(3)),
    "Acad Elec9": ({2: D(86)}, D(3)),
    "Acad Elec10": ({2: D(78)}, D(3)),
    "Acad Elec11": ({2: D(81)}, D(3)),
    # 320 hours in one term -> 12 units.
    "Tech-Pro Elec": ({3: D(96)}, D(12)),
}


def test_g12_cross_track_general_average_is_two_marks_off_the_plain_mean():
    """Annex E: 36 units, 89.25, reported as 89.

    The plain mean of the same nine finals is 87. Two marks, because one
    12-unit subject is worth four 3-unit ones and an unweighted average
    treats it as worth one. This is the case that makes the whole change
    matter, and it is the shape the school's Grade 12 TechPro sections have.
    """
    entries = _annual_entries(G12_CROSS_TRACK, unrounded=True)
    assert total_units(entries, WEIGHTED) == 36
    assert compute_general_average(entries, WEIGHTED) == 89
    assert compute_general_average(entries, UNWEIGHTED) == 87


def test_g12_cross_track_term_3_is_the_techpro_elective_alone():
    entries = _term_entries(G12_CROSS_TRACK, 3)
    assert compute_term_average(entries, WEIGHTED) == 96


# --- Grade 12 Tech-Pro, both work immersion models (Annex E, p. 86) -----


@pytest.mark.parametrize(
    "label, table",
    [
        (
            "2 electives, 1-term work immersion",
            {
                "Tech-Pro Elective 2": ({1: D(76)}, D(12)),
                "Tech-Pro Elective 3": ({2: D(85)}, D(12)),
                "Work Immersion (320 hours)": ({3: D(90)}, D(12)),
            },
        ),
        (
            "1 elective, 2-term work immersion",
            {
                "Tech-Pro Elective 2": ({1: D(76)}, D(12)),
                "Work Immersion (320 hours)": ({2: D(85)}, D(12)),
                "Work Immersion (320 hours), second term": ({3: D(90)}, D(12)),
            },
        ),
    ],
)
def test_g12_techpro_work_immersion_models(label, table):
    """Both models: 36 units, 83.67, reported as 84."""
    entries = _annual_entries(table, unrounded=True)
    assert total_units(entries, WEIGHTED) == 36, label
    assert compute_general_average(entries, WEIGHTED) == 84, label


# --- The rounding question DO 017 leaves open ---------------------------


def _exact_weighted(entries):
    weighted = sum((e.grade * e.units for e in entries), D(0))
    return weighted / sum((e.units for e in entries), D(0))


def test_annex_e_reconciles_at_3348_over_39():
    """Annex E's Grade 11 Academic total, checked as a total.

    DepEd prints 85.84; the exact quotient is 85.846…, so their figure is
    truncated rather than rounded. Asserted here because the total is the
    part a reader can check against the PDF by hand.

    On *this* dataset rounding the subject finals first changes nothing —
    General Science's 78.66… and Pag-aaral's 95.33… round to 79 and 95 and
    the two thirds cancel to the same 430. That is a coincidence of these
    numbers, not a general result; `test_rounded_and_unrounded_finals_can_
    disagree` shows the case where it does not hold.
    """
    unrounded = _annual_entries(G11_ACADEMIC, unrounded=True)
    weighted_total = sum((e.grade * e.units for e in unrounded), D(0))
    assert weighted_total == 3348
    assert total_units(unrounded, WEIGHTED) == 39
    assert _exact_weighted(unrounded).quantize(D("0.001")) == D("85.846")

    rounded = _annual_entries(G11_ACADEMIC, unrounded=False)
    assert _exact_weighted(rounded) == _exact_weighted(unrounded)


def test_rounded_and_unrounded_finals_can_disagree():
    """Why `average_from_unrounded_finals` has to be a stored switch.

    A two-term subject on 85 and 86 has an exact final of 85.5 and a reported
    final of 86 — half a mark of difference, the most a subject final can
    carry. Weighted at 4 units against a 3-unit elective on 83:

        from unrounded finals: (85.5 x 4 + 83 x 3) / 7 = 84.43 -> 84
        from rounded finals:   (86   x 4 + 83 x 3) / 7 = 84.71 -> 85

    One mark apart, from the same encoded grades. DO 017's worked examples
    do the first; its printed tables show the finals of the second. Neither
    can be inferred from the other, so the app stores which one applies
    rather than picking.
    """
    table = {
        "Two-term core": ({1: D(85), 2: D(86)}, D(2)),
        "One-term elective": ({3: D(83)}, D(3)),
    }
    unrounded = _annual_entries(table, unrounded=True)
    rounded = _annual_entries(table, unrounded=False)

    assert total_units(unrounded, WEIGHTED) == 7
    assert compute_general_average(unrounded, WEIGHTED) == 84
    assert compute_general_average(rounded, WEIGHTED) == 85


def test_depeds_own_annex_disagrees_with_itself_about_the_printed_final():
    """General Science on 76 / 78 / 82 is printed as 78 on p. 84 and 79 on
    p. 86 of the same annex. 78.666… rounds half-up to 79, so the app reports
    79 and treats p. 84 as the typo — recorded here so the discrepancy is a
    known decision rather than a surprise if anyone checks against the PDF."""
    grades = {1: D(76), 2: D(78), 3: D(82)}
    assert compute_subject_final_grade(grades, {1, 2, 3}) == 79
    assert compute_subject_final_grade_exact(grades, {1, 2, 3}) == D(236) / D(3)


# --- Guardrails ---------------------------------------------------------


def test_unweighted_is_unit_weighted_with_every_unit_equal():
    """The two methods share one implementation, so this is what stops the
    unweighted path from drifting if the weighted one is changed."""
    grades = [D(90), D(80), D(100)]
    entries = [GradeUnits(g, D(1)) for g in grades]
    assert compute_term_average(entries, WEIGHTED) == compute_term_average(grades, UNWEIGHTED)


def test_a_missing_grade_still_blocks_the_weighted_average():
    """Rule 2 survives weighting: a NULL is not a zero, and not a subject to
    quietly leave out of the denominator."""
    entries = [GradeUnits(D(90), D(6)), GradeUnits(None, D(3))]
    assert compute_term_average(entries, WEIGHTED) is None
    assert compute_general_average(entries, WEIGHTED) is None


def test_zero_total_units_is_none_not_a_division_error():
    assert compute_general_average([GradeUnits(D(90), D(0))], WEIGHTED) is None


def test_units_do_not_change_a_subjects_own_final_grade():
    """Units weight subjects against each other; they never touch the grade
    inside one. A three-term core is still the mean of its three terms."""
    grades = {1: D(80), 2: D(85), 3: D(90)}
    assert compute_subject_final_grade(grades, {1, 2, 3}) == 85


# --- The language pair under DO 017 -------------------------------------
#
# The school's decision (2026-08-20): the pair keeps its parent-component
# *display* and moves to DO 017's *computation*. Those are two independent
# things and the tests below pin them separately, because the failure mode
# is getting one without the other — a card listing three lines whose
# average was computed from two, or two lines whose average came from one.


def test_the_pair_counts_once_at_one_core_subjects_weight():
    """DO 017 Table 1 makes Effective Communication / Mabisang Komunikasyon
    a single 160-hour core subject, so the pair carries **2** units in a
    term, not 2 + 2.

    Effective Communication 80 and Mabisang Komunikasyon 90 combine to 85.
    Against one 3-unit elective on 90:

        pair as one 2-unit entry (DO 017):  (85 x 2 + 90 x 3) / 5 = 88
        pair as two 2-unit entries (wrong): (80 x 2 + 90 x 2 + 90 x 3) / 7 = 87

    The wrong version is not obviously wrong on inspection, which is why it
    is asserted rather than reviewed.
    """
    combined = GradeUnits(D(85), D(2))
    elective = GradeUnits(D(90), D(3))
    assert compute_term_average([combined, elective], WEIGHTED) == 88

    double_counted = [GradeUnits(D(80), D(2)), GradeUnits(D(90), D(2)), elective]
    assert compute_term_average(double_counted, WEIGHTED) == 87


def test_the_pairs_annual_units_are_one_subjects_across_the_terms_it_runs():
    """Three terms at 2 units is 6 for the pair as a whole — the same as any
    other core subject, and half what summing the two components would give.
    """
    pair = GradeUnits(D(85), D(2) * D(3))
    core = GradeUnits(D(85), D(2) * D(3))
    assert total_units([pair, core], WEIGHTED) == 12


def test_g1_the_schools_own_grade_11_term_average():
    """§68 Test G1 — the case an adviser will actually see first.

    Annex E prints the language pair with a single term grade of its own.
    This school encodes the two components and derives the combined grade
    from them (§15), so the pair enters Term 1 at 85, not 80 — and the
    whole point of the amendment shows up in one mark:

        weighted, pair once  (6 entries, 13 units) = 1076 / 13 -> 83
        flat, pair twice     (7 entries)           = 585 / 7   -> 84

    84 is not a wrong answer that looks wrong. It is what the app printed
    the day before, which is why it is asserted rather than reviewed.
    """
    effcomm, mabkom = D(80), D(90)
    combined = D(85)  # ROUND((80 + 90) / 2), §15

    weighted = [
        GradeUnits(combined, D(2)),
        GradeUnits(D(90), D(2)),   # General Mathematics
        GradeUnits(D(76), D(2)),   # General Science
        GradeUnits(D(80), D(2)),   # Life and Career Skills
        GradeUnits(D(93), D(2)),   # Pag-aaral ng Kasaysayan at Lipunang Pilipino
        GradeUnits(D(76), D(3)),   # term-specific elective
    ]
    assert total_units(weighted, WEIGHTED) == 13
    assert sum(e.grade * e.units for e in weighted) == 1076
    assert compute_term_average(weighted, WEIGHTED) == 83

    superseded = [effcomm, mabkom, D(90), D(76), D(80), D(93), D(76)]
    assert len(superseded) == 7
    assert compute_term_average(superseded, UNWEIGHTED) == 84
