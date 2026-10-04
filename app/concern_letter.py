"""The printed concern letter (spec §78.5), drawn with ReportLab.

One page per learner, on the long bond (8.5 × 13 in) the school prints
on: letterhead with the seal, the date, the bilingual letter signed by the
adviser alone, and a tear-off acknowledgement slip at the foot. The words
come from `notice_messages.letter_paragraphs`; this module only lays them
out, so the wording has one home.

Helvetica's character set covers the en dash and ñ but not ballot boxes
or scissors, so the slip's tick boxes are drawn as rectangles and the cut
line is a dashed rule with its label in words.
"""

import io
import os
from dataclasses import dataclass

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

SEAL_PATH = os.path.join(os.path.dirname(__file__), "assets", "fgnmhs_seal.png")
PAGE_SIZE = (8.5 * inch, 13 * inch)
_BLUE = colors.HexColor("#1B4F9C")
_GRAY = colors.HexColor("#555555")

MARGIN = 0.9 * inch
BODY_FONT = ("Helvetica", 11)
LEADING = 15


@dataclass(frozen=True)
class LetterData:
    school_name: str
    school_address: str
    letter_date: str  # "October 5, 2026"
    words: dict  # from notice_messages.letter_paragraphs


def _paragraph(c, text, x, y, width, *, font=BODY_FONT, leading=LEADING) -> float:
    c.setFont(*font)
    for line in simpleSplit(text, font[0], font[1], width):
        c.drawString(x, y, line)
        y -= leading
    return y


def _box(c, x, y, size=9):
    c.rect(x, y - 1, size, size, stroke=1, fill=0)


def _draw_letter(c, data: LetterData) -> None:
    page_w, page_h = PAGE_SIZE
    width = page_w - 2 * MARGIN
    words = data.words
    y = page_h - 0.7 * inch

    # Letterhead
    seal = 0.85 * inch
    if os.path.exists(SEAL_PATH):
        c.drawImage(
            SEAL_PATH, MARGIN, y - seal, width=seal, height=seal,
            preserveAspectRatio=True, mask="auto",
        )
    c.setFillColor(_BLUE)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(MARGIN + seal + 12, y - 28, data.school_name.upper())
    c.setFillColor(_GRAY)
    c.setFont("Helvetica", 9.5)
    c.drawString(MARGIN + seal + 12, y - 44, "Senior High School")
    if data.school_address:
        c.drawString(MARGIN + seal + 12, y - 57, data.school_address)
    c.setFillColor(colors.black)
    y -= seal + 14
    c.setStrokeColor(_BLUE)
    c.setLineWidth(1.2)
    c.line(MARGIN, y, page_w - MARGIN, y)
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.8)
    y -= 28

    c.setFont(*BODY_FONT)
    c.drawRightString(page_w - MARGIN, y, data.letter_date)
    y -= 34

    for salutation, body in (
        ("salutation_en", "body_en"),
        ("salutation_fil", "body_fil"),
    ):
        c.setFont("Helvetica-Bold", 11)
        c.drawString(MARGIN, y, words[salutation])
        y -= LEADING + 6
        y = _paragraph(c, words[body], MARGIN, y, width)
        y -= 20

    y -= 6
    c.setFont(*BODY_FONT)
    c.drawString(MARGIN, y, words["closing"])
    y -= 44
    c.setFont("Helvetica-Bold", 11)
    c.drawString(MARGIN, y, words["signatory"])
    c.setLineWidth(0.6)
    c.line(MARGIN, y + 13, MARGIN + 2.6 * inch, y + 13)
    y -= LEADING
    c.setFont(*BODY_FONT)
    c.drawString(MARGIN, y, words["signatory_title"])

    # Tear-off slip, anchored to the foot of the page so it is always in
    # the same place whatever the letter's length.
    slip_top = 3.3 * inch
    c.setDash(4, 3)
    c.line(0.4 * inch, slip_top, page_w - 0.4 * inch, slip_top)
    c.setDash()
    c.setFont("Helvetica-Oblique", 7.5)
    c.setFillColor(_GRAY)
    c.drawCentredString(page_w / 2, slip_top + 4, "cut here / gupitin dito")
    c.setFillColor(colors.black)

    y = slip_top - 26
    c.setFont("Helvetica-Bold", 11)
    c.drawString(MARGIN, y, words["slip_title"])
    c.setFont("Helvetica", 9)
    c.drawString(MARGIN, y - 13, words["slip_note"])
    y -= 34
    y = _paragraph(c, words["slip_received"], MARGIN, y, width, font=("Helvetica", 10.5))
    y -= 6
    _box(c, MARGIN, y)
    c.setFont("Helvetica", 10.5)
    c.drawString(MARGIN + 16, y, words["slip_attend"])
    y -= 22
    _box(c, MARGIN, y)
    c.drawString(MARGIN + 16, y, words["slip_other"])
    c.line(MARGIN + 16, y - 16, page_w - MARGIN, y - 16)
    y -= 46
    third = width / 3
    for index, label in enumerate(("Name / Pangalan", "Signature / Lagda", "Date / Petsa")):
        x = MARGIN + index * third
        c.line(x, y, x + third - 14, y)
        c.setFont("Helvetica", 9)
        c.drawString(x, y - 12, label)


def generate_concern_letters(letters: list[LetterData]) -> bytes:
    """One page per letter, in the order given (the roster's)."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=PAGE_SIZE)
    for data in letters:
        _draw_letter(c, data)
        c.showPage()
    c.save()
    return buffer.getvalue()
