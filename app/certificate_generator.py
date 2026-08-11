"""Award certificate PDF (§40) — direct ReportLab generation, matching
the layout the user supplied as a reference image. This is a new custom
design with no pre-existing official DepEd template to preserve pixel-for-
pixel (unlike SF9/SF2), so it's generated directly rather than through the
openpyxl-fill-then-LibreOffice-convert pipeline used for those forms.

Two output shapes share one drawing routine:

- **one per page** (`generate_award_certificate`) — full landscape Letter,
  used for the Academic Excellence Award, which is a DepEd order.
- **two per page** (`generate_award_certificates_2up`) — two half-page
  certificates on portrait Letter with a cut line between them, to save
  paper on the tiered Honors, which is classroom-level recognition rather
  than an official issuance.

`_draw_certificate` draws into an arbitrary rectangle at an arbitrary
scale, so both callers use exactly the same layout and nothing can drift
between them.
"""

import io
import os
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

SEAL_PATH = os.path.join(os.path.dirname(__file__), "assets", "fgnmhs_seal.png")

_BLUE = colors.HexColor("#1B4F9C")
_GRAY = colors.HexColor("#555555")

# The design's native size — a landscape Letter page. Every measurement
# below is expressed for this size and then scaled to whatever box it's
# drawn into, so the one-per-page and two-per-page outputs stay identical
# in proportion.
_DESIGN_W, _DESIGN_H = landscape(letter)

# Total height of the title-through-date block (the sum of the y-steps
# between its five lines). Used to centre that block vertically rather
# than letting it hang off the bottom of the header — keep it in sync if
# those steps change.
_BODY_BLOCK_HEIGHT = 120


@dataclass
class CertificateData:
    """One certificate's content. Grouped so the batch renderer can take a
    list without a twelve-argument call per item."""

    school_name: str
    schools_division: str
    learner_name: str
    award_name: str
    general_average: Decimal | None
    recognition_date: date
    recognition_venue: str
    school_year_name: str
    adviser_name: str
    school_head_name: str
    school_head_position: str
    term_name: str | None = None


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


_TERM_LABEL = re.compile(r"^\s*term\s*(\d+)\s*$", re.IGNORECASE)

# Spelled out rather than "1st/2nd/3rd" — a certificate is formal prose,
# where numerals read as abbreviations. Only as many as a school year
# realistically has; anything beyond falls back to the numeral form.
_SPELLED_ORDINALS = {
    1: "First",
    2: "Second",
    3: "Third",
    4: "Fourth",
    5: "Fifth",
    6: "Sixth",
}


def formal_term_name(term_name: str) -> str:
    """Turns the stored label "Term 1" into "First Term" for certificate
    prose — "Term 1 Average" reads like a column heading, "First Term
    Average" reads like a citation.

    Only the recognised "Term <n>" shape is rewritten. A term the admin
    renamed to something else ("Midyear", say) passes through untouched
    rather than being mangled, since only the seeded names follow this
    pattern.
    """
    match = _TERM_LABEL.match(term_name)
    if not match:
        return term_name
    number = int(match.group(1))
    return f"{_SPELLED_ORDINALS.get(number, _ordinal(number))} Term"


def _citation(data: CertificateData) -> str:
    """A term certificate must not claim a General Average the learner
    hasn't earned yet — it cites that term's average instead."""
    average = int(data.general_average) if data.general_average is not None else "—"
    label = (
        f"{formal_term_name(data.term_name)} Average" if data.term_name else "General Average"
    )
    return f"for earning {data.award_name} with a {label} of {average}."


def _given_line(data: CertificateData) -> str:
    """The venue is omitted rather than left dangling — an unset venue
    would otherwise render "Given this 3rd of April 2027 at , during…"."""
    given = f"Given this {_ordinal(data.recognition_date.day)} of {data.recognition_date:%B %Y}"
    if data.recognition_venue:
        given += f" at {data.recognition_venue}"
    return f"{given}, during School Year {data.school_year_name}."


def _draw_certificate(c, data: CertificateData, *, x: float, y: float, width: float, height: float) -> None:
    """Draws one certificate inside the given rectangle.

    The design is laid out at landscape-Letter size and uniformly scaled
    to fit, preserving its aspect ratio and centring within the box — so
    a half-page certificate is the same design, just smaller, rather than
    a squashed one.
    """
    scale = min(width / _DESIGN_W, height / _DESIGN_H)
    inner_w, inner_h = _DESIGN_W * scale, _DESIGN_H * scale
    x0 = x + (width - inner_w) / 2
    y0 = y + (height - inner_h) / 2
    top = y0 + inner_h
    center_x = x0 + inner_w / 2

    def font(name: str, size: float) -> None:
        c.setFont(name, size * scale)

    margin = 0.35 * inch * scale
    c.setStrokeColor(colors.black)
    c.setLineWidth(1.2 * scale)
    c.rect(x0 + margin, y0 + margin, inner_w - 2 * margin, inner_h - 2 * margin)

    if os.path.exists(SEAL_PATH):
        seal = 0.95 * inch * scale
        c.drawImage(
            SEAL_PATH,
            center_x - seal / 2,
            top - 1.55 * inch * scale,
            width=seal,
            height=seal,
            preserveAspectRatio=True,
            mask="auto",
        )

    cursor = top - 1.75 * inch * scale
    font("Times-Bold", 12)
    c.setFillColor(colors.black)
    c.drawCentredString(center_x, cursor, "Republic of the Philippines")
    cursor -= 15 * scale
    font("Times-Bold", 13)
    c.drawCentredString(center_x, cursor, "DEPARTMENT OF EDUCATION")
    cursor -= 18 * scale
    font("Times-Bold", 15)
    c.setFillColor(_BLUE)
    c.drawCentredString(center_x, cursor, data.school_name.upper())
    cursor -= 14 * scale
    font("Times-Italic", 9)
    c.setFillColor(_GRAY)
    c.drawCentredString(center_x, cursor, data.schools_division)

    # The title down to the "Given this..." line reads as one block, so
    # position it off the box's vertical centre rather than off the
    # header — otherwise it sits high and leaves a dead gap above the
    # signatures.
    cursor = y0 + inner_h / 2 + (_BODY_BLOCK_HEIGHT / 2) * scale
    font("Times-Bold", 26)
    c.setFillColor(_BLUE)
    c.drawCentredString(center_x, cursor, "CERTIFICATE OF RECOGNITION")

    cursor -= 30 * scale
    font("Times-Italic", 11)
    c.setFillColor(_GRAY)
    c.drawCentredString(center_x, cursor, "is proudly presented to")

    cursor -= 34 * scale
    font("Times-BoldItalic", 24)
    c.setFillColor(colors.black)
    c.drawCentredString(center_x, cursor, data.learner_name.upper())

    cursor -= 32 * scale
    font("Times-Roman", 12)
    c.setFillColor(colors.black)
    c.drawCentredString(center_x, cursor, _citation(data))

    cursor -= 24 * scale
    font("Times-Roman", 10)
    c.drawCentredString(center_x, cursor, _given_line(data))

    sig_y = y0 + margin + 0.9 * inch * scale
    left_x = x0 + inner_w * 0.28
    right_x = x0 + inner_w * 0.72
    font("Times-Bold", 11)
    c.drawCentredString(left_x, sig_y, data.adviser_name.upper())
    c.drawCentredString(right_x, sig_y, data.school_head_name.upper())
    font("Times-Roman", 10)
    c.drawCentredString(left_x, sig_y - 15 * scale, "Class Adviser")
    c.drawCentredString(right_x, sig_y - 15 * scale, data.school_head_position)


def generate_award_certificate(**fields) -> bytes:
    """One certificate on a full landscape Letter page."""
    data = CertificateData(**fields)
    buffer = io.BytesIO()
    width, height = landscape(letter)
    c = canvas.Canvas(buffer, pagesize=(width, height))
    _draw_certificate(c, data, x=0, y=0, width=width, height=height)
    c.showPage()
    c.save()
    return buffer.getvalue()


def generate_award_certificates_2up(certificates: list[CertificateData]) -> bytes:
    """Two certificates per portrait Letter page, with a dashed cut line
    between them — halves the paper for the classroom-level Honors.

    An odd count leaves the bottom half of the last page blank rather
    than stretching one certificate to fill it.
    """
    buffer = io.BytesIO()
    width, height = letter  # portrait: each certificate gets a half
    c = canvas.Canvas(buffer, pagesize=(width, height))
    half = height / 2

    for index in range(0, len(certificates), 2):
        pair = certificates[index : index + 2]
        # Top half first, so reading order matches the list order.
        _draw_certificate(c, pair[0], x=0, y=half, width=width, height=half)
        if len(pair) == 2:
            _draw_certificate(c, pair[1], x=0, y=0, width=width, height=half)

        c.setStrokeColor(_GRAY)
        c.setLineWidth(0.5)
        c.setDash(3, 3)
        c.line(0.25 * inch, half, width - 0.25 * inch, half)
        c.setDash()
        c.showPage()

    c.save()
    return buffer.getvalue()
