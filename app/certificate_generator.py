"""Award certificate PDF (§40) — direct ReportLab generation, matching
the layout the user supplied as a reference image. This is a new custom
design with no pre-existing official DepEd template to preserve pixel-for-
pixel (unlike SF9/SF2), so it's generated directly rather than through the
openpyxl-fill-then-LibreOffice-convert pipeline used for those forms.
"""

import io
import os
from datetime import date
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

SEAL_PATH = os.path.join(os.path.dirname(__file__), "assets", "fgnmhs_seal.png")

_BLUE = colors.HexColor("#1B4F9C")
_GRAY = colors.HexColor("#555555")

# Total height of the title-through-date block (the sum of the y-steps
# between its five lines). Used to centre that block vertically rather
# than letting it hang off the bottom of the header — keep it in sync if
# those steps change.
_BODY_BLOCK_HEIGHT = 120


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def generate_award_certificate(
    *,
    school_name: str,
    schools_division: str,
    learner_name: str,
    award_name: str,
    general_average: Decimal,
    recognition_date: date,
    recognition_venue: str,
    school_year_name: str,
    adviser_name: str,
    school_head_name: str,
    school_head_position: str,
    term_name: str | None = None,
) -> bytes:
    """`term_name` is set for a TERM-scoped award (the tiered Honors,
    judged on one term's Term Average) and None for an ANNUAL one (the
    Academic Excellence Award, judged on the year's General Average). It
    only changes the wording of the citation line — a term certificate
    must not claim a General Average the learner hasn't earned yet."""
    buffer = io.BytesIO()
    page_size = landscape(letter)
    width, height = page_size
    c = canvas.Canvas(buffer, pagesize=page_size)

    # Border
    margin = 0.35 * inch
    c.setStrokeColor(colors.black)
    c.setLineWidth(1.2)
    c.rect(margin, margin, width - 2 * margin, height - 2 * margin)

    center_x = width / 2

    # Seal
    if os.path.exists(SEAL_PATH):
        seal_size = 0.95 * inch
        c.drawImage(
            SEAL_PATH,
            center_x - seal_size / 2,
            height - 1.55 * inch,
            width=seal_size,
            height=seal_size,
            preserveAspectRatio=True,
            mask="auto",
        )

    y = height - 1.75 * inch
    c.setFont("Times-Bold", 12)
    c.setFillColor(colors.black)
    c.drawCentredString(center_x, y, "Republic of the Philippines")
    y -= 15
    c.setFont("Times-Bold", 13)
    c.drawCentredString(center_x, y, "DEPARTMENT OF EDUCATION")
    y -= 18
    c.setFont("Times-Bold", 15)
    c.setFillColor(_BLUE)
    c.drawCentredString(center_x, y, school_name.upper())
    y -= 14
    c.setFont("Times-Italic", 9)
    c.setFillColor(_GRAY)
    c.drawCentredString(center_x, y, schools_division)

    # The title down to the "Given this..." line reads as one block, so
    # position it off the page's vertical centre rather than off the
    # header — otherwise it sits high and leaves a dead gap above the
    # signatures.
    y = height / 2 + _BODY_BLOCK_HEIGHT / 2
    c.setFont("Times-Bold", 26)
    c.setFillColor(_BLUE)
    c.drawCentredString(center_x, y, "CERTIFICATE OF RECOGNITION")

    y -= 30
    c.setFont("Times-Italic", 11)
    c.setFillColor(_GRAY)
    c.drawCentredString(center_x, y, "is proudly presented to")

    y -= 34
    c.setFont("Times-BoldItalic", 24)
    c.setFillColor(colors.black)
    c.drawCentredString(center_x, y, learner_name.upper())

    y -= 32
    c.setFont("Times-Roman", 12)
    c.setFillColor(colors.black)
    ga_display = int(general_average) if general_average is not None else "—"
    if term_name:
        citation = (
            f"for earning {award_name} with a {term_name} Average of {ga_display}."
        )
    else:
        citation = f"for earning {award_name} with a General Average of {ga_display}."
    c.drawCentredString(center_x, y, citation)

    y -= 24
    c.setFont("Times-Roman", 10)
    given_text = (
        f"Given this {_ordinal(recognition_date.day)} of {recognition_date.strftime('%B %Y')} "
        f"at {recognition_venue}, during School Year {school_year_name}."
    )
    c.drawCentredString(center_x, y, given_text)

    # Signature block
    sig_y = margin + 0.9 * inch
    left_x = width * 0.28
    right_x = width * 0.72
    c.setFont("Times-Bold", 11)
    c.drawCentredString(left_x, sig_y, adviser_name.upper())
    c.drawCentredString(right_x, sig_y, school_head_name.upper())
    c.setFont("Times-Roman", 10)
    c.drawCentredString(left_x, sig_y - 15, "Class Adviser")
    c.drawCentredString(right_x, sig_y - 15, school_head_position)

    c.showPage()
    c.save()
    return buffer.getvalue()
