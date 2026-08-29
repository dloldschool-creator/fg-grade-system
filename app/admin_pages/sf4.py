"""SF4-SHS — Monthly Learners' Movement and Attendance (§77).

School-wide rather than per-section, so this is a Registrar / School Head
page: the form's own footer is signed by the School Head.

Excel only, by decision — SF4 is submitted as a file rather than printed,
so there is no PDF button here.
"""

import calendar as _calendar

import pandas as pd
import streamlit as st

from app.admin_pages._helpers import get_session, render_flashes
from app.attendance_service import class_days_in_month, months_with_class_days
from app.auth import require_role
from app.excel_template import workbook_to_bytes
from app.models.organization import SchoolYear
from app.sf4_report import MOVEMENT_COLUMNS, build_sf4_workbook


def _preview_frame(rows, class_day_count: int) -> pd.DataFrame:
    """The same figures the form carries, one line per Track/Strand."""
    frame = []
    for entry in rows:
        average = entry.daily_average(class_day_count)
        percentage = entry.percentage()
        line = {
            "Grade": entry.grade_number,
            "Track": entry.track,
            "Strand": entry.strand,
            "Registered (M/F/T)": f"{entry.registered.male:.0f} / "
            f"{entry.registered.female:.0f} / {entry.registered.total:.0f}",
            "Daily average": f"{average.total:.2f}",
            "% for the month": f"{percentage.total:.2f}%",
        }
        for movement_type in MOVEMENT_COLUMNS:
            _before, during = entry.movement(movement_type)
            line[movement_type.value.replace("_", " ").title()] = int(during.total)
        frame.append(line)
    return pd.DataFrame(frame)


def render() -> None:
    require_role("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD")
    st.title("SF4 — Monthly Learners' Movement and Attendance")
    st.caption(
        "School-wide, one row per Track and Strand. Every figure is read from "
        "the attendance and movement records — nothing is re-entered for the "
        "form."
    )
    render_flashes()

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("No school years yet.")
            return
        sy_by_id = {sy.id: sy for sy in school_years}

        col1, col2 = st.columns(2)
        with col1:
            sy_choice = st.selectbox(
                "School year",
                options=[sy.id for sy in school_years],
                format_func=lambda v: sy_by_id[v].name,
            )

        months = months_with_class_days(session, sy_choice)
        if not months:
            st.warning(
                "No class days on the academic calendar for this school year — "
                "generate it on the Academic Calendar page first."
            )
            return

        with col2:
            # Changing the month regenerates everything below it; Streamlit
            # re-runs the script on the selection.
            year, month = st.selectbox(
                "Report month",
                options=months,
                format_func=lambda ym: f"{_calendar.month_name[ym[1]]} {ym[0]}",
            )

        class_days = class_days_in_month(session, sy_choice, year, month)

        try:
            workbook = build_sf4_workbook(session, sy_choice, year, month)
        except ValueError as exc:
            st.error(str(exc))
            return

        # Rebuilt for the preview from the same call's data would mean
        # aggregating twice, so the preview reads the sheet the form was
        # filled from instead — what you see is literally what downloads.
        from app.sf4_report import SHEET_NAME

        worksheet = workbook[SHEET_NAME]
        col1, col2, col3 = st.columns(3)
        col1.metric("Class days this month", len(class_days))
        col2.metric("Registered (end of month)", worksheet.cell(36, 5).value or 0)
        col3.metric("Attendance for the month", f"{worksheet.cell(36, 11).value or 0}%")

        st.divider()
        st.subheader("Preview")
        preview = []
        for row_number in list(range(12, 23)) + list(range(24, 35)):
            track = worksheet.cell(row_number, 1).value
            if not track and not worksheet.cell(row_number, 2).value:
                continue
            preview.append(
                {
                    "Track": track,
                    "Strand": worksheet.cell(row_number, 2).value,
                    "Registered M": worksheet.cell(row_number, 3).value,
                    "Registered F": worksheet.cell(row_number, 4).value,
                    "Registered T": worksheet.cell(row_number, 5).value,
                    "Daily average": worksheet.cell(row_number, 8).value,
                    "% for month": worksheet.cell(row_number, 11).value,
                    "Dropped": worksheet.cell(row_number, 15).value,
                    "Transferred out": worksheet.cell(row_number, 24).value,
                    "Transferred in": worksheet.cell(row_number, 33).value,
                    "Shifted out": worksheet.cell(row_number, 42).value,
                    "Shifted in": worksheet.cell(row_number, 51).value,
                }
            )
        if preview:
            st.dataframe(pd.DataFrame(preview), hide_index=True, width="stretch")
            st.caption(
                "Movement columns show this month's figures; the form itself also "
                "carries the running totals before and after the month."
            )
        else:
            st.info("No enrolled learners for this school year yet.")
            return

        st.divider()
        stem = f"SF4_{sy_by_id[sy_choice].name}_{year}-{month:02d}"
        st.download_button(
            "Download Excel (.xlsx)",
            data=workbook_to_bytes(workbook),
            file_name=f"{stem}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
