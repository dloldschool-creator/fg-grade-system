"""School-wide dashboard (§3F "view dashboards", "review section summaries").

Read-only by construction: it draws no control that writes anything, so a
School Head reaching it cannot change official data whatever else changes
around it. That is a stronger guarantee than hiding buttons.

Every figure is read from the already-computed tables — this page never
recomputes a grade or an attendance total, so what a principal sees here
is exactly what the gradebooks and report cards say.
"""

import calendar as _calendar

import pandas as pd
import streamlit as st
from sqlalchemy import func

from app.admin_pages._helpers import get_session, render_flashes
from app.auth import require_role
from app.models.academic_structure import GradeLevel, Section, Strand, Track
from app.models.attendance import AttendanceMonthStatus
from app.models.enums import CompletionStatus, EnrollmentStatus, FinalizationState
from app.models.grades import AnnualGradeSummary, TermGrade
from app.models.learners import Enrollment
from app.models.organization import SchoolYear, Term

# The statuses that mean a learner is still on the roll — same set the
# gradebook and SF2 use, so headcounts agree across the app.
ACTIVE_STATUSES = {
    EnrollmentStatus.ENROLLED,
    EnrollmentStatus.LATE_ENROLLMENT,
    EnrollmentStatus.TRANSFERRED_IN,
    EnrollmentStatus.SHIFTED_IN,
}

DASH = "—"


def _enrollment_overview(session, sy_id) -> list[dict]:
    """One row per section, carrying its grade level, track and strand.

    Sorted by grade level, then track, then strand, then section name, so
    the rows arrive already grouped the way the page displays them and
    the way SF4 reports them.
    """
    sections = session.query(Section).filter_by(school_year_id=sy_id).all()
    if not sections:
        return []

    grade_levels = {g.id: g for g in session.query(GradeLevel).all()}
    tracks = {t.id: t for t in session.query(Track).all()}
    strands = {s.id: s for s in session.query(Strand).all()}
    enrollments = session.query(Enrollment).filter_by(school_year_id=sy_id).all()

    by_section: dict = {}
    for enrollment in enrollments:
        by_section.setdefault(enrollment.section_id, []).append(enrollment)

    summaries = {
        row.enrollment_id: row
        for row in session.query(AnnualGradeSummary)
        .filter(AnnualGradeSummary.enrollment_id.in_([e.id for e in enrollments]))
        .all()
    } if enrollments else {}

    rows = []
    for section in sections:
        members = by_section.get(section.id, [])
        active = [e for e in members if e.enrollment_status in ACTIVE_STATUSES]
        complete = sum(
            1 for e in active
            if e.id in summaries and summaries[e.id].completion_status == CompletionStatus.COMPLETE
        )
        grade_level = grade_levels.get(section.grade_level_id)
        track = tracks.get(section.track_id)
        strand = strands.get(section.strand_id)
        rows.append(
            {
                # Kept for grouping and ordering, dropped before display.
                "_grade_order": grade_level.display_order if grade_level else 0,
                "_grade": grade_level.name if grade_level else DASH,
                "Track": track.name if track else DASH,
                "Strand": strand.name if strand else DASH,
                "Section": section.name,
                "Active": len(active),
                "On roll": len(members),
                "Records complete": f"{complete} / {len(active)}" if active else DASH,
            }
        )

    rows.sort(key=lambda r: (r["_grade_order"], r["Track"], r["Strand"], r["Section"]))
    return rows


DISPLAY_COLUMNS = ["Track", "Strand", "Section", "Active", "On roll", "Records complete"]


def _render_sections(rows: list[dict]) -> None:
    """A table per grade level, ordered by track then strand within each.

    Grouped rather than one flat list because that is how the school
    thinks about it and how SF4 reports it — and with thirty sections a
    single table is a wall of names.
    """
    for grade in dict.fromkeys(row["_grade"] for row in rows):
        block = [row for row in rows if row["_grade"] == grade]
        active = sum(row["Active"] for row in block)
        strands = len({row["Strand"] for row in block})

        st.markdown(
            f"**{grade}** — {len(block)} section(s), {strands} strand(s), "
            f"{active:,} active learner(s)"
        )
        st.dataframe(
            pd.DataFrame([{k: row[k] for k in DISPLAY_COLUMNS} for row in block]),
            hide_index=True,
            use_container_width=True,
        )


def _encoding_progress(session, sy_id) -> list[dict]:
    """How far grade encoding has got, per term — the question a school
    head actually asks near a deadline."""
    terms = session.query(Term).filter_by(school_year_id=sy_id).order_by(Term.term_number).all()
    if not terms:
        return []

    counts = dict(
        session.query(TermGrade.term_id, func.count(TermGrade.id))
        .filter(TermGrade.term_id.in_([t.id for t in terms]))
        .group_by(TermGrade.term_id)
        .all()
    )
    submitted = dict(
        session.query(TermGrade.term_id, func.count(TermGrade.id))
        .filter(
            TermGrade.term_id.in_([t.id for t in terms]),
            TermGrade.official_grade.isnot(None),
        )
        .group_by(TermGrade.term_id)
        .all()
    )

    return [
        {
            "Term": term.name,
            "Encoding": term.grade_encoding_status.value if term.grade_encoding_status else DASH,
            "Grade rows": counts.get(term.id, 0),
            "With a grade": submitted.get(term.id, 0),
        }
        for term in terms
    ]


def _attendance_status(session, sy_id) -> list[dict]:
    """Which section-months are still open (§33). Blank means nobody has
    started that month, which is different from started-and-incomplete."""
    sections = {
        s.id: s for s in session.query(Section).filter_by(school_year_id=sy_id).all()
    }
    if not sections:
        return []
    statuses = (
        session.query(AttendanceMonthStatus)
        .filter(AttendanceMonthStatus.section_id.in_(list(sections)))
        .all()
    )
    if not statuses:
        return []

    # year_month is a date pinned to the 1st, not separate year/month
    # columns.
    rows = []
    for status in sorted(statuses, key=lambda s: (s.year_month, sections[s.section_id].name)):
        rows.append(
            {
                "Section": sections[status.section_id].name,
                "Month": f"{_calendar.month_name[status.year_month.month]} {status.year_month.year}",
                "Status": status.status.value if status.status else DASH,
            }
        )
    return rows


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD")
    st.title("School Dashboard")
    st.caption(
        "Nothing on this page changes any data."
    )
    render_flashes()

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("No school years yet.")
            return
        sy_by_id = {sy.id: sy for sy in school_years}
        sy_choice = st.selectbox(
            "School year",
            options=[sy.id for sy in school_years],
            format_func=lambda v: sy_by_id[v].name,
        )

        sections = _enrollment_overview(session, sy_choice)
        if not sections:
            st.info("No sections for this school year yet.")
            return

        total_active = sum(row["Active"] for row in sections)
        col1, col2, col3 = st.columns(3)
        col1.metric("Sections", len(sections))
        col2.metric("Active learners", f"{total_active:,}")
        col3.metric(
            "Records complete",
            sum(
                int(str(row["Records complete"]).split("/")[0])
                for row in sections
                if row["Records complete"] != DASH
            ),
        )

        st.divider()
        st.subheader("Sections")
        _render_sections(sections)

        st.divider()
        st.subheader("Grade encoding")
        progress = _encoding_progress(session, sy_choice)
        if progress:
            st.dataframe(pd.DataFrame(progress), hide_index=True, use_container_width=True)
            st.caption(
                "A term shows CLOSED when teachers cannot currently encode grades "
                "for it. That is the normal state between encoding periods."
            )
        else:
            st.caption("This school year has no terms yet.")

        st.divider()
        st.subheader("Attendance months")
        attendance = _attendance_status(session, sy_choice)
        if attendance:
            outstanding = [r for r in attendance if r["Status"] != FinalizationState.FINALIZED.value]
            if outstanding:
                st.caption(f"{len(outstanding)} section-month(s) not yet finalized.")
            st.dataframe(pd.DataFrame(attendance), hide_index=True, use_container_width=True)
        else:
            st.caption("No attendance months have been started yet.")

        if current_user.is_read_only():
            st.divider()
            st.caption(
                "You have read-only access. Reports can be viewed and printed from "
                "the SF9, SF2 and Term Cards pages."
            )
