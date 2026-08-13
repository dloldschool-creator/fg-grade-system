"""What Excel does to a spreadsheet on the way to being uploaded.

Every case here came from a real failed upload. The theme is that the
teacher typed the right thing and Excel changed it: a 12-digit LRN became
a float in scientific notation, and a birthdate typed as YYYY-MM-DD was
rewritten into the PC's regional order on save.

None of these produce an exception. They produce a *wrong record* — the
wrong birthday, or an LRN that fails a length check for no visible
reason — which is why they are pinned here rather than left to be noticed.
"""

from datetime import date

import pytest

from app.import_pipeline import (
    DEFAULT_DATE_ORDER,
    detect_date_order,
    parse_date,
    parse_lrn,
)
from app.import_specs import _parse_sex
from app.models.enums import Sex


# --- LRN: Excel treats twelve digits as a number ---------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "107041140016",        # typed as text, the way we ask for
        "107041140016.0",      # read back as a float
        " 107041140016 ",      # padded by a copy-paste
        "1.07041140016E+11",   # saved to CSV in Excel's display form
        "1.07041140016e11",
    ],
)
def test_an_lrn_survives_every_shape_excel_gives_it_back_in(raw):
    """A teacher should not have to reformat the column as text first —
    each of these is recoverable arithmetically, with nothing guessed."""
    assert parse_lrn(raw) == ("107041140016", None)


def test_a_rounded_lrn_is_still_rejected():
    """1.07E+11 has genuinely lost its digits — Excel rounded them away
    when it wrote the file. Expanding it would invent a number, so it is
    reported instead. This is the one case the teacher must fix at source.
    """
    value, error = parse_lrn("1.07E+11")
    assert value is None
    assert ".xlsx" in error
    # The tempting bug: 1.07E+11 expands cleanly to 107000000000, which is
    # exactly twelve digits and passes every other check.
    assert "107000000000" not in str(value)


def test_rounding_manufactures_duplicates_that_are_not_real():
    """Why accepting a rounded LRN would be worse than rejecting it.

    A real upload had two different learners whose LRNs Excel wrote as
    1.07023E+11. Expanded, both become 107023000000 — so accepting them
    would report a duplicate that does not exist, reject one learner who
    is genuinely distinct, and store a fabricated LRN for the other.
    """
    first, first_error = parse_lrn("1.07023E+11")
    second, second_error = parse_lrn("1.07023E+11")
    assert first is None and second is None
    assert first_error and second_error


@pytest.mark.parametrize("raw", ["12345", "abcdefghijkl", "10704114001X"])
def test_a_genuinely_wrong_lrn_is_still_an_error(raw):
    value, error = parse_lrn(raw)
    assert value is None and error


def test_blank_stays_blank():
    """An LRN is optional — not yet assigned is a real state, and must
    never become a placeholder (rule 2's spirit)."""
    assert parse_lrn("") == (None, None)
    assert parse_lrn(None) == (None, None)


# --- Birthdate: Excel rewrites it to the PC's regional format --------------


def test_iso_is_read_the_same_whichever_order_is_in_force():
    for order in ("mdy", "dmy"):
        assert parse_date("2009-01-15", order) == (date(2009, 1, 15), None)


def test_a_slash_date_follows_the_order_it_is_given():
    assert parse_date("01/15/2009", "mdy") == (date(2009, 1, 15), None)
    assert parse_date("15/01/2009", "dmy") == (date(2009, 1, 15), None)


def test_the_order_is_decided_from_the_whole_column_not_one_value():
    """03/04/2009 alone is two different days. One unambiguous value
    elsewhere in the file settles it for every row."""
    assert detect_date_order(["03/04/2009", "25/12/2008"]) == "dmy"
    assert detect_date_order(["03/04/2009", "12/25/2008"]) == "mdy"


def test_an_entirely_ambiguous_column_falls_back_to_the_regional_default():
    """Nothing in the file can decide it, so it uses the order the school's
    machines actually write. Silent either way — but wrong less often."""
    assert detect_date_order(["03/04/2009", "05/06/2008"]) == DEFAULT_DATE_ORDER


def test_contradictory_evidence_does_not_pick_a_side():
    """A column holding both 25/12 and 12/25 is inconsistent at source.
    Guessing would silently corrupt half of it, so it defaults instead."""
    assert detect_date_order(["25/12/2008", "12/25/2008"]) == DEFAULT_DATE_ORDER


def test_detection_ignores_blanks_and_junk():
    assert detect_date_order(["", None, "not a date", "25/12/2008"]) == "dmy"


def test_an_impossible_date_is_reported_not_raised():
    """§51 names "impossible date" as a validation error, which means the
    row is listed for the user, not that the upload dies."""
    value, error = parse_date("2009-02-30")
    assert value is None and error


def test_a_blank_birthdate_is_not_an_error_here():
    """Required-ness is the validator's call, not the parser's — so that a
    missing value reads as "required" rather than "unrecognisable"."""
    assert parse_date("") == (None, None)


# --- Sex: the file may abbreviate, the database never does -----------------


@pytest.mark.parametrize("raw", ["M", "m", "MALE", "male", " Male ", "  m"])
def test_every_way_of_writing_male_is_stored_as_male(raw):
    """A masterlist that abbreviates to M/F must land in the database as
    MALE/FEMALE — the reports read the stored value, and a stray "M" would
    be a sex the enum has no name for."""
    sex, error = _parse_sex(raw)
    assert error is None
    assert sex is Sex.MALE
    assert sex.value == "MALE"


@pytest.mark.parametrize("raw", ["F", "f", "FEMALE", "female", " Female "])
def test_every_way_of_writing_female_is_stored_as_female(raw):
    sex, error = _parse_sex(raw)
    assert error is None
    assert sex is Sex.FEMALE
    assert sex.value == "FEMALE"


@pytest.mark.parametrize("raw", ["", None, "X", "BOY", "1"])
def test_anything_else_is_an_error_rather_than_a_guess(raw):
    sex, error = _parse_sex(raw)
    assert sex is None and error
