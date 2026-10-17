import calendar as _calendar

import pandas as pd
import streamlit as st

from app.admin_pages import attendance
from app.admin_pages._helpers import (
    get_session,
    render_flashes,
    render_reprint_notice,
    section_picker,
)
from app.attendance_service import (
    class_days_in_month,
    display_state,
    get_month_status,
    months_with_class_days,
    roster_for_month,
    summarize_month_batch,
)
from app.auth import require_role
from app.display_time import current_month_index
from app.models.attendance import AttendanceRecord
from app.models.enums import FinalizationState, Sex
from app.models.organization import SchoolYear
from app.sf2_download import render_sf2_downloads
from app.sf2_report import paginate, printed_code

# Must match the Attendance entry in streamlit_app.GROUPS — a page link is
# resolved by url_path alone (tests/test_navigation.py checks the pair).
ATTENDANCE_URL_PATH = "attendance"


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

    summaries = summarize_month_batch(session, roster, class_days)
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
        summary = summaries[enrollment.id]
        row["ABS"] = summary.days_absent
        row["PRES"] = summary.days_present
        frame_rows.append(row)
    return pd.DataFrame(frame_rows)


def _attendance_link() -> None:
    """Where the month is encoded and finalized. Callers draw it only for
    someone who may go there: a School Head cannot reach Attendance, and a
    link to a page missing from their sidebar leads nowhere."""
    st.page_link(
        st.Page(attendance.render, title="Attendance & SF2", url_path=ATTENDANCE_URL_PATH),
        label="Go to Attendance & SF2 to encode or finalize this month",
        icon="🗒️",
    )


def render() -> None:
    # No ADVISER (2026-10-17): advisers came here expecting to encode, as on
    # the workbook's SF2 sheet. They print from the Attendance page instead,
    # where encoding, finalizing and printing are one page.
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD")
    st.title("SF2 — Daily Attendance Report of Learners")
    st.caption(
        "Generated from the attendance encoded on the Attendance & SF2 page. "
        "Advisers update and finalize each month there, and print their own SF2 "
        "from it once the month is finalized."
    )
    render_flashes()

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("No school years yet.")
            return
        sy_by_id = {sy.id: sy for sy in school_years}
        sy_choice = st.selectbox(
            "School year", options=[sy.id for sy in school_years], format_func=lambda v: sy_by_id[v].name
        )

        section = section_picker(session, sy_choice, key="sf2")
        if section is None:
            return
        section_choice = section.id

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
            index=current_month_index(months),
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
        col4.metric("Attendance", display_state(state))

        if state != FinalizationState.FINALIZED:
            st.warning(
                f"This month's attendance is **{display_state(state)}**, not FINALIZED. The form "
                "will generate, but figures can still change — the adviser finalizes "
                "it on the Attendance & SF2 page before it is submitted."
            )
        if current_user.has_role("REGISTRAR", "ADVISER"):
            _attendance_link()
        render_reprint_notice(session, current_user, section, status)

        st.divider()
        st.subheader("Preview — read-only")
        st.caption(
            "Blank means present — that is the form's own convention. "
            "**X** absent, **T-L** late, **T-C** cutting, **·** (dot) before the "
            "learner joined or after they left."
        )
        st.dataframe(_preview_frame(session, roster, class_days), hide_index=True, width="stretch")

        st.divider()
        st.subheader("Download")
        render_sf2_downloads(section, sy_choice, year, month)
