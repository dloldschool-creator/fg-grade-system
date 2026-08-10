"""Implements the required tests from master-spec.md §68 (Test A, B, C, F —
the ones that are about grade computation; Test D is SF2/attendance and
Test E is awards, each belonging to their own later phase)."""

from decimal import Decimal

from app.grading_engine import (
    compute_combined_language_final_grade,
    compute_combined_language_term_grade,
    compute_general_average,
    compute_subject_final_grade,
    compute_term_average,
    determine_pass_fail,
    round_half_up,
)

D = Decimal


def test_round_half_up_differs_from_python_builtin_banker_rounding():
    # Python's round() rounds half-to-even: round(92.5) == 92, round(2.5) == 2.
    # DepEd/Excel ROUND() rounds half away from zero. This is exactly the
    # boundary Test A's combined-language final grade hits (92.5 -> 93) —
    # get this wrong and every combined-language General Average is off.
    assert round_half_up(D("92.5")) == 93
    assert round_half_up(D("2.5")) == 3
    assert round_half_up(D("91.5")) == 92  # sanity: still rounds up correctly when odd-adjacent


def test_a_grade11_combined_language():
    effcomm_terms = {1: D(90), 2: D(98), 3: D(92)}
    mabkom_terms = {1: D(92), 2: D(90), 3: D(93)}
    all_three_terms = {1, 2, 3}

    effcomm_final = compute_subject_final_grade(effcomm_terms, all_three_terms)
    mabkom_final = compute_subject_final_grade(mabkom_terms, all_three_terms)
    assert effcomm_final == 93
    assert mabkom_final == 92

    combined_final = compute_combined_language_final_grade(effcomm_final, mabkom_final)
    assert combined_final == 93  # ROUND((93 + 92) / 2) = 93, per master-spec.md §14

    combined_t1 = compute_combined_language_term_grade(effcomm_terms[1], mabkom_terms[1])
    combined_t2 = compute_combined_language_term_grade(effcomm_terms[2], mabkom_terms[2])
    combined_t3 = compute_combined_language_term_grade(effcomm_terms[3], mabkom_terms[3])
    assert (combined_t1, combined_t2, combined_t3) == (91, 94, 93)

    # Annual General Average counts the combined final ONCE, not the two
    # component finals separately (§19) — caller assembles this list;
    # simulating a G11 enrollment with just this one (combined) learning
    # area plus one ordinary subject.
    other_subject_final = D(85)
    general_average = compute_general_average([combined_final, other_subject_final])
    assert general_average == round_half_up((D(93) + D(85)) / 2)
    assert general_average == 89  # (93+85)/2 = 89.0

    # And NOT the (wrong) three-way average that double-counts the language pair.
    wrong_if_double_counted = compute_general_average(
        [effcomm_final, mabkom_final, other_subject_final]
    )
    assert general_average != wrong_if_double_counted


def test_b_term_specific_elective():
    # Subject active only in Term 2, with grade 91.
    final = compute_subject_final_grade({2: D(91)}, {2})
    assert final == 91  # passed through unchanged, not averaged/rounded


def test_c_missing_required_term_is_incomplete():
    # Subject active in T1/T2/T3 but T3 is blank.
    final = compute_subject_final_grade({1: D(88), 2: D(90), 3: None}, {1, 2, 3})
    assert final is None
    assert determine_pass_fail(final, passing_grade=D(75)) == "INCOMPLETE"


def test_f_null_never_becomes_zero():
    # A completely unencoded subject.
    final = compute_subject_final_grade({1: None}, {1})
    assert final is None  # not 0

    # General Average with any missing input is None, not computed from
    # whatever happens to be present.
    assert compute_general_average([D(90), None, D(85)]) is None
    assert compute_general_average([None]) is None
    assert compute_general_average([]) is None

    # Combined-language helpers propagate None the same way.
    assert compute_combined_language_term_grade(D(90), None) is None
    assert compute_combined_language_final_grade(None, D(90)) is None


def test_determine_pass_fail_thresholds():
    passing = D(75)
    assert determine_pass_fail(D(75), passing) == "PASSED"
    assert determine_pass_fail(D("74.9"), passing) == "FAILED"
    assert determine_pass_fail(None, passing) == "INCOMPLETE"


def test_two_term_subject_averages_only_its_two_terms():
    final = compute_subject_final_grade({1: D(80), 2: D(90), 3: D(100)}, {1, 2})
    assert final == round_half_up((D(80) + D(90)) / 2)
    assert final == 85


def test_term_average_uses_spec_worked_example():
    """§17's own example: a G11 academic section has SEVEN term-grade
    entries, because Effective Communication and Mabisang Komunikasyon
    each count separately."""
    grades = [D(90), D(88), D(85), D(92), D(87), D(91), D(89)]
    assert len(grades) == 7
    assert compute_term_average(grades) == round_half_up(sum(grades) / D(7))


def test_term_average_counts_the_language_pair_separately():
    """The rule most likely to be got wrong: §17 says outright "Do not
    substitute the combined language grade when calculating the Term
    Average", which is the opposite of the General Average rule (§19).

    Effective Communication 90 and Mabisang Komunikasyon 80 combine to 85
    for SF9 display. Averaged against a single other subject of 100:
      - as two separate entries (correct): (90 + 80 + 100) / 3 = 90
      - as one combined entry (wrong):     (85 + 100) / 2      = 93
    """
    correct = compute_term_average([D(90), D(80), D(100)])
    assert correct == 90

    combined = compute_combined_language_term_grade(D(90), D(80))
    assert combined == 85
    wrong = compute_term_average([combined, D(100)])
    assert wrong == 93
    assert correct != wrong


def test_term_average_is_none_when_any_subject_is_unencoded():
    assert compute_term_average([D(90), None, D(85)]) is None
    assert compute_term_average([]) is None


def test_term_average_rounds_half_up():
    # (92 + 93) / 2 = 92.5 -> 93, not Python round()'s 92.
    assert compute_term_average([D(92), D(93)]) == 93
