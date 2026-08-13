"""Tests for app/certificate_generator.py — the wording rules and the
one-per-page / two-per-page output shapes."""

import re
from datetime import date
from decimal import Decimal

import pytest
from reportlab.lib.pagesizes import landscape, letter

from app.certificate_generator import (
    CertificateData,
    _citation,
    certificate_award_name,
    formal_term_name,
    _given_line,
    _ordinal,
    generate_award_certificate,
    generate_award_certificates_2up,
)


# --- What the award is called on the certificate ---------------------------


def test_the_deped_order_and_version_come_off_the_printed_name():
    """The policy is named for the administrators who maintain it. A
    learner's certificate should read "Academic Excellence Award", not
    cite the order it was created under."""
    assert (
        certificate_award_name("Academic Excellence Award (DO 15, s. 2026) (v1)")
        == "Academic Excellence Award"
    )
    assert (
        certificate_award_name("Academic Excellence Award (DO 15, s. 2026)")
        == "Academic Excellence Award"
    )


@pytest.mark.parametrize(
    "label", ["With Highest Honors", "With High Honors", "With Honors"]
)
def test_the_tiered_honors_labels_are_untouched(label):
    """They carry no parenthetical, so there is nothing to strip — and
    stripping them would be wrong, these are the award names themselves."""
    assert certificate_award_name(label) == label


def test_only_trailing_parentheticals_are_stripped():
    """A parenthetical that is part of the award reads mid-string."""
    assert certificate_award_name("Best in (Applied) Mathematics") == (
        "Best in (Applied) Mathematics"
    )


def test_it_never_strips_its_way_down_to_nothing():
    """A name that is *entirely* a parenthetical keeps it rather than
    printing a blank line where the award should be."""
    assert certificate_award_name("(v1)") == "(v1)"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_missing_award_name_falls_back_rather_than_printing_empty(blank):
    assert certificate_award_name(blank) == "RECOGNITION"


def test_the_citation_line_uses_the_cleaned_name():
    """The wording rule and the cleaning have to meet somewhere — this is
    the line a parent actually reads."""
    data = _certificate(award_name=certificate_award_name(
        "Academic Excellence Award (DO 15, s. 2026) (v1)"
    ))
    assert "Academic Excellence Award" in _citation(data)
    assert "DO 15" not in _citation(data)
    assert "(v1)" not in _citation(data)


def _certificate(**overrides) -> CertificateData:
    fields = dict(
        school_name="Francisco G. Nepomuceno Memorial High School",
        schools_division="Schools Division of Angeles City",
        learner_name="DELA CRUZ, JUAN",
        award_name="WITH HONORS",
        general_average=Decimal(92),
        recognition_date=date(2026, 10, 17),
        recognition_venue="FGNMHS Covered Court",
        school_year_name="2026-2027",
        adviser_name="DENNY LAINE LIWAG",
        school_head_name="JUAN D. REYES",
        school_head_position="Head Teacher III",
    )
    fields.update(overrides)
    return CertificateData(**fields)


# --- Wording ---------------------------------------------------------------


def test_term_certificate_cites_the_term_average():
    """A term certificate must not claim a General Average the learner
    hasn't earned yet — the year isn't finished. The stored label is
    rewritten to formal prose: "First Term", not "Term 1"."""
    assert _citation(_certificate(term_name="Term 1")) == (
        "for earning WITH HONORS with a First Term Average of 92."
    )


def test_annual_certificate_cites_the_general_average():
    assert _citation(_certificate()) == (
        "for earning WITH HONORS with a General Average of 92."
    )


def test_missing_average_does_not_print_none():
    assert "—" in _citation(_certificate(general_average=None))


def test_blank_venue_is_omitted_rather_than_left_dangling():
    """Without this the line reads "...October 2026 at , during School
    Year..." — likely for a term award, where the adviser has no venue to
    give."""
    line = _given_line(_certificate(recognition_venue=""))
    assert "at ," not in line
    assert line == "Given this 17th of October 2026, during School Year 2026-2027."


def test_venue_is_included_when_set():
    assert "at FGNMHS Covered Court," in _given_line(_certificate())


@pytest.mark.parametrize(
    "day, expected",
    [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"),
     (12, "12th"), (13, "13th"), (21, "21st"), (22, "22nd"), (23, "23rd")],
)
def test_ordinal_handles_the_teens_exception(day, expected):
    assert _ordinal(day) == expected


@pytest.mark.parametrize(
    "stored, expected",
    [("Term 1", "First Term"), ("Term 2", "Second Term"), ("Term 3", "Third Term"),
     ("term 1", "First Term"), ("  Term 2  ", "Second Term")],
)
def test_term_labels_are_rewritten_as_formal_prose(stored, expected):
    """"Term 1 Average" reads like a column heading; "First Term Average"
    reads like a citation. Spelled out rather than "1st", since numerals
    read as abbreviations in formal prose."""
    assert formal_term_name(stored) == expected


@pytest.mark.parametrize("renamed", ["Midyear", "Summer Term", "First Semester"])
def test_renamed_terms_pass_through_untouched(renamed):
    """Only the seeded "Term <n>" shape is rewritten — a term the admin
    renamed shouldn't be mangled."""
    assert formal_term_name(renamed) == renamed


# --- Page shapes -----------------------------------------------------------


def _page_sizes(pdf_bytes: bytes) -> list[tuple[int, int]]:
    """Page dimensions straight out of the PDF's /MediaBox entries.

    Parsed from the bytes rather than with a PDF library on purpose: the
    only readers available here (pypdf, pymupdf) aren't in
    requirements.txt, so depending on one would make these tests pass
    locally and fail anywhere else. ReportLab writes exactly one
    /MediaBox per page, so the count is the page count.
    """
    boxes = re.findall(
        rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]", pdf_bytes
    )
    return [(round(float(x2) - float(x1)), round(float(y2) - float(y1))) for x1, y1, x2, y2 in boxes]


def test_single_certificate_is_one_landscape_page():
    sizes = _page_sizes(generate_award_certificate(**_certificate().__dict__))
    assert sizes == [tuple(round(v) for v in landscape(letter))]


def test_two_up_puts_two_certificates_on_one_portrait_page():
    """Halves the paper for classroom-level Honors."""
    pdf = generate_award_certificates_2up([_certificate(), _certificate()])
    assert _page_sizes(pdf) == [tuple(round(v) for v in letter)]
    # Portrait, unlike the one-per-page landscape output.
    width, height = _page_sizes(pdf)[0]
    assert height > width


@pytest.mark.parametrize(
    "count, expected_pages", [(1, 1), (2, 1), (3, 2), (4, 2), (5, 3), (60, 30)]
)
def test_two_up_page_count(count, expected_pages):
    """An odd count leaves the bottom half of the last sheet blank rather
    than stretching one certificate to fill it."""
    pdf = generate_award_certificates_2up([_certificate() for _ in range(count)])
    assert len(_page_sizes(pdf)) == expected_pages


def test_two_up_is_empty_pdf_for_no_certificates():
    assert _page_sizes(generate_award_certificates_2up([])) == []
