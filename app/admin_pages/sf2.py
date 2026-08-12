import calendar as _calendar

import pandas as pd
import streamlit as st

from app.admin_pages._helpers import get_session, render_flashes
from app.attendance_service import (
    class_days_in_month,
    get_month_status,
    months_with_class_days,
    roster_for_month,
    summarize_month,
)
from app.auth import require_role
from app.models.academic_structure import Section
from app.models.attendance import AttendanceRecord
from app.models.enums import FinalizationState, Sex
from app.models.organization import SchoolYear
from app.xlsx_render import workbook_to_pdf
from app.sf2_report import (
    build_sf2_workbook,
    paginate,
    printed_code,
    workbook_to_bytes,
)


def _preview_frame(session, roster, class_days) -> pd.DataFrame:
    """On-screen preview of the same marks the printed form carries. Uses
    the printed codes (blank for present), so what's shown here is what
    lands on the page."""
    records = {}
    if roster and class_days:
        rows = (
            session.query(AttendanceRecord)
            .filter(
                AttendanceRecord.enrollment_id.in_([e.id for e, _, _ in roster]),
                AttendanceRecord.calendar_date_id.in_([d.id for d in class_days]),
            )
            .all()
        )
        records = {(r.enrollment_id, r.calendar_date_id): r.status for r in rows}

    frame_rows = []
    for enrollment, learner, window in roster:
        row = {
            "Sex": learner.sex.value[0],
            "Name": f"{learner.last_name}, {learner.first_name}",
        }
        for day in class_days:
            key = str(day.calendar_date.day)
            if not window.contains(day.calendar_date):
                row[key] = "·"
            else:
                row[key] = printed_code(records.get((enrollment.id, day.id)))
        summary = summarize_month(session, enrollment, window, class_days)
        row["ABS"] = summary.days_absent
        row["PRES"] = summary.days_present
        frame_rows.append(row)
    return pd.DataFrame(frame_rows)


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")
    st.title("SF2 — Daily Attendance Report of Learners")
    st.caption(
        "Generated straight from the attendance you have already encoded. Learners "
        "are split male and female, and a large section runs onto extra pages "
        "rather than dropping anyone."
    )
    render_flashes()

    adviser_scoped = not current_user.has_role("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD")

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("No school years yet.")
            return
        sy_by_id = {sy.id: sy for sy in school_years}
        sy_choice = st.selectbox(
            "School year", options=[sy.id for sy in school_years], format_func=lambda v: sy_by_id[v].name
        )

        sections_query = session.query(Section).filter_by(school_year_id=sy_choice)
        if adviser_scoped:
            sections_query = sections_query.filter_by(adviser_user_id=current_user.id)
        sections = sections_query.order_by(Section.name).all()
        if not sections:
            st.warning(
                "You're not the adviser of any section for this school year yet."
                if adviser_scoped
                else "No sections for this school year yet."
            )
            return
        section_by_id = {s.id: s for s in sections}
        section_choice = st.selectbox(
            "Section", options=[s.id for s in sections], format_func=lambda v: section_by_id[v].name
        )

        months = months_with_class_days(session, sy_choice)
        if not months:
            st.warning(
                "No class days on the academic calendar for this school year — generate "
                "it on the Academic Calendar page first."
            )
            return
        month_choice = st.selectbox(
            "Month",
            options=months,
            format_func=lambda ym: f"{_calendar.month_name[ym[1]]} {ym[0]}",
        )
        year, month = month_choice

        class_days = class_days_in_month(session, sy_choice, year, month)
        roster = roster_for_month(session, section_choice, sy_choice, year, month)
        if not roster:
            st.info("No learners on this section's sheet for this month.")
            return

        males = [r for r in roster if r[1].sex == Sex.MALE]
        females = [r for r in roster if r[1].sex == Sex.FEMALE]
        pages = paginate(len(males), len(females))

        status = get_month_status(session, section_choice, year, month)
        state = status.status if status else FinalizationState.NOT_STARTED

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Male / Female", f"{len(males)} / {len(females)}")
        col2.metric("Class days", len(class_days))
        col3.metric("Pages", pages)
        col4.metric("Attendance", state.value)

        if state != FinalizationState.FINALIZED:
            st.warning(
                f"This month's attendance is **{state.value}**, not FINALIZED. The form "
                "will generate, but figures can still change — finalize on the "
                "Attendance page before submitting it."
            )

        st.divider()
        st.subheader("Preview")
        st.caption(
            "Blank means present — that is the form's own convention. "
            "**X** absent, **T-L** late, **T-C** cutting, **·** before the learner "
            "joined or after they left."
        )
        st.dataframe(_preview_frame(session, roster, class_days), hide_index=True, use_container_width=True)

        st.divider()
        st.subheader("Download")

        workbook = build_sf2_workbook(session, section_choice, sy_choice, year, month)
        xlsx_bytes = workbook_to_bytes(workbook)
        stem = f"SF2_{section_by_id[section_choice].name.replace(' ', '')}_{year}-{month:02d}"

        col_a, col_b = st.columns(2)
        with col_a:
            st.download_button(
                "Download Excel (.xlsx)",
                data=xlsx_bytes,
                file_name=f"{stem}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        with col_b:
            st.download_button(
                "Download PDF",
                data=workbook_to_pdf(workbook),
                file_name=f"{stem}.pdf",
                mime="application/pdf",
            )
