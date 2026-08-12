import calendar as _calendar

import pandas as pd
import streamlit as st

from app.admin_pages._helpers import get_session, render_flashes, try_commit
from app.attendance_service import months_with_class_days
from app.auth import require_role
from app.export_service import (
    attendance_export,
    award_eligibility,
    final_grade_summary,
    gradebook,
    record_export,
    section_masterlist,
    to_csv,
    to_xlsx,
)
from app.models.academic_structure import Section
from app.models.awards import AwardPolicy, AwardPolicyVersion
from app.models.organization import SchoolYear, Term

EXPORTS = {
    "MASTERLIST": "Section masterlist",
    "GRADEBOOK": "Gradebook (one term)",
    "FINAL_GRADES": "Final grade summary",
    "ATTENDANCE": "Attendance (one month)",
    "AWARDS": "Award eligibility",
}


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")
    st.title("Export")
    st.caption(
        "Download any of these as Excel or CSV. LRNs keep their leading zeros "
        "when the file is opened in Excel."
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
        section = section_by_id[section_choice]

        export_choice = st.selectbox(
            "What to export", options=list(EXPORTS), format_func=lambda v: EXPORTS[v]
        )

        scope = {"school_year_id": str(sy_choice), "section_id": str(section_choice)}
        table = None

        if export_choice == "MASTERLIST":
            table = section_masterlist(session, section_choice, sy_choice)
        elif export_choice == "FINAL_GRADES":
            table = final_grade_summary(session, section_choice, sy_choice)
        elif export_choice == "GRADEBOOK":
            terms = (
                session.query(Term).filter_by(school_year_id=sy_choice).order_by(Term.term_number).all()
            )
            if not terms:
                st.warning("This school year has no terms yet.")
                return
            term_by_id = {t.id: t for t in terms}
            term_choice = st.selectbox(
                "Term", options=[t.id for t in terms], format_func=lambda v: term_by_id[v].name
            )
            scope["term_number"] = term_by_id[term_choice].term_number
            table = gradebook(session, section_choice, sy_choice, term_by_id[term_choice].term_number)
        elif export_choice == "ATTENDANCE":
            months = months_with_class_days(session, sy_choice)
            if not months:
                st.warning("No class days on the academic calendar for this school year yet.")
                return
            month_choice = st.selectbox(
                "Month",
                options=months,
                format_func=lambda ym: f"{_calendar.month_name[ym[1]]} {ym[0]}",
            )
            scope["year"], scope["month"] = month_choice
            table = attendance_export(session, section_choice, sy_choice, *month_choice)
        elif export_choice == "AWARDS":
            versions = (
                session.query(AwardPolicyVersion).filter_by(effective_school_year_id=sy_choice).all()
            )
            if not versions:
                st.warning("No award policy versions effective for this school year yet.")
                return
            policies = {p.id: p for p in session.query(AwardPolicy).all()}
            version_by_id = {v.id: v for v in versions}
            version_choice = st.selectbox(
                "Award policy",
                options=[v.id for v in versions],
                format_func=lambda v: (
                    f"{policies[version_by_id[v].award_policy_id].name} "
                    f"(v{version_by_id[v].version_number})"
                ),
            )
            scope["award_policy_version_id"] = str(version_choice)
            table = award_eligibility(session, section_choice, sy_choice, version_choice)

        if table is None:
            return

        st.divider()
        st.subheader("Preview")
        if not table.rows:
            st.info("Nothing to export for this selection.")
            return
        st.dataframe(pd.DataFrame(table.rows, columns=table.columns), hide_index=True, use_container_width=True)
        st.caption(f"{len(table.rows)} row(s).")

        st.divider()
        stem = f"{table.name.replace(' ', '')}_{section.name.replace(' ', '')}_{sy_by_id[sy_choice].name}"
        col1, col2 = st.columns(2)
        with col1:
            if st.download_button(
                "Download Excel (.xlsx)",
                data=to_xlsx(table),
                file_name=f"{stem}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            ):
                record_export(session, export_choice, scope, current_user.id, f"{stem}.xlsx")
                try_commit(session, "Export recorded.")
        with col2:
            if st.download_button(
                "Download CSV",
                data=to_csv(table),
                file_name=f"{stem}.csv",
                mime="text/csv",
            ):
                record_export(session, export_choice, scope, current_user.id, f"{stem}.csv")
                try_commit(session, "Export recorded.")
