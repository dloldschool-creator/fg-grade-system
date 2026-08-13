"""In-app quick guide.

Deliberately not a feature tour. Every item here is something that has a
**non-obvious rule behind it** — a place where doing the natural thing
produces a wrong record rather than an error message. Anything the screen
already makes obvious is left out; the point is to be short enough to be
read.

Content is data rather than markup so the sections a user actually needs
can be shown first, and so adding a rule later doesn't mean rewriting a
page.
"""

import streamlit as st

from app.admin_pages._helpers import render_flashes
from app.auth import get_current_user

# The three that apply to everyone, and cause the most damage when
# misunderstood — each one is a rule where "the obvious thing" is wrong.
UNIVERSAL = [
    (
        "The sidebar is grouped by what you're doing",
        "**Overview** and **Grades & Attendance** are the day-to-day work. "
        "**Forms & Reports** is where every printable document lives. "
        "**Setup** is in dependency order — you cannot make a Section without "
        "an Academic Structure, or a Section Offering without a Section — so "
        "when setting up a new school year, work straight down that list.\n\n"
        "You only see the pages your role allows, so your sidebar will be "
        "shorter than someone else's.",
    ),
    (
        "A blank is not a zero",
        "Anywhere a grade or an attendance day is blank, it means **nobody has "
        "encoded it yet** — not that the learner scored nothing. Never type 0 to "
        "mean 'not yet'. A 0 is a real grade and it will pull down averages, "
        "print on a report card, and cost a learner an award.",
    ),
    (
        "You are signed out after 60 minutes idle",
        "Unsaved typing is lost, so press **Save** before stepping away. "
        "Refreshing the browser or opening the app in a new tab also signs you "
        "out, so sign in again when that happens.",
    ),
    (
        "Sensitive changes are recorded",
        "Changing a grade, altering attendance, reopening a finalized record, "
        "overriding an award or moving a learner is logged with your name, the "
        "old value, the new value and the time. That's normal and expected — "
        "it protects you as much as the record.",
    ),
]

BY_ROLE = {
    "SUBJECT_TEACHER": (
        "Subject Teachers — Gradebook",
        [
            (
                "You type one final grade per learner, per subject, per term",
                "The system does **not** compute from Written Work / Performance "
                "Task / Exam. Work those out in your own class record as you "
                "always have, then encode the single official term grade here.",
            ),
            (
                "Save and Submit are different",
                "**Save** keeps your work and leaves grades editable. **Submit** "
                "hands them on for checking. Save often; Submit when the term's "
                "grades are final.",
            ),
            (
                "Editing a submitted grade sends it back to DRAFT",
                "That's intentional — it makes clear the grade needs submitting "
                "again. If you change something after submitting, remember to "
                "press Submit a second time.",
            ),
            (
                "A deadline warning doesn't stop you",
                "If the Gradebook says you're past the submission deadline, you "
                "can still save and submit — it's telling you, not blocking you. "
                "What *does* stop you is encoding being closed, which is a "
                "separate setting and says so plainly.",
            ),
            (
                "If you can't type anything, encoding is closed",
                "Grade encoding is opened and closed per term by the Super "
                "Admin. A closed term is read-only for everyone. Ask for it to "
                "be opened rather than working around it.",
            ),
            (
                "You only see your own assigned classes",
                "If a class is missing, your teacher assignment hasn't been made "
                "yet — that's a Super Admin task.",
            ),
        ],
    ),
    "ADVISER": (
        "Advisers — Attendance, cards and awards",
        [
            (
                "Prepare the month's sheet before encoding attendance",
                "Press **Prepare / refresh this month's sheet** first. It marks "
                "everyone present for every class day, so you only change the "
                "exceptions. Press it again whenever a new learner joins or the "
                "calendar changes, or their days will be missing.",
            ),
            (
                "Late and Cutting still count as present",
                "The learner was in school, so they count as present and are "
                "tallied separately. Use **X** only for a genuine absence.",
            ),
            (
                "A learner who leaves mid-month still appears that month",
                "They show on that month's SF2 with a remark and drop off the "
                "next one. That's the DepEd rule, not an oversight.",
            ),
            (
                "Finalizing a month locks it",
                "Fix everything shown in red first. After finalizing, only a "
                "Super Admin can reopen it, and they must give a reason.",
            ),
            (
                "Recompute after grades change, before you print",
                "Grade Summary's figures — Final Grades, the General Average, "
                "whether a record counts as COMPLETE — are stored, not worked "
                "out fresh each time you look. They refresh automatically when "
                "a teacher saves, but if something looks stale after an "
                "unusual change, press **Recompute**. It only re-reads what is "
                "already encoded; it never invents a grade.",
            ),
            (
                "Printing a whole class",
                "The SF9 page has **Print the whole section** — one PDF, one "
                "card per learner. It warns you about anyone whose record isn't "
                "complete: their card prints with blank cells, which a parent "
                "will read as a missing grade.",
            ),
        ],
    ),
    "REGISTRAR": (
        "Registrar — Learners and enrollment",
        [
            (
                "Upload as Excel (.xlsx), not CSV — this is the one that bites",
                "The LRN is 12 digits and is stored as text so leading zeros "
                "survive. You don't need to format the column as text first: from "
                "a **.xlsx** file the number comes through whole however Excel is "
                "displaying it.\n\n"
                "**Saving as CSV is what breaks it.** Excel writes the number the "
                "way it looks on screen, so `107041140016` becomes `1.07041E+11` "
                "and the last six digits are thrown away — not hidden, gone. The "
                "rows are refused rather than guessed at, because two different "
                "learners can round to the same value and the system would "
                "otherwise report a duplicate that isn't real and store an LRN "
                "nobody has.\n\n"
                "Fix: **Save As → Excel Workbook (.xlsx)** and upload that.",
            ),
            (
                "Names are saved in capitals",
                "Type them however you like; the system stores and prints them "
                "uppercase so every form matches.",
            ),
            (
                "Log a movement rather than editing the status",
                "Transferring, dropping or shifting a learner should be recorded "
                "as a movement with its effective date. That updates their "
                "status *and* makes the attendance and SF2 rules work — editing "
                "the status alone doesn't.",
            ),
            (
                "Fill in the Section column and the import enrolls them too",
                "On the learner template the **Section** column is optional. "
                "Fill it in and each learner is created *and* placed in that "
                "section in one step. Leave it blank and only the record is "
                "created — you then enroll them on the **Enrollment** page, "
                "which can do a whole batch at once.\n\n"
                "It matters which you did: a learner who exists but isn't "
                "enrolled appears in the masterlist and nowhere else — no "
                "gradebook, no SF2, no report card.",
            ),
            (
                "Column headings are forgiving; the values inside them aren't",
                "`Last Name`, `last_name` and `LASTNAME` are all understood, and "
                "extra columns are ignored — so you can upload a sheet you "
                "already keep without renaming its headings. Dates are read in "
                "whatever order your Excel writes them.\n\n"
                "What still has to be right is the *content*: section and subject "
                "names, and a 12-digit LRN. Anything unrecognised is listed as a "
                "row to fix — nothing is saved until you press the button, and a "
                "bad row never stops the good ones being shown.",
            ),
            (
                "Section and subject names must match exactly on an import",
                "The import file is checked against what is already in the "
                "system, and a near-miss is rejected rather than guessed at. "
                "The template's **Reference** sheet lists the exact names — "
                "ask the ICT Coordinator to regenerate it after new sections "
                "are created, or it will be listing yesterday's.",
            ),
            (
                "Imported grades arrive as DRAFT",
                "Nothing imported is treated as final. It still goes through the "
                "normal submit and verify steps. A blank cell in your file stays "
                "blank; it never becomes a 0.",
            ),
            (
                "Import term grades one section at a time",
                "Pick the school year at the top — section names repeat between "
                "years, and that choice is what keeps a grade off last year's "
                "section of the same name.\n\n"
                "After the grades are saved, every learner in the file has their "
                "averages recalculated. That's the slow part, and it's why a "
                "file per section is better than one file for the whole school. "
                "Let it finish; the progress bar is the recalculation, not the "
                "saving.",
            ),
        ],
    ),
    "SCHOOL_HEAD": (
        "School Head — Read-only view",
        [
            (
                "You can see everything and change nothing",
                "That's deliberate. The Dashboard, Grade Summary and the "
                "report pages are open to you school-wide; the buttons that "
                "encode, recompute, finalize or reopen are not drawn at all "
                "rather than shown and refused.",
            ),
            (
                "Start at the Dashboard",
                "It shows enrolment per section, how many learners have a "
                "complete record, how far grade encoding has got in each term, "
                "and which section-months of attendance are still unfinalized — "
                "which is usually the question worth asking near a deadline.",
            ),
            (
                "You can print any report",
                "SF9, SF2 and Term Cards are all available, including the "
                "whole-section SF9 print. Printing changes nothing.",
            ),
        ],
    ),
    "SUPER_ADMIN": (
        "Super Admin — Setup and control",
        [
            (
                "Section Offerings decides what a learner is graded on",
                "Not the subject catalog, and not the subject profile — those "
                "only seed defaults. If a subject is missing from a gradebook, "
                "or a term is blacked out on a report card, this is the page to "
                "look at.",
            ),
            (
                "Open the term before teachers can encode",
                "On School Years & Terms. Closing it again makes that term "
                "read-only school-wide, which is the safe state between "
                "encoding periods.",
            ),
            (
                "Generate the calendar, then check November and December",
                "Generation covers weekends and the fixed national holidays, but "
                "it deliberately does not guess at proclamation-dependent days "
                "(Eid, special non-working days, local suspensions). The page "
                "shows a per-month comparison — those two months usually need a "
                "manual adjustment, and changing a day needs a reason.",
            ),
            (
                "Reopening a finalized record is possible but audited",
                "It requires a reason and reverts the affected grades to DRAFT "
                "for re-submission. Policy or threshold changes never "
                "recalculate a finished year on their own.",
            ),
            (
                "\"It's still referenced elsewhere\" — how deleting works",
                "Nothing that is in use can be deleted, and that is deliberate: "
                "a learner's grades and attendance would go with them. The "
                "message names what you tried to delete, not what is holding "
                "it, so work down this list:\n\n"
                "- **Strand or track** — held by a Subject Profile or a "
                "Section. Delete the profile first (Subject Profiles → Delete "
                "profile), or leave the strand and just untick **Active**.\n"
                "- **Section** — held by its Section Offerings, attendance "
                "months, and any learners enrolled. Move the learners out "
                "first.\n"
                "- **Subject** — held by any Section Offering that uses it.\n"
                "- **User** — held by their roles, teaching assignments, and "
                "any section they advise. Clear those three and the delete "
                "works; everything historical about them (who submitted a "
                "grade, who finalized a month) is kept and simply stops "
                "naming them.\n"
                "- **Learner** — see below. Do not delete a real one.",
            ),
            (
                "Untick Active instead of deleting, where you can",
                "For anything with an **Active** checkbox — tracks, strands, "
                "subjects — unticking it is the intended way to retire it. It "
                "disappears from the dropdowns for new records while "
                "everything that already refers to it keeps working. Deleting "
                "is for something created by mistake five minutes ago.\n\n"
                "It also survives a re-seed: the setup script adds back any "
                "missing standard row, so a deleted strand can reappear, while "
                "a deactivated one stays deactivated.",
            ),
            (
                "Never delete a real learner",
                "Use the workflow instead — log a movement (transferred out, "
                "dropped, NLS) on the Enrollment page. Their history stays "
                "intact and the forms report them correctly for the month they "
                "left. Deleting is only for test records, and needs a script "
                "run by the ICT Coordinator.",
            ),
            (
                "Fill in Subject Profiles before Section Offerings",
                "\"Seed from profile\" on Section Offerings copies whatever "
                "the matching profile contains. If the profile is empty it "
                "copies nothing, silently, and you are left adding every "
                "subject to every section by hand. Set each profile up once "
                "per grade level and track/strand, then seeding a section is "
                "one click.",
            ),
            (
                "Give every strand a distinct name",
                "Two strands may share a name as far as the database is "
                "concerned — only the code has to be unique. But the forms "
                "print the *name*, so two strands called the same thing are "
                "indistinguishable on SF9 and SF2, and SF4 would report them "
                "as one line to the division. If two specializations are "
                "genuinely different, make their names say so.",
            ),
            (
                "One role still has no screens",
                "**Attendance Encoder** is in the role list because the "
                "specification defines it, but nothing is built for it yet — "
                "granting it on its own leaves that person with no pages at "
                "all. Give them the Adviser role for their section instead.",
            ),
            (
                "The Backup download is not your only backup",
                "It holds the school's records but **not the login accounts**, "
                "which live in Supabase. Restore from Supabase's own backups "
                "after a real failure; keep this one so the school still holds "
                "its data independently. Treat the file as confidential.",
            ),
        ],
    ),
}

# Asked often enough to be worth answering once, in writing.
NOTES = [
    (
        "Why can't I finalize a learner's grades?",
        "Their annual record isn't complete — at least one applicable subject "
        "has no grade. Encode the missing grades, press Recompute on Grade "
        "Summary, and the button becomes available.",
    ),
]


def _render_items(items) -> None:
    for title, body in items:
        st.markdown(f"**{title}**")
        st.markdown(body)
        st.write("")


def render() -> None:
    current_user = get_current_user()
    st.title("Quick Guide")
    st.caption(
        "Think of this as a quick cheat sheet for the parts of the pages that "
        "aren't super obvious. Everything else is pretty self-explanatory. Let "
        "the admin know if you get stuck on anything! -DL"
    )
    render_flashes()

    st.subheader("Applies to everyone")
    _render_items(UNIVERSAL)

    roles = sorted(current_user.role_codes) if current_user else []
    mine = [code for code in BY_ROLE if code in roles]
    others = [code for code in BY_ROLE if code not in mine]

    if mine:
        st.divider()
        st.subheader("Your role")
        for code in mine:
            heading, items = BY_ROLE[code]
            with st.expander(heading, expanded=len(mine) == 1):
                _render_items(items)

    if others:
        st.divider()
        st.subheader("Other roles" if mine else "By role")
        st.caption("Shown so you know what to expect from colleagues' screens.")
        for code in others:
            heading, items = BY_ROLE[code]
            with st.expander(heading):
                _render_items(items)

    st.divider()
    st.subheader("Question that comes up")
    for question, answer in NOTES:
        with st.expander(question):
            st.markdown(answer)
