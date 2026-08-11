import streamlit as st

from app.admin_pages._helpers import get_session, render_flashes
from app.auth import require_role
from app.excel_template import workbook_to_bytes
from app.models.academic_structure import Section
from app.models.grades import AnnualGradeSummary
from app.models.learners import Enrollment, Learner
from app.models.organization import SchoolYear
from app.pdf_convert import PdfConversionError, is_pdf_available, xlsx_to_pdf
from app.report_card import build_learning_area_rows
from app.sf9_report import MAX_LEARNING_AREAS, build_sf9_workbook

DASH = "—"


def _fmt(value):
    return int(value) if value is not None else DASH


def _preview(session, enrollment) -> None:
    """The same rows the printed form carries, including the §16 rule:
    the combined parent row shows a Final Grade, its indented components
    deliberately don't."""
    rows = build_learning_area_rows(session, enrollment)
    st.table(
        [
            {
                "Learning Area": row.display_name,
                "Term 1": _fmt(row.term_grades.get(1)),
                "Term 2": _fmt(row.term_grades.get(2)),
                "Term 3": _fmt(row.term_grades.get(3)),
                "Final Grade": "" if row.is_component else _fmt(row.final_grade),
                "Remarks": "" if row.is_component else (row.remark or DASH),
            }
            for row in rows
        ]
    )
    if len(rows) > MAX_LEARNING_AREAS:
        st.error(
            f"{len(rows)} learning areas, but the SF9 template has room for "
            f"{MAX_LEARNING_AREAS}. The form can't be generated until the section's "
            "offerings are reduced or the template is revised."
        )


def _learner_label(learner: Learner) -> str:
    return f"{learner.last_name}, {learner.first_name}"


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER")
    st.title("SF9 — Learner's Progress Report Card")
    st.caption(
        "Built from the computed grades and finalized attendance (§35) — nothing is "
        "re-encoded for the form. Grade 11's combined-language hierarchy follows §16: "
        "the parent row carries the Final Grade, its two component rows stay blank."
    )
    render_flashes()

    adviser_scoped = not current_user.has_role("SUPER_ADMIN", "REGISTRAR")

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

        enrollments = (
            session.query(Enrollment)
            .filter_by(section_id=section_choice, school_year_id=sy_choice)
            .join(Learner, Learner.id == Enrollment.learner_id)
            .order_by(Learner.last_name, Learner.first_name)
            .all()
        )
        if not enrollments:
            st.info("No learners enrolled in this section yet.")
            return

        learner_by_enrollment = {e.id: session.get(Learner, e.learner_id) for e in enrollments}
        enrollment_choice = st.selectbox(
            "Learner",
            options=[e.id for e in enrollments],
            format_func=lambda v: _learner_label(learner_by_enrollment[v]),
        )
        enrollment = next(e for e in enrollments if e.id == enrollment_choice)

        summary = (
            session.query(AnnualGradeSummary).filter_by(enrollment_id=enrollment.id).one_or_none()
        )
        col1, col2 = st.columns(2)
        col1.metric("General Average", str(_fmt(summary.general_average if summary else None)))
        col2.metric("Completion", summary.completion_status.value if summary else "not computed yet")
        if summary is None or summary.completion_status.value != "COMPLETE":
            st.warning(
                "This learner's record isn't COMPLETE. The card will still generate, but "
                "unencoded subjects print blank — recompute on Grade Summary first."
            )

        st.divider()
        st.subheader("Learning Progress and Achievement")
        _preview(session, enrollment)

        st.divider()
        st.subheader("Download")
        stem = (
            f"SF9_{learner_by_enrollment[enrollment.id].last_name}"
            f"_{learner_by_enrollment[enrollment.id].first_name}".replace(" ", "")
        )
        try:
            xlsx_bytes = workbook_to_bytes(build_sf9_workbook(session, enrollment.id))
        except ValueError as exc:
            st.error(str(exc))
            return

        col_a, col_b = st.columns(2)
        with col_a:
            st.download_button(
                "Download Excel (.xlsx)",
                data=xlsx_bytes,
                file_name=f"{stem}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        with col_b:
            if is_pdf_available():
                try:
                    st.download_button(
                        "Download PDF",
                        data=xlsx_to_pdf(xlsx_bytes, basename=stem),
                        file_name=f"{stem}.pdf",
                        mime="application/pdf",
                    )
                except PdfConversionError as exc:
                    st.error(f"PDF conversion failed — {exc}")
            else:
                st.button("Download PDF", disabled=True, key="sf9_pdf_disabled")
                st.caption(
                    "PDF export needs LibreOffice installed on the machine running this "
                    "app. Until then, open the .xlsx and print or export from Excel."
                )
