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
        "out — that's a known limitation, not a fault.",
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
                "LRN is 12 digits and must be unique",
                "It's stored as text so leading zeros survive. If you paste from "
                "Excel, check it hasn't been turned into something like "
                "`1.07041E+11` — that value is destroyed, not just displayed "
                "oddly.",
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
                "Imported grades arrive as DRAFT",
                "Nothing imported is treated as final. It still goes through the "
                "normal submit and verify steps. A blank cell in your file stays "
                "blank; it never becomes a 0.",
            ),
        ],
    ),
    "SCHOOL_HEAD": (
        "School Head — Read-only view",
        [
            (
                "You can see everything and change nothing",
                "That's deliberate (§3F). The Dashboard, Grade Summary and the "
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
            (
                "If you also advise a section, you edit normally",
                "The read-only limit follows the account, not the title. "
                "Holding School Head alongside Adviser or Registrar gives you "
                "that role's editing rights as usual.",
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
        "The things worth knowing that the screens don't make obvious. "
        "Everything else is meant to be self-explanatory — if it isn't, say so "
        "and it should be fixed rather than documented."
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
