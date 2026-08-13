"""Every roster reads males first, then females, alphabetical within each.

That is how the DepEd forms are laid out (§34) and how the teachers' own
workbook is organised, so a screen in any other order makes them tick
names off against a list that doesn't match the paper in front of them.

The bug this pins: the sex is stored as the strings "MALE" and "FEMALE",
and sorting *those* alphabetically puts FEMALE first. The attendance
roster did exactly that while its docstring claimed the opposite — a
wrong order renders perfectly, so nothing ever complained.
"""

import pytest

from app.models.enums import Sex
from app.roster_order import learner_sort_key


class FakeLearner:
    def __init__(self, sex, last_name, first_name=""):
        self.sex = sex
        self.last_name = last_name
        self.first_name = first_name

    def __repr__(self):
        return f"{self.last_name}({getattr(self.sex, 'value', self.sex)})"


def test_males_come_before_females():
    rows = [FakeLearner(Sex.FEMALE, "ABAYAN"), FakeLearner(Sex.MALE, "ZAMORA")]
    assert [r.last_name for r in sorted(rows, key=learner_sort_key)] == ["ZAMORA", "ABAYAN"]


def test_the_naive_sort_would_get_this_backwards():
    """Guards the actual mistake rather than just the desired output:
    ordering on the stored value is alphabetical, and "FEMALE" < "MALE"."""
    assert Sex.FEMALE.value < Sex.MALE.value
    assert learner_sort_key(FakeLearner(Sex.MALE, "A")) < learner_sort_key(
        FakeLearner(Sex.FEMALE, "A")
    )


def test_alphabetical_within_each_group():
    rows = [
        FakeLearner(Sex.FEMALE, "REYES"),
        FakeLearner(Sex.MALE, "TISOY"),
        FakeLearner(Sex.FEMALE, "BRINGAS"),
        FakeLearner(Sex.MALE, "ABAYAN"),
    ]
    assert [r.last_name for r in sorted(rows, key=learner_sort_key)] == [
        "ABAYAN", "TISOY", "BRINGAS", "REYES",
    ]


def test_first_name_breaks_a_shared_surname():
    rows = [
        FakeLearner(Sex.MALE, "REYES", "MARCO"),
        FakeLearner(Sex.MALE, "REYES", "ANGELO"),
    ]
    assert [r.first_name for r in sorted(rows, key=learner_sort_key)] == ["ANGELO", "MARCO"]


def test_a_plain_string_sex_works_too():
    """Callers pass Learner rows, but the key reads `.sex` loosely so a
    tuple or a partially-built row doesn't need special handling."""
    assert learner_sort_key(FakeLearner("MALE", "A")) < learner_sort_key(
        FakeLearner("FEMALE", "A")
    )


@pytest.mark.parametrize("sex", [None, "", "X"])
def test_an_unknown_sex_sorts_last_instead_of_raising(sex):
    """Sex is required, so this only catches a half-built row. A name at
    the bottom of the roster beats a page that won't render."""
    rows = [FakeLearner(sex, "AAA"), FakeLearner(Sex.FEMALE, "ZZZ")]
    assert [r.last_name for r in sorted(rows, key=learner_sort_key)] == ["ZZZ", "AAA"]


def test_missing_names_do_not_raise():
    assert learner_sort_key(FakeLearner(Sex.MALE, None, None)) == (0, "", "")
