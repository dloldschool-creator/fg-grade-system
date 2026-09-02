"""Temporary term cards (§39) — the slip an adviser hands a learner at
the end of a term, generated directly with ReportLab.

**Eight cards to a 8.5 x 13 inch sheet**, two across and four down, which
is the Philippine "long bond" size the school actually prints on. §39's
own suggestion is six per landscape Letter; eight per long bond is the
school's instruction and fits the same content on the paper they have.

Like the award certificates, one drawing routine renders a single card
into an arbitrary rectangle and the page builder tiles it — so the
per-learner print and the whole-section batch cannot drift apart.

The subject list comes from `report_card.build_term_subject_rows`, and how
it treats the Grade 11 language pair follows the grading policy in force,
because **the list has to add up to the Term Average printed beneath it**.
Under DO 017 s. 2026 the pair is one learning area counted once, and prints
as a parent row with its two components indented under it; under
master-spec §17 it is two ordinary subjects, listed flat. This module draws
whatever it is handed and doesn't know which rule applied — the indent
arrives already in the name, exactly as it does on the SF9.
"""

import io
import os
from dataclasses import dataclass, field
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

SEAL_PATH = os.path.join(os.path.dirname(__file__), "assets", "fgnmhs_seal.png")

_BLUE = colors.HexColor("#1B4F9C")
_GRAY = colors.HexColor("#555555")
_RULE = colors.HexColor("#999999")

# Philippine long bond ("Folio"), which is what the school prints on.
PAGE_SIZE = (8.5 * inch, 13 * inch)
CARDS_ACROSS = 2
CARDS_DOWN = 4
CARDS_PER_PAGE = CARDS_ACROSS * CARDS_DOWN

# How many subject lines fit before the card has to elide.
#
# At the card's font sizes (8.3pt row pitch) the geometry allows about 13
# lines between the first subject line and the rule above TERM AVERAGE, so
# 12 leaves one line of margin for the "+N more" line — tighter than before
# the fonts were enlarged for readability (was ~4 lines of margin at 7.6pt),
# but still well clear of the realistic worst case: a full Grade 11 term
# with the language pair (parent + two indented components) plus three
# electives is 10 lines (see test_a_full_grade_11_term_fits_...). It was 8
# while the pair printed as two flat rows; under DO 017 the pair prints as
# a parent plus two indented components, which is three lines, and a Grade
# 11 term with three electives would have elided real subjects at the old
# cap.
MAX_SUBJECT_LINES = 12


@dataclass
class TermCardData:
    school_name: str
    term_name: str
    learner_name: str
    lrn: str
    grade_level: str
    section_name: str
    subjects: list[tuple[str, Decimal | None]] = field(default_factory=list)
    term_average: Decimal | None = None
    adviser_name: str = ""
    adviser_comment: str | None = None


def _grade_text(value) -> str:
    """Grades are whole numbers by construction (§60). A blank means not
    yet encoded — never rendered as 0 (rule 2)."""
    return str(int(value)) if value is not None else "—"


def _fit(c, text: str, font: str, size: float, max_width: float) -> str:
    """Truncates with an ellipsis rather than letting a long name spill
    into the neighbouring card."""
    if c.stringWidth(text, font, size) <= max_width:
        return text
    while text and c.stringWidth(text + "…", font, size) > max_width:
        text = text[:-1]
    return text + "…"


def _draw_card(c, data: TermCardData, *, x: float, y: float, width: float, height: float) -> None:
    """Draws one card inside the given rectangle."""
    pad = 7
    inner_x = x + pad
    inner_w = width - 2 * pad
    cursor = y + height - pad

    c.setStrokeColor(_RULE)
    c.setLineWidth(0.7)
    c.rect(x, y, width, height)

    # Header: seal on the left, school name and title beside it.
    seal = 26
    if os.path.exists(SEAL_PATH):
        c.drawImage(
            SEAL_PATH, inner_x, cursor - seal, width=seal, height=seal,
            preserveAspectRatio=True, mask="auto",
        )
    text_x = inner_x + seal + 5
    text_w = inner_w - seal - 5

    c.setFillColor(_BLUE)
    c.setFont("Helvetica-Bold", 7.2)
    c.drawString(text_x, cursor - 9, _fit(c, data.school_name.upper(), "Helvetica-Bold", 7.2, text_w))
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.4)
    c.drawString(text_x, cursor - 19, "TEMPORARY REPORT CARD")
    c.setFillColor(_GRAY)
    c.setFont("Helvetica", 7.4)
    c.drawString(text_x, cursor - 29, data.term_name)

    cursor -= seal + 5
    c.setStrokeColor(_RULE)
    c.setLineWidth(0.5)
    c.line(inner_x, cursor, inner_x + inner_w, cursor)

    # Learner identity.
    cursor -= 11
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9.2)
    c.drawString(inner_x, cursor, _fit(c, data.learner_name.upper(), "Helvetica-Bold", 9.2, inner_w))
    cursor -= 9.5
    c.setFillColor(_GRAY)
    c.setFont("Helvetica", 6.8)
    identity = f"LRN: {data.lrn or '—'}    {data.grade_level} · {data.section_name}"
    c.drawString(inner_x, cursor, _fit(c, identity, "Helvetica", 6.8, inner_w))

    # Subjects. The grade column is right-aligned against the card edge.
    cursor -= 10
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 7.2)
    grade_x = inner_x + inner_w
    name_w = inner_w - 28

    shown = data.subjects[:MAX_SUBJECT_LINES]
    for name, grade in shown:
        c.drawString(inner_x, cursor, _fit(c, name, "Helvetica", 7.2, name_w))
        c.drawRightString(grade_x, cursor, _grade_text(grade))
        cursor -= 8.3
    if len(data.subjects) > MAX_SUBJECT_LINES:
        c.setFillColor(_GRAY)
        c.drawString(inner_x, cursor, f"+{len(data.subjects) - MAX_SUBJECT_LINES} more")
        cursor -= 8.3

    # Term average sits just above the signature line, so it stays put
    # whatever the subject count.
    footer_y = y + pad
    average_y = footer_y + 23
    c.setStrokeColor(_RULE)
    c.setLineWidth(0.5)
    c.line(inner_x, average_y + 10, inner_x + inner_w, average_y + 10)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.0)
    c.drawString(inner_x, average_y, "TERM AVERAGE")
    c.drawRightString(grade_x, average_y, _grade_text(data.term_average))

    if data.adviser_comment:
        c.setFillColor(_GRAY)
        c.setFont("Helvetica-Oblique", 6.2)
        c.drawString(
            inner_x, average_y - 9,
            _fit(c, data.adviser_comment, "Helvetica-Oblique", 6.2, inner_w),
        )

    c.setFillColor(_GRAY)
    c.setFont("Helvetica", 6.2)
    c.drawRightString(
        grade_x, footer_y,
        _fit(c, f"{data.adviser_name.upper()} · Adviser", "Helvetica", 6.2, inner_w),
    )


def generate_term_cards(cards: list[TermCardData]) -> bytes:
    """Tiles the cards eight to a page, paginating automatically (§39 —
    the adviser never works out batches by hand)."""
    buffer = io.BytesIO()
    page_w, page_h = PAGE_SIZE
    c = canvas.Canvas(buffer, pagesize=PAGE_SIZE)

    margin = 0.3 * inch
    gutter = 0.12 * inch
    card_w = (page_w - 2 * margin - (CARDS_ACROSS - 1) * gutter) / CARDS_ACROSS
    card_h = (page_h - 2 * margin - (CARDS_DOWN - 1) * gutter) / CARDS_DOWN

    for index, data in enumerate(cards):
        slot = index % CARDS_PER_PAGE
        if slot == 0 and index:
            c.showPage()
        column = slot % CARDS_ACROSS
        row = slot // CARDS_ACROSS
        x = margin + column * (card_w + gutter)
        # Fill top-to-bottom, so reading order matches the roster order.
        y = page_h - margin - (row + 1) * card_h - row * gutter
        _draw_card(c, data, x=x, y=y, width=card_w, height=card_h)

    if cards:
        c.showPage()
    c.save()
    return buffer.getvalue()


def page_count(card_count: int) -> int:
    return -(-card_count // CARDS_PER_PAGE)  # ceil
