import io
import zipfile

import streamlit as st

from app.admin_pages._helpers import get_session, render_flashes
from app.auth import require_role
from app.excel_template import workbook_to_bytes
from app.models.academic_structure import Section
from app.models.enums import CompletionStatus
from app.models.grades import AnnualGradeSummary
from app.models.learners import Enrollment, Learner
from app.models.organization import SchoolYear
from app.report_card import build_learning_area_rows
from app.sf9_report import MAX_LEARNING_AREAS, build_sf9_workbook, load_sf9_context
from app.xlsx_render import workbook_to_pdf, workbooks_to_pdf

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
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")
    st.title("SF9 — Learner's Progress Report Card")
    st.caption(
        "Built from the grades and attendance already encoded — nothing is typed "
        "again for the form."
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
            workbook = build_sf9_workbook(session, enrollment.id)
        except ValueError as exc:
            st.error(str(exc))
            return

        col_a, col_b = st.columns(2)
        with col_a:
            st.download_button(
                "Download Excel (.xlsx)",
                data=workbook_to_bytes(workbook),
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

        _batch_section(session, enrollments, learner_by_enrollment, section_by_id[section_choice])


def _batch_section(session, enrollments, learner_by_enrollment, section) -> None:
    """One PDF holding every learner's card, a page each (§35).

    Worth doing in one file rather than a download per learner: an adviser
    printing 40 cards otherwise clicks 40 times and collates by hand.
    """
    st.divider()
    st.subheader("Print the whole section")

    summaries = {
        row.enrollment_id: row
        for row in session.query(AnnualGradeSummary)
        .filter(AnnualGradeSummary.enrollment_id.in_([e.id for e in enrollments]))
        .all()
    }
    complete = [
        e for e in enrollments
        if e.id in summaries and summaries[e.id].completion_status == CompletionStatus.COMPLETE
    ]
    incomplete = [e for e in enrollments if e not in complete]

    st.caption(
        f"{len(complete)} of {len(enrollments)} learner(s) have a COMPLETE annual record."
    )
    if incomplete:
        # Not a blocker: an adviser may legitimately want the finished
        # cards now. But an unencoded subject prints blank, and a blank
        # cell on a card going home reads as a missing grade rather than
        # an unfinished one — so name who is affected.
        st.warning(
            "These learners aren't COMPLETE and will print with blank cells — "
            "recompute on Grade Summary first if that's not intended: "
            + ", ".join(_learner_label(learner_by_enrollment[e.id]) for e in incomplete[:8])
            + (f" (+{len(incomplete) - 8} more)" if len(incomplete) > 8 else "")
        )

    only_complete = st.checkbox(
        "Only include learners with a COMPLETE record",
        value=bool(incomplete),
        key="sf9_batch_complete_only",
    )
    chosen = complete if only_complete else list(enrollments)
    if not chosen:
        st.info("No learners match — nothing to print.")
        return

    # Splitting into several files doesn't make the work shorter — the
    # cost is per learner either way — it only lets printing start
    # sooner, at the price of collating more files. One file with a
    # progress bar is the better default; the option is here because
    # printing 40 cards in one go can be worth breaking up.
    per_file = st.selectbox(
        "Learners per file",
        options=[0, 20, 10, 5],
        format_func=lambda v: "All in one PDF" if v == 0 else f"{v} per PDF (zipped)",
        key="sf9_batch_chunk",
    )

    if not st.button(f"Build for {len(chosen)} learner(s)", type="primary"):
        return

    groups = (
        [chosen] if not per_file
        else [chosen[i:i + per_file] for i in range(0, len(chosen), per_file)]
    )

    progress = st.progress(0.0, text="Loading section data…")
    done = 0

    # One context for the whole section regardless of grouping: without it
    # each card issues ~43 queries, which at ~85ms per round trip is over
    # two minutes for a full section.
    context = load_sf9_context(session, chosen)

    def _workbooks(group):
        nonlocal done
        # Built lazily, one at a time, so a 40-learner section costs one
        # workbook of memory rather than forty.
        for enrollment in group:
            yield build_sf9_workbook(session, enrollment.id, context)
            done += 1
            progress.progress(
                done / len(chosen),
                text=f"Rendered {done} of {len(chosen)} card(s)…",
            )

    try:
        rendered = [(group, workbooks_to_pdf(_workbooks(group))) for group in groups]
    except ValueError as exc:
        progress.empty()
        st.error(str(exc))
        return
    progress.empty()

    stem = f"SF9_{section.name.replace(' ', '')}"
    if len(rendered) == 1:
        data = rendered[0][1]
        st.success(f"{len(chosen)} card(s), {len(data) / 1024:,.0f} KB.")
        st.download_button(
            "Download section PDF", data=data, file_name=f"{stem}_all.pdf",
            mime="application/pdf", type="primary",
        )
        return

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (group, data) in enumerate(rendered, start=1):
            archive.writestr(f"{stem}_part{index:02d}_of_{len(rendered)}.pdf", data)
    payload = buffer.getvalue()
    st.success(
        f"{len(chosen)} card(s) across {len(rendered)} PDF(s), "
        f"{len(payload) / 1024:,.0f} KB zipped."
    )
    st.download_button(
        "Download section PDFs (.zip)", data=payload, file_name=f"{stem}_all.zip",
        mime="application/zip", type="primary",
    )
