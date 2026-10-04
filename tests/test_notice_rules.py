"""Spec §78.2–78.3: which group a learner's parent notice falls into.

Pure tests of `app.notice_rules` — no database. The thresholds are the
ones the school approved (at most 2 absences, 2 lates, no cutting), passed
in rather than read, exactly as the service does.
"""

import pytest

from app.notice_rules import (
    LearnerTermFigures,
    NoticeGroup,
    NoticeThresholds,
    classify,
    effective_group,
    may_override,
)

POLICY = NoticeThresholds(max_absences=2, max_lates=2, max_cuttings=0, version=1)


def figures(**overrides) -> LearnerTermFigures:
    base = dict(
        summary_found=True,
        grades_complete=True,
        failed_count=0,
        eligible_days=60,
        unencoded_days=0,
        absences=0,
        lates=0,
        cuttings=0,
    )
    base.update(overrides)
    return LearnerTermFigures(**base)


def group(**overrides) -> NoticeGroup:
    return classify(figures(**overrides), POLICY).group


def test_a_clean_record_is_released():
    assert group() is NoticeGroup.RELEASE


@pytest.mark.parametrize(
    "field, at_limit, over",
    [("absences", 2, 3), ("lates", 2, 3), ("cuttings", 0, 1)],
)
def test_each_threshold_is_inclusive_at_the_limit_and_concern_one_over(field, at_limit, over):
    assert group(**{field: at_limit}) is NoticeGroup.RELEASE
    assert group(**{field: over}) is NoticeGroup.CONCERN


def test_one_failing_subject_is_concern():
    result = classify(figures(failed_count=1), POLICY)
    assert result.group is NoticeGroup.CONCERN
    assert result.reasons == ("failing 1 subject",)


def test_concern_lists_every_trigger_not_just_the_first():
    """The concern list is OR across triggers; the adviser sees all of them."""
    result = classify(figures(failed_count=2, absences=4, lates=3, cuttings=1), POLICY)
    assert result.reasons == ("failing 2 subjects", "4 absences", "3 lates", "1 cutting")


# --- Not ready comes first -------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    [
        {"summary_found": False, "grades_complete": False, "failed_count": None},
        {"grades_complete": False},
        # Complete but never summarised: None is not zero (rule 2).
        {"failed_count": None},
        {"unencoded_days": 1},
        {"eligible_days": 0},
    ],
)
def test_an_incomplete_record_is_not_ready_never_released(missing):
    assert group(**missing) is NoticeGroup.NOT_READY


def test_not_ready_wins_even_over_an_obvious_concern():
    """A learner failing with blank grades still waits: the blank grades
    might be failures too, and the parent should get one complete notice."""
    assert group(failed_count=3, absences=10, unencoded_days=2) is NoticeGroup.NOT_READY


def test_a_half_encoded_month_is_not_mistaken_for_perfect_attendance():
    """Zero absences on a sheet nobody has filled in is not a clean record."""
    result = classify(figures(absences=0, unencoded_days=40), POLICY)
    assert result.group is NoticeGroup.NOT_READY
    assert result.reasons == ("40 attendance days not encoded",)


def test_not_ready_reports_both_grades_and_attendance():
    result = classify(figures(grades_complete=False, unencoded_days=1), POLICY)
    assert result.reasons == (
        "some grades for this term are still blank",
        "1 attendance day not encoded",
    )


# --- Overrides --------------------------------------------------------------


@pytest.mark.parametrize("override", [NoticeGroup.RELEASE, NoticeGroup.CONCERN])
def test_an_override_moves_a_ready_learner_either_way(override):
    for computed in (NoticeGroup.RELEASE, NoticeGroup.CONCERN):
        assert effective_group(computed, override) is override


def test_an_override_never_lifts_not_ready():
    assert effective_group(NoticeGroup.NOT_READY, NoticeGroup.RELEASE) is NoticeGroup.NOT_READY
    assert not may_override(NoticeGroup.NOT_READY)


def test_no_override_leaves_the_computed_group():
    assert effective_group(NoticeGroup.CONCERN, None) is NoticeGroup.CONCERN


def test_the_policy_reads_back_in_plain_words():
    assert POLICY.describe() == (
        "no failing grade, at most 2 absence(s), at most 2 late(s) and no cutting"
    )
