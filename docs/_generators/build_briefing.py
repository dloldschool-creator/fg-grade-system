# Regenerates the two documents in docs/ and the system map they use.
#
# Not part of the application: needs `python-docx` and `Pillow`, neither of
# which is in requirements.txt, and nothing in app/ imports any of this.
# The published .docx files are edited in Word from here on; re-run these
# only to rebuild from scratch or to redraw the map.
#
#     python docs/_generators/build_system_map.py
#     python docs/_generators/build_briefing.py
#     python docs/_generators/build_system_doc.py

"""One-page presenter's guide for the faculty meeting."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docx import Document
from docx.shared import Inches, Pt, RGBColor

from docx_helpers import (
    ALIGN_CENTER, ALIGN_LEFT, AMBER, BAND_FILL, GREY, HDR_FILL, INK, NAVY,
    SOFT_FILL, TEAL, WARN_FILL, base_style, bottom_rule, cell_margins,
    cell_text, keep_together, make_table, no_borders, para, rich, set_page,
    shade, table_borders,
)

import os

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")) + "/"
OUT = REPO + "docs/FGNMHS Grading System - Faculty Presentation Guide.docx"

W = 7.5  # content width, Letter with 0.5in margins

doc = Document()
set_page(doc.sections[0], 8.5, 11, (0.45, 0.5, 0.4, 0.5))
base_style(doc, size=9.0)

# ------------------------------------------------------------------ title
p = para(doc, "FGNMHS SENIOR HIGH SCHOOL", size=8, bold=True, colour=GREY,
         space_after=0)
p = rich(doc, [("Grading, Attendance, Forms & Awards System", True, NAVY)],
         size=16, space_after=1)
p = para(doc,
         "How to deliver it to the faculty  ·  presenter's copy  ·  faculty meeting with the School Head",
         size=9, colour=GREY, space_after=5)
bottom_rule(p, colour="0B3E7A", size=10, space=3)

# ------------------------------------------------------------ the message
t = make_table(doc, 1, 1, [W])
no_borders(t)
cell_margins(t, top=80, bottom=80, left=110, right=110)
c = t.rows[0].cells[0]
shade(c, BAND_FILL)
c.text = ""
pp = c.paragraphs[0]
r = pp.add_run("SAY THIS FIRST, AND SAY IT AGAIN AT THE END   ")
r.bold = True
r.font.size = Pt(9.0)
r.font.color.rgb = NAVY
r2 = pp.add_run(
    "\u201cYour class record does not change. What changes is where the final number goes after you "
    "have worked it out \u2014 and everything after that: report cards, SF2, SF4, honours.\u201d")
r2.font.size = Pt(10.0)
r2.italic = True
pp.paragraph_format.space_after = Pt(0)
pp.paragraph_format.line_spacing = 1.05
para(doc, "", size=3, space_after=0)


def section_title(text, extra=None):
    pr = doc.add_paragraph()
    r = pr.add_run(text.upper())
    r.bold = True
    r.font.size = Pt(9.0)
    r.font.color.rgb = NAVY
    if extra:
        re_ = pr.add_run("   " + extra)
        re_.font.size = Pt(9.0)
        re_.font.color.rgb = GREY
    pr.paragraph_format.space_before = Pt(7.5)
    pr.paragraph_format.space_after = Pt(2.5)
    keep_together(pr)
    return pr


def tick(text, lead=None):
    pr = doc.add_paragraph()
    r0 = pr.add_run("\u25AA  ")
    r0.font.size = Pt(9.0)
    r0.font.color.rgb = NAVY
    if lead:
        rb = pr.add_run(lead)
        rb.bold = True
        rb.font.size = Pt(9.0)
    r = pr.add_run(text)
    r.font.size = Pt(9.0)
    pf = pr.paragraph_format
    pf.left_indent = Inches(0.17)
    pf.first_line_indent = Inches(-0.17)
    pf.space_after = Pt(1.5)
    pf.line_spacing = 1.02
    return pr


# --------------------------------------------------------- before the meeting
section_title("Before the meeting", "\u2014 fifteen minutes, done in the room you will present in")
tick("and leave it on the Dashboard. Check the footer line: it tells you whether the version on "
     "screen is really the current one.",
     lead="Sign in on the projector ")
tick("A printed SF9 and a printed term card, passed around, convince faster than a screen does.",
     lead="Put paper in their hands.  ")
tick("so you answer a raised hand with a name and a date, not with \u201cwe'll see\u201d.",
     lead="Check Users & Roles for who still has no account, ")
tick("The laptop is on cable; the faculty will be on the same Wi-Fi the room actually has.",
     lead="Test the connection from a phone. ")
tick("Adoption follows authority, not features.",
     lead="Agree who opens and who closes \u2014 the School Head does both. ")

# ------------------------------------------------------------ running order
section_title("Running order", "\u2014 thirty minutes")
rows = [
    ("3 min", "School Head",
     "Opens. From Term 1 this is the school's record of grades and attendance. Names the deadline out loud."),
    ("2 min", "You",
     "What does NOT change: your own class record, your own computation, your professional judgement."),
    ("8 min", "You",
     "Live demo: sign in \u2192 Gradebook \u2192 encode three grades \u2192 Save \u2192 Submit. Leave one cell blank on purpose and show that it stays blank."),
    ("5 min", "You",
     "Live, adviser's view: prepare the month's attendance sheet, print one term card, print a whole section's SF9 in one file."),
    ("4 min", "You",
     "The three safeguards, on screen: a blank is not a zero; the Audit Log naming who changed what; a finalized record refusing to be edited."),
    ("5 min", "Floor",
     "Questions. Use the answers below \u2014 short, then stop talking."),
    ("3 min", "School Head",
     "Closes with the three asks, the date they are due, and who to go to when something is wrong."),
]
t = make_table(doc, len(rows), 3, [0.5, 0.85, W - 1.35])
table_borders(t, size=4)
cell_margins(t, top=22, bottom=22, left=60, right=60)
for j, (a, b, c_) in enumerate(rows):
    cells = t.rows[j].cells
    if j % 2 == 1:
        for cc in cells:
            shade(cc, SOFT_FILL)
    cell_text(cells[0], a, size=8.9, bold=True, colour=NAVY)
    cell_text(cells[1], b, size=9.0, bold=True)
    cell_text(cells[2], c_, size=9.0)

# ------------------------------------------------------------- two columns
section_title("Promise these three", "\u2014 and refuse these three")
t = make_table(doc, 1, 2, [W / 2 - 0.06, W / 2 - 0.06])
no_borders(t)
cell_margins(t, top=60, bottom=60, left=100, right=100)
left, right = t.rows[0].cells
shade(left, "EAF4EC")
shade(right, WARN_FILL)


def fill(cell, title, items, colour):
    cell.text = ""
    p0 = cell.paragraphs[0]
    r = p0.add_run(title)
    r.bold = True
    r.font.size = Pt(9.0)
    r.font.color.rgb = colour
    p0.paragraph_format.space_after = Pt(2)
    p0.paragraph_format.line_spacing = 1.0
    for lead, rest in items:
        pi = cell.add_paragraph()
        r0 = pi.add_run("\u25AA  ")
        r0.font.size = Pt(9.0)
        r0.font.color.rgb = colour
        rb = pi.add_run(lead)
        rb.bold = True
        rb.font.size = Pt(9.0)
        rr = pi.add_run(rest)
        rr.font.size = Pt(9.0)
        pf = pi.paragraph_format
        pf.left_indent = Inches(0.17)
        pf.first_line_indent = Inches(-0.17)
        pf.space_after = Pt(1.5)
        pf.line_spacing = 1.02


fill(left, "SAY PLAINLY \u2014 all three are true", [
    ("One number per learner, per subject, per term. ",
     "The system does not want your Written Work / Performance Task / Exam breakdown."),
    ("Nobody overwrites anybody. ",
     "Two teachers can encode the same class at the same time and neither entry is lost."),
    ("A mistake can be fixed. ",
     "The correction is recorded with your name and the reason \u2014 that protects you as much as it protects the record."),
], TEAL)

fill(right, "DO NOT PROMISE \u2014 all three will be tested", [
    ("It does not replace your class record. ", "Keep it. It is how you show your work."),
    ("It does not work offline. ", "No internet, no encoding \u2014 encode later, nothing saved is lost."),
    ("It is not finished. ",
     "The separate checking step and Grade 12's Term 2 and Term 3 subject lists are still being completed."),
], AMBER)

# --------------------------------------------------------------------- Q&A
section_title("Questions you will be asked", "\u2014 answer in one breath, then stop")
qa = [
    ("Will it compute my grades for me?",
     "No. You compute them exactly as you do now. You encode the one official number."),
    ("What if I type the wrong grade?",
     "Edit it. It drops back to DRAFT, you press Submit again, and the change is logged."),
    ("Who else can see my grades?",
     "That section's adviser, the Registrar, the School Head and the ICT Administrator. Not other subject teachers."),
    ("A learner has no grade yet \u2014 do I put 0?",
     "Never. Leave it blank. Blank means \u201cnot yet\u201d; 0 is a real grade and it can cost a learner an award."),
    ("I got signed out / the internet dropped.",
     "Nothing saved is lost. You are signed out after 60 idle minutes \u2014 press Save before you step away."),
    ("My class is not in my Gradebook.",
     "Nobody has assigned you to it yet. Your section's adviser can do it in a minute on Teacher Assignments."),
    ("Do I still keep my class record?",
     "Yes. The system holds the official number; your record shows how you arrived at it."),
    ("Can I use my phone?",
     "Yes, it is the same app \u2014 but encode a whole class on a PC."),
]
t = make_table(doc, len(qa), 2, [2.3, W - 2.3])
table_borders(t, size=4)
cell_margins(t, top=20, bottom=20, left=60, right=60)
for j, (q, a) in enumerate(qa):
    cells = t.rows[j].cells
    if j % 2 == 1:
        for cc in cells:
            shade(cc, SOFT_FILL)
    cell_text(cells[0], q, size=8.9, bold=True, colour=NAVY)
    cell_text(cells[1], a, size=9.0)

# ------------------------------------------------------------------- close
section_title("Close by asking for exactly three things")
t = make_table(doc, 1, 3, [W / 3 - 0.05, W / 3 - 0.05, W / 3 - 0.05])
no_borders(t)
cell_margins(t, top=60, bottom=60, left=100, right=100)
asks = [
    ("1  Sign in today.",
     "Set your own password. The one handed to you has been read by someone else, so it is not yours until you replace it."),
    ("2  Report a missing class.",
     "Tell your section's adviser the same day \u2014 they can fix it themselves, without waiting for the ICT Coordinator."),
    ("3  Encode Term 1.",
     "Term 1 closes on 15 September 2026. After it closes the term is read-only for everyone."),
]
for i, (t_, b_) in enumerate(asks):
    cc = t.rows[0].cells[i]
    shade(cc, BAND_FILL)
    cc.text = ""
    p0 = cc.paragraphs[0]
    r = p0.add_run(t_)
    r.bold = True
    r.font.size = Pt(9.6)
    r.font.color.rgb = NAVY
    p0.paragraph_format.space_after = Pt(1)
    p0.paragraph_format.line_spacing = 1.0
    p1 = cc.add_paragraph()
    r1 = p1.add_run(b_)
    r1.font.size = Pt(9.0)
    p1.paragraph_format.space_after = Pt(0)
    p1.paragraph_format.line_spacing = 1.02

pf = para(doc,
          "fgnmhs-shs.streamlit.app  ·  in-app help: Quick Guide in the sidebar  ·  "
          "prepared by the ICT Coordinator, 21 August 2026",
          size=8.0, colour=GREY, space_before=6, space_after=0)

doc.save(OUT)
print("wrote", OUT)
