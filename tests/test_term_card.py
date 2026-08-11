"""Tests for app/term_card.py — the temporary term card layout (§39)."""

import re
from decimal import Decimal

import pytest

from app.term_card import (
    CARDS_PER_PAGE,
    MAX_SUBJECT_LINES,
    PAGE_SIZE,
    TermCardData,
    _grade_text,
    generate_term_cards,
    page_count,
)

D = Decimal


def _card(name="DELA CRUZ, JUAN", subjects=None, average=D(92), comment=None) -> TermCardData:
    return TermCardData(
        school_name="Francisco G. Nepomuceno Memorial High School",
        term_name="Term 1",
        learner_name=name,
        lrn="107041140016",
        grade_level="G11",
        section_name="STEM - A",
        subjects=subjects if subjects is not None else [("General Mathematics", D(90))],
        term_average=average,
        adviser_name="Denny Laine Liwag",
        adviser_comment=comment,
    )


def _page_sizes(pdf: bytes) -> list[tuple[int, int]]:
    """Read straight from /MediaBox — no PDF library is in
    requirements.txt, so depending on one would make these pass locally
    and fail elsewhere."""
    boxes = re.findall(
        rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]", pdf
    )
    return [(round(float(x2) - float(x1)), round(float(y2) - float(y1))) for x1, y1, x2, y2 in boxes]


# --- Page geometry ---------------------------------------------------------


def test_page_is_philippine_long_bond():
    """8.5 x 13in — what the school prints on, rather than §39's suggested
    landscape Letter."""
    width, height = PAGE_SIZE
    assert round(width / 72, 2) == 8.5
    assert round(height / 72, 2) == 13.0


def test_eight_cards_per_sheet():
    assert CARDS_PER_PAGE == 8


@pytest.mark.parametrize(
    "learners, sheets",
    [(1, 1), (7, 1), (8, 1), (9, 2), (16, 2), (17, 3), (48, 6)],
)
def test_pagination_is_automatic(learners, sheets):
    """§39: the adviser never works out batches by hand."""
    assert page_count(learners) == sheets
    pdf = generate_term_cards([_card() for _ in range(learners)])
    assert len(_page_sizes(pdf)) == sheets


def test_every_page_is_long_bond():
    pdf = generate_term_cards([_card() for _ in range(9)])
    assert _page_sizes(pdf) == [(612, 936), (612, 936)]


def test_no_learners_produces_an_empty_document():
    assert _page_sizes(generate_term_cards([])) == []


# --- Card content ----------------------------------------------------------


def test_missing_grade_prints_a_dash_not_zero():
    """The NULL-is-not-zero rule (rule 2) reaching the card: a subject
    with no grade encoded yet must not read as a zero."""
    assert _grade_text(None) == "—"
    assert _grade_text(D(90)) == "90"


def test_grades_print_as_whole_numbers():
    """Stored Numeric(5,2), integral by construction (§60)."""
    assert _grade_text(D("93.00")) == "93"


def test_long_subject_lists_do_not_overrun_the_card():
    """A card is a fixed box; more subjects than fit are summarised
    rather than spilling into the card beneath."""
    many = [(f"Subject {i}", D(90)) for i in range(MAX_SUBJECT_LINES + 4)]
    pdf = generate_term_cards([_card(subjects=many)])
    assert len(_page_sizes(pdf)) == 1  # still one sheet, nothing pushed off


def test_a_card_with_a_comment_still_fits_one_sheet():
    pdf = generate_term_cards(
        [_card(comment="Keep up the excellent work this term.") for _ in range(8)]
    )
    assert len(_page_sizes(pdf)) == 1
