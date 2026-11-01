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

"""System documentation for submission to the Schools Division Office."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK
from docx.shared import Inches, Pt, RGBColor

from docx_helpers import (
    ALIGN_CENTER, AMBER, BAND_FILL, GREY, HDR_FILL, INK, NAVY, RULE, SOFT_FILL,
    TEAL, WARN_FILL, add_page_number_footer, base_style, bottom_rule, bullet,
    callout, cell_margins, cell_text, data_table, heading, keep_together,
    make_table, no_borders, numbered, para, rich, set_page, shade,
    subheading, table_borders,
)

import os

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")) + "/"
ROOT = REPO
OUT = ROOT + "docs/FGNMHS Grading System - System Documentation.docx"
SEAL = ROOT + "app/assets/fgnmhs_seal.png"
MAP = ROOT + "docs/assets/system-map.png"

W = 6.5

doc = Document()
set_page(doc.sections[0], 8.5, 11, (0.9, 1.0, 0.85, 1.0))
base_style(doc, size=10)
add_page_number_footer(
    doc.sections[0],
    "FGNMHS SHS Grading, Attendance, Forms & Awards System  \u00b7  System Documentation  \u00b7  21 August 2026")


def spacer(pts=6):
    para(doc, "", size=pts, space_after=0)


def page_break():
    p = doc.add_paragraph()
    p.add_run().add_break(WD_BREAK.PAGE)
    p.paragraph_format.space_after = Pt(0)


# ============================================================ COVER
p = doc.add_paragraph()
p.alignment = ALIGN_CENTER
p.add_run().add_picture(SEAL, width=Inches(1.15))
p.paragraph_format.space_after = Pt(6)

para(doc, "Republic of the Philippines  \u00b7  Department of Education", size=9.5,
     colour=GREY, align=ALIGN_CENTER, space_after=1)
para(doc, "Region III  \u00b7  Schools Division of Angeles City  \u00b7  1st District", size=9.5,
     colour=GREY, align=ALIGN_CENTER, space_after=1)
para(doc, "FRANCISCO G. NEPOMUCENO MEMORIAL HIGH SCHOOL", size=11, bold=True,
     colour=INK, align=ALIGN_CENTER, space_after=1)
pcov = para(doc, "School ID 301040  \u00b7  Angeles City, Pampanga", size=9.5, colour=GREY,
            align=ALIGN_CENTER, space_after=10)
bottom_rule(pcov, colour="0B3E7A", size=8, space=6)

spacer(22)
para(doc, "SENIOR HIGH SCHOOL", size=11, bold=True, colour=GREY,
     align=ALIGN_CENTER, space_after=3)
para(doc, "Grading, Attendance, Forms", size=25, bold=True, colour=NAVY,
     align=ALIGN_CENTER, space_after=0)
para(doc, "and Awards System", size=25, bold=True, colour=NAVY,
     align=ALIGN_CENTER, space_after=8)
para(doc, "SYSTEM DOCUMENTATION", size=13, bold=True, colour=INK,
     align=ALIGN_CENTER, space_after=3)
para(doc,
     "Prepared for the Schools Division of Angeles City, for consideration for "
     "implementation in other schools",
     size=10.5, italic=True, colour=GREY, align=ALIGN_CENTER, space_after=2)

spacer(16)

t = make_table(doc, 1, 1, [4.9])
t.alignment = WD_TABLE_ALIGNMENT.CENTER
no_borders(t)
cell_margins(t, top=110, bottom=110, left=140, right=140)
c = t.rows[0].cells[0]
shade(c, BAND_FILL)
c.text = ""
facts = [
    ("In use since", "12 August 2026  \u00b7  School Year 2026\u20132027"),
    ("Covers", "Grades 11 and 12  \u00b7  Academic and TechPro tracks  \u00b7  three terms"),
    ("Curriculum", "Strengthened SHS Curriculum (DepEd Order 017 s. 2026)"),
    ("Cost to the school", "None \u2014 free hosting, no licences, no new hardware"),
    ("Document date", "21 August 2026"),
]
first = True
for k, v in facts:
    pp = c.paragraphs[0] if first else c.add_paragraph()
    first = False
    r = pp.add_run(k + "   ")
    r.bold = True
    r.font.size = Pt(9.5)
    r.font.color.rgb = NAVY
    r2 = pp.add_run(v)
    r2.font.size = Pt(9.5)
    pp.paragraph_format.space_after = Pt(2)
    pp.paragraph_format.line_spacing = 1.0

spacer(14)
para(doc,
     "Contents:  1 What it is  \u00b7  2 Why it was built  \u00b7  3 System map  \u00b7  "
     "4 Who uses it  \u00b7  5 How a grade travels  \u00b7  6 What it produces  \u00b7  "
     "7 How it follows DepEd policy  \u00b7  8 Safeguards  \u00b7  9 What a school needs  \u00b7  "
     "10 Adopting it elsewhere  \u00b7  11 What is not yet built  \u00b7  12 Where it stands today",
     size=9, colour=GREY, align=ALIGN_CENTER, space_after=0)

page_break()

# ============================================================ 1
heading(doc, "1", "What this system is")
para(doc,
     "It is a web application that a senior high school uses to record grades and attendance, "
     "and to print the DepEd forms that come out of them. Staff open a link in an ordinary "
     "browser \u2014 on a school PC or on their own phone \u2014 sign in, and do their own part of "
     "the work. The system does the arithmetic, keeps the record, and fills the school's own "
     "official SF9, SF2 and SF4 files. Nothing is installed on any school computer.",
     size=10)
para(doc,
     "It replaced an Excel automation workbook that only one person could use at a time. It has "
     "been in daily use at Francisco G. Nepomuceno Memorial High School since 12 August 2026.",
     size=10, space_after=7)

rows = [
    ("What it is",
     "A web application, opened in a browser. No installation, no server in the school, no new hardware."),
    ("Who it is for",
     "Senior High School, Grades 11 and 12, Academic and TechPro tracks, three terms per school year."),
    ("What it replaces",
     "An Excel automation workbook \u2014 one file, one computer, one person at a time."),
    ("What it produces",
     "SF9 report cards, SF2 daily attendance, SF4 monthly movement, term cards, award certificates, "
     "Excel and CSV exports, and a complete downloadable backup."),
    ("Rules it follows",
     "The Strengthened SHS Curriculum under DepEd Order 017 s. 2026 \u2014 including unit-weighted "
     "term and general averages \u2014 and DepEd Order 15 s. 2026 for the Academic Excellence Award."),
    ("Cost to the school",
     "None. It runs on the free tier of two hosting services. No licences, no subscriptions."),
    ("People needed to run it",
     "One staff member as system administrator. Everyone else only uses their own part of it."),
    ("Size",
     "29 screens; 46 tables of records; 483 automated checks that must pass before any change is released."),
]
data_table(doc, ["", "At a glance"], rows, [1.55, W - 1.55], size=9.5, head_size=9.5)

# ============================================================ 2
heading(doc, "2", "Why it was built")
para(doc,
     "The school kept its grades in an Excel workbook of considerable skill and long standing. "
     "Three things it could not do are what this system exists for.", size=10)
numbered(doc, 1,
         "Forty subject teachers cannot edit one file. Grades were collected on paper or by "
         "messenger and retyped by one person. Retyping is where most errors entered, and it is "
         "the slowest part of every deadline.",
         size=10, bold_lead="One person at a time.  ")
numbered(doc, 2,
         "A number in a spreadsheet cell carries no history. When a grade was questioned, nobody "
         "could show when it changed, who changed it, or why.",
         size=10, bold_lead="No record of who changed what.  ")
numbered(doc, 3,
         "DepEd Order 017 s. 2026 replaced the flat average with a unit-weighted one. In a "
         "workbook that means editing formulas in every copy of every sheet and hoping none was "
         "missed. On DepEd's own Grade 12 worked example the two methods differ by two whole "
         "points \u2014 87 against 89 \u2014 which is the distance between an award and no award.",
         size=10, bold_lead="The arithmetic moved.  ")
para(doc,
     "The school also runs three terms rather than two semesters, so the standard forms and "
     "formulas had to be re-derived in any case.", size=10, space_before=3)

# ============================================================ 3 (map)
heading(doc, "3", "System map")
para(doc,
     "The whole system on one page. Read it downwards: people sign in at the top, official forms "
     "come out at the bottom. Everything in between is either a screen someone uses, a rule that "
     "does arithmetic, or a place something is kept.", size=10, space_after=8)

pmap = doc.add_paragraph()
pmap.alignment = ALIGN_CENTER
pmap.add_run().add_picture(MAP, width=Inches(W))
pmap.paragraph_format.space_after = Pt(8)

para(doc,
     "Two things on the map are worth naming, because they are what keeps the record trustworthy. "
     "First, there is exactly one set of rules doing the arithmetic \u2014 the screen and the printed "
     "form read the same computed figures, so they cannot disagree with each other. Second, the "
     "official templates are the school's own Excel files: the system fills their cells and leaves "
     "their layout, logos and signature blocks untouched, so what prints is the DepEd form rather "
     "than a look-alike.", size=10)

# ============================================================ 4 (roles)
heading(doc, "4", "Who uses it")
para(doc,
     "Five kinds of user. Each person signs in with their own account and sees only the screens "
     "their work needs \u2014 the menu is shorter for a subject teacher than for an administrator, "
     "and every page checks the person's role again when it is opened, so a page cannot be "
     "reached by guessing its address.", size=10, space_after=7)

rows = [
    ("Subject Teacher",
     "Encodes one official grade per learner, per subject, per term, in the classes assigned to "
     "them. Saves as often as they like; submits when the class is final.",
     "Sees no other teacher's classes and no setup screen. Cannot alter a finalized grade."),
    ("Class Adviser",
     "Names the subject teachers for their own section; records attendance; reviews the class's "
     "grade summary; prints term cards and report cards; computes awards.",
     "Sees only learners in the sections they advise, plus learners they added who are not yet "
     "enrolled anywhere. Cannot delete a learner."),
    ("Registrar",
     "Adds and imports learners, enrols them, records transfers, drops and shifts, and imports "
     "grades from Excel where a section encodes offline first.",
     "Cannot change grading policy, award thresholds or user accounts."),
    ("School Head",
     "Sees the whole school \u2014 dashboard, grade summaries, every report \u2014 and prints anything.",
     "Changes nothing. The role is read-only by design."),
    ("ICT Administrator",
     "Sets up the school year, terms, calendar, sections, subjects, units, offerings, policies and "
     "accounts; opens and closes encoding; reads the audit log; takes backups.",
     "Cannot make a change disappear. Every sensitive action is logged under their name."),
]
data_table(doc, ["Role", "What they do", "What they cannot do"], rows,
           [1.05, 3.0, W - 4.05], size=9, head_size=9)

# ============================================================ 5
heading(doc, "5", "How a grade travels, from typing to a printed report card")
steps = [
    ("Before the year opens, the administrator records the school year, its three terms, the "
     "class-day calendar, the sections, and \u2014 most importantly \u2014 which subject each section "
     "actually takes in each term. That last list is the single source of truth: a learner is "
     "graded on what their own section is recorded as offering, and on nothing else."),
    ("Each adviser names the teacher for each subject of their section. That one act is what gives "
     "that teacher a gradebook for that class, so a missing class is fixed by the adviser in a "
     "minute rather than by a support request."),
    ("The administrator opens the term for encoding. A closed term is read-only school-wide, which "
     "is the safe state between encoding periods."),
    ("The subject teacher types one official grade per learner for that subject and term, and "
     "presses Submit when the class is final. A learner not yet graded is left blank. A blank never "
     "becomes a zero, anywhere."),
    ("The system computes immediately: each subject's final grade from the terms that subject "
     "actually ran, then the term average, then the general average \u2014 all unit-weighted, as "
     "DepEd Order 017 requires."),
    ("The adviser or registrar reviews the class on the Grade Summary screen. A learner missing any "
     "applicable grade reads INCOMPLETE and cannot be finalized until it is encoded. Finalizing "
     "locks the record; reopening it needs a reason and is recorded."),
    ("The forms print from those same stored figures \u2014 report card, term card, certificate. "
     "There is no second calculation anywhere in the system, which is why the screen and the paper "
     "always agree."),
]
for i, s in enumerate(steps, 1):
    numbered(doc, i, s, size=10, space_after=3)

spacer(3)
callout(doc, "The four states a grade passes through",
        "DRAFT \u2192 SUBMITTED \u2192 VERIFIED \u2192 FINALIZED.  Editing a submitted grade returns it to "
        "DRAFT, so it has to be submitted again \u2014 deliberately, because it makes the change "
        "visible. Three of the four states are in use today; the separate VERIFIED checking step "
        "exists in the database and in the locking rules but is not yet on any screen. See Section 11.")

# ============================================================ 6
heading(doc, "6", "What it produces")
rows = [
    ("SF9 \u2014 Report Card", "PDF and Excel",
     "One learner at a time, or a whole section in a single file. Warns first about any learner "
     "whose record is incomplete, because blank cells read to a parent as a lost grade."),
    ("SF2 \u2014 Daily Attendance", "PDF and Excel",
     "One per section per month, built from the recorded class days. A learner who leaves mid-month "
     "still appears that month with a remark, as the rule requires."),
    ("SF4 \u2014 Monthly Movement", "Excel",
     "School-wide, one row per track and strand within each grade level, plus totals. Submitted as "
     "a file rather than printed."),
    ("Term card", "PDF",
     "The school's own three-term card, one per learner per term, printable for a whole section."),
    ("Award certificates", "PDF",
     "Generated for each eligible learner under the award policy in force, with the reason recorded "
     "for each learner found not eligible."),
    ("Excel and CSV exports", ".xlsx / .csv",
     "Grades, attendance, enrolment and awards, for anything the official forms do not cover. "
     "Learner Reference Numbers are written as text so a 12-digit number is never damaged."),
    ("Full backup", "ZIP of CSV files",
     "A complete copy of the record the school holds itself, with a manifest describing how to "
     "restore it. Readable in Excel in twenty years, with or without this system."),
]
data_table(doc, ["Output", "Format", "Notes"], rows, [1.35, 0.95, W - 2.3], size=9, head_size=9)
para(doc,
     "SF10 is not yet included \u2014 it is waiting on the school's finalized template file, not on "
     "programming. See Section 11.", size=9, colour=GREY, space_before=3)

# ============================================================ 7
heading(doc, "7", "How it follows DepEd policy")
items = [
    ("Three terms, not two semesters.",
     " The number of terms is a record in the database, not a number written into the program. A "
     "school on two semesters, or a return to four quarters, is a change of configuration rather "
     "than a rewrite."),
    ("Unit-weighted averages (DO 017 s. 2026, Annex E).",
     " The term average and the general average are the sum of (grade \u00d7 units) divided by the "
     "sum of units, rounded half-up to a whole number. The units come from Table 19 of the order. "
     "A subject's annual units are its units per term multiplied by the number of terms it actually "
     "ran, so a one-term elective is never weighted like a three-term core subject."),
    ("The general average is not the average of the three term averages.",
     " It is built from each subject's final grade, and each subject's final grade respects the "
     "terms that that subject actually ran \u2014 some electives run one term, some two, some all three."),
    ("Grade 11's paired language subjects count once.",
     " Effective Communication and Mabisang Komunikasyon are encoded and stored separately, but "
     "DO 017 Table 1 makes them a single 160-hour learning area, so together they carry one core "
     "subject's weight. The report card prints them as one parent row carrying the grade, with the "
     "two components indented beneath it for information."),
    ("Awards follow a stored policy, not program code.",
     " The Academic Excellence Award (DO 15 s. 2026 \u2014 complete record, no derogatory record, "
     "general average of at least 90, no final grade below 80) is the policy in force. The school's "
     "older tiered honours scheme is kept alongside it as a separate, selectable policy version. "
     "Choosing between them is an administrator's recorded decision."),
    ("Rules are versioned by school year and by grade level.",
     " When the next order changes an average, it is added as a new policy version effective from a "
     "stated year. The finished year keeps the rules it was graded under; nothing is ever "
     "recalculated behind anyone's back."),
    ("The printed form is the DepEd form.",
     " The system fills the school's own official Excel templates cell by cell and leaves their "
     "layout, merges, fonts, logos and signature blocks exactly as they are."),
]
for lead, rest in items:
    bullet(doc, rest, size=10, bold_lead=lead, space_after=3)

# ============================================================ 8
heading(doc, "8", "The safeguards built into it")
para(doc,
     "Each row below is a way a school record can go quietly wrong. Each was closed on purpose, and "
     "most are checked automatically before any change to the system is released.",
     size=10, space_after=7)
rows = [
    ("A missing grade must never read as a zero",
     "Blank means \u201cnot yet encoded\u201d \u2014 on screen, in every average and on every printed form. "
     "Nothing is defaulted to 0. A learner missing any applicable grade reads INCOMPLETE and cannot "
     "be finalized until it is entered."),
    ("Grades must be reproducible",
     "Every official figure comes from fixed, written-down arithmetic. No artificial intelligence is "
     "involved in computing any grade, at any point. That arithmetic is covered by 483 automated "
     "checks, including DepEd's own worked examples from Annex E of DO 017."),
    ("Someone must be answerable for every change",
     "Changing a grade or an attendance day, reopening a finalized record, overriding an award, "
     "assigning a teacher or moving a learner records who did it, the value before, the value after, "
     "the time, and \u2014 where required \u2014 the reason."),
    ("Two people may work at once",
     "Every editable record carries a version number. If two people change the same row, the second "
     "is told, instead of silently replacing the first person's work."),
    ("Each person sees only what their job needs",
     "A subject teacher sees their assigned classes; an adviser sees the sections they advise; the "
     "School Head sees everything and changes nothing. The check runs on the page itself, not only "
     "in the menu."),
    ("A finished year must stay finished",
     "Policy, weight and threshold changes apply to new school years, or through an explicit new "
     "policy version. There is no blanket recalculation of a year already closed."),
    ("Identifiers must survive being handled",
     "The 12-digit LRN is stored and exported as text. An uploaded CSV in which Excel has already "
     "shortened an LRN is refused rather than guessed at \u2014 two different learners can round to the "
     "same number, so guessing would manufacture duplicates that do not exist."),
    ("Records must not quietly vanish",
     "Anything still in use cannot be deleted. A learner who leaves is recorded as a movement with an "
     "effective date, which is what makes the monthly forms report them correctly; they are never "
     "removed."),
    ("The data must be recoverable",
     "The hosting service takes its own automatic backups, and in addition the school can download a "
     "complete copy at any time as plain CSV files in a ZIP, readable without this system."),
]
data_table(doc, ["What could go wrong", "How it is prevented"], rows, [1.95, W - 1.95],
           size=9, head_size=9)

# ============================================================ 9
heading(doc, "9", "What a school needs to run it")
rows = [
    ("Internet", "A working connection wherever staff encode. Any browser, on a PC or a phone."),
    ("Hardware", "Nothing beyond what the school already has. No server, no installation, no "
                 "software to keep updated on each machine."),
    ("Licences", "None. The system is written in Python and uses freely available components."),
    ("Hosting", "Two free accounts \u2014 one to run the application, one for the database. The measured "
                "footprint is about 176 MB with staff signed in, plus roughly 1 MB per additional "
                "signed-in user, so a school of this size fits inside the free allowance."),
    ("People", "One staff member as system administrator \u2014 roughly a day a week during setup, much "
               "less afterwards. Teachers and advisers need no training beyond a short briefing and "
               "the Quick Guide built into the app."),
    ("The school's own forms", "The school's SF9, SF2 and SF4 Excel print-views. The system reads the "
                               "layout out of the file it is given, so a school whose forms differ "
                               "needs its own templates, not programming."),
]
data_table(doc, ["Item", "Requirement"], rows, [1.35, W - 1.35], size=9.5, head_size=9.5)

# ============================================================ 10
heading(doc, "10", "How another school would adopt it")
rows = [
    ("1", "Create the two free hosting accounts and connect them to each other.", "Half a day"),
    ("2", "Deploy the application: copy the code, set nine configuration values, start it.", "Half a day"),
    ("3", "Record the school, the school year, its terms, and the class-day calendar.", "Half a day"),
    ("4", "Record tracks, strands, grade levels, sections, subjects and their units.", "1\u20132 days"),
    ("5", "Build one subject profile per grade level and track or strand, then seed each section's "
          "subject offerings from it. This step decides what every learner is graded on, so it is "
          "worth doing slowly.", "1\u20132 days"),
    ("6", "Import staff from one Excel file, and learners from one file per section.", "2\u20133 days"),
    ("7", "Advisers name their own subject teachers for their own sections.", "1 hour each"),
    ("8", "Dress rehearsal: take one section all the way through \u2014 enrol, encode all three terms, "
          "finalize, print every form \u2014 using throwaway records, then purge them.", "1 day"),
]
data_table(doc, ["", "Step", "Rough effort"], rows, [0.3, W - 1.35, 1.05], size=9.5, head_size=9.5)
para(doc,
     "For a school of about 1,200 learners that is roughly two to three weeks of part-time work by "
     "one coordinator, most of it data entry rather than anything technical.",
     size=10, space_before=5)
spacer(2)
callout(doc, "Why it transfers",
        "Nothing about this particular school is written into the program. The subjects, their units, "
        "the sections, the number of terms, the award thresholds and the layout of every printed form "
        "are all data or template files. A different school with different strands, a different "
        "subject list and its own forms is a matter of what is entered, not of what is programmed.",
        fill="EAF4EC", title_colour=TEAL)

# ============================================================ 11
heading(doc, "11", "What is not yet built")
para(doc,
     "Listed plainly, because a division office deciding whether to replicate this needs the gaps as "
     "much as the features.", size=10)
gaps = [
    ("The separate checking step.",
     " Grades are meant to move DRAFT \u2192 SUBMITTED \u2192 VERIFIED \u2192 FINALIZED. The database and the "
     "locking rules already support VERIFIED, but no screen assigns it yet, so grades currently move "
     "DRAFT \u2192 SUBMITTED \u2192 FINALIZED. Who verifies, on which screen, and at what level of detail is "
     "a school policy decision rather than a programming one."),
    ("SF10.",
     " Waiting on the school's finalized template file. The layer that fills a template already "
     "exists and reads the layout out of the file, so no new layout programming is expected. The "
     "output will be labelled TEMPORARY THREE-TERM SF10 \u2014 FOR SCHOOL USE ONLY."),
    ("Grade 12's Term 2 and Term 3 subject offerings.",
     " A data-entry task at this school rather than a defect: an annual general average cannot be "
     "computed for a section whose subjects are all recorded in Term 1."),
    ("No offline mode.",
     " Encoding needs a connection. Nothing already saved is lost when the connection drops, but "
     "work cannot continue without it."),
    ("No parent or learner sign-in.",
     " Results reach families the way they always have \u2014 on the printed card."),
    ("One rule of DO 017 is not yet enforced.",
     " A learner who enrols in more electives than the required minimum must pass all of them to "
     "graduate. The system does not yet check that when finalizing."),
]
for lead, rest in gaps:
    bullet(doc, rest, size=10, bold_lead=lead, space_after=3)

# ============================================================ 12
heading(doc, "12", "Where it stands today")
para(doc, "As of 21 August 2026.", size=9, colour=GREY, space_after=4)
facts = [
    "Live at fgnmhs-shs.streamlit.app since 12 August 2026, in use for School Year 2026\u20132027.",
    "43 staff accounts; 362 active teaching assignments across 369 recorded subject offerings.",
    "Every Grade 11 subject offering \u2014 all 16 sections \u2014 has a named teacher. Grade 12's are all "
    "but complete.",
    "The DO 017 unit system was switched on on 20 August 2026, before any grade had been encoded, so "
    "there is no split between learners graded under the old rule and the new one.",
    "Term 1 closes on 15 September 2026.",
    "Still to do before full use: migrating the balance of about 1,200 learners, and completing "
    "Grade 12's Term 2 and Term 3 subject offerings.",
]
for f in facts:
    bullet(doc, f, size=10, space_after=2)

subheading(doc, "Documentation the school holds", size=10.5)
for f in [
    "A master specification of the rules, in 123 numbered sections.",
    "The database design, field by field.",
    "A build log recording the reasoning behind every design decision, including the mistakes.",
    "Written deployment and day-to-day operating procedures.",
    "483 automated checks that must pass before any change is released.",
    "A Quick Guide inside the app itself, written separately for each role.",
]:
    bullet(doc, f, size=10, space_after=2)

# ------------------------------------------------------------- signatures
spacer(20)
t = make_table(doc, 1, 2, [W / 2 - 0.15, W / 2 - 0.15])
no_borders(t)
cell_margins(t, top=0, bottom=0, left=0, right=140)
left, right = t.rows[0].cells
for cell, label, name, title in (
        (left, "Prepared by:", "________________________________", "ICT Coordinator"),
        (right, "Noted:", "HERMES P. VARGAS", "Principal IV")):
    cell.text = ""
    p0 = cell.paragraphs[0]
    r = p0.add_run(label)
    r.font.size = Pt(9.5)
    r.font.color.rgb = GREY
    p0.paragraph_format.space_after = Pt(14)
    p1 = cell.add_paragraph()
    r1 = p1.add_run(name)
    r1.bold = True
    r1.font.size = Pt(10.5)
    p1.paragraph_format.space_after = Pt(0)
    p2 = cell.add_paragraph()
    r2 = p2.add_run(title)
    r2.font.size = Pt(9.5)
    r2.font.color.rgb = GREY
    p2.paragraph_format.space_after = Pt(0)

doc.save(OUT)
print("wrote", OUT)
