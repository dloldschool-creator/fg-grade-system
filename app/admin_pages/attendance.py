import calendar as _calendar

import pandas as pd
import streamlit as st

from app.admin_pages._helpers import flash, get_session, render_flashes, try_commit
from app.attendance_service import (
    class_days_in_month,
    finalize_month,
    get_month_status,
    get_or_create_month_status,
    months_with_class_days,
    records_for_month,
    reopen_month,
    roster_for_month,
    seed_month_records,
    summarize_month,
    validate_month,
)
from app.auth import require_role
from app.models.academic_structure import Section
from app.models.attendance import AttendanceRecord
from app.models.enums import AttendanceStatus, FinalizationState
from app.models.organization import SchoolYear

# §30's printed codes. The DB stores the internal enum; these are only the
# encoding UI's shorthand, and the SF2 renderer (Phase 9) does its own
# mapping — notably PRESENT prints as a blank cell on the paper form, but
# showing it blank *here* would make "encoded as present" and "nobody has
# touched this yet" look identical.
CODE_BY_STATUS = {
    AttendanceStatus.PRESENT: "P",
    AttendanceStatus.ABSENT: "X",
    AttendanceStatus.LATE: "T-L",
    AttendanceStatus.CUTTING: "T-C",
}
STATUS_BY_CODE = {code: status for status, code in CODE_BY_STATUS.items()}

# Shown for a day outside the learner's active window (before a late
# enrollee arrived, after a transfer-out). Not editable in any meaningful
# sense — anything typed into such a cell is ignored on save.
NOT_ELIGIBLE = "·"

LEARNER_COLUMN = "Learner"
SEX_COLUMN = "Sex"

EDITABLE_STATES = {FinalizationState.NOT_STARTED, FinalizationState.OPEN, FinalizationState.FOR_REVIEW}


def _grid_dataframe(session, roster, class_days) -> pd.DataFrame:
    records = records_for_month(
        session, [e.id for e, _, _ in roster], [d.id for d in class_days]
    )
    rows = []
    for enrollment, learner, window in roster:
        row = {
            LEARNER_COLUMN: f"{learner.last_name}, {learner.first_name}",
            SEX_COLUMN: learner.sex.value[0],
        }
        for day in class_days:
            column = str(day.calendar_date.day)
            if not window.contains(day.calendar_date):
                row[column] = NOT_ELIGIBLE
                continue
            record = records.get((enrollment.id, day.id))
            row[column] = CODE_BY_STATUS[record.status] if record else ""
        rows.append(row)
    return pd.DataFrame(rows)


def _save_grid(session, roster, class_days, edited: pd.DataFrame, user_id) -> None:
    records = records_for_month(
        session, [e.id for e, _, _ in roster], [d.id for d in class_days]
    )
    changed = 0
    invalid: list[str] = []
    for index, (enrollment, learner, window) in enumerate(roster):
        for day in class_days:
            column = str(day.calendar_date.day)
            if not window.contains(day.calendar_date):
                continue
            raw = str(edited.iloc[index][column] or "").strip().upper()
            if raw in ("", NOT_ELIGIBLE):
                continue
            status = STATUS_BY_CODE.get(raw)
            if status is None:
                invalid.append(f"{learner.last_name} on {day.calendar_date:%b %d}: '{raw}'")
                continue
            record = records.get((enrollment.id, day.id))
            if record is None:
                session.add(
                    AttendanceRecord(
                        enrollment_id=enrollment.id,
                        calendar_date_id=day.id,
                        status=status,
                        encoded_by_user_id=user_id,
                    )
                )
                changed += 1
            elif record.status != status:
                record.status = status
                record.encoded_by_user_id = user_id
                record.version += 1
                changed += 1

    if invalid:
        flash(
            "error",
            "Ignored unrecognised code(s) — use P, X, T-L or T-C: " + "; ".join(invalid[:5]),
        )
    if changed:
        try_commit(session, f"Saved {changed} attendance change(s).")
    else:
        session.rollback()
        flash("info", "No changes to save.")


def _summary_table(session, roster, class_days) -> None:
    rows = []
    for enrollment, learner, window in roster:
        summary = summarize_month(session, enrollment, window, class_days)
        rows.append(
            {
                "Learner": f"{learner.last_name}, {learner.first_name}",
                "Eligible days": summary.eligible_days,
                "Present": summary.days_present,
                "Absent": summary.days_absent,
                "Late": summary.late_count,
                "Cutting": summary.cutting_count,
                "Not encoded": summary.unencoded_days,
                "5+ absences": "⚠️" if summary.has_consecutive_absence_warning else "",
            }
        )
    st.table(rows)


def _finalization_panel(session, current_user, section_id, school_year_id, year, month) -> None:
    status = get_month_status(session, section_id, year, month)
    current_state = status.status if status else FinalizationState.NOT_STARTED

    st.write(f"**Month status:** {current_state.value}")

    if status is not None and status.status == FinalizationState.FINALIZED:
        st.success(f"Finalized {status.finalized_at:%Y-%m-%d %H:%M} — attendance is read-only.")
        if current_user.has_role("SUPER_ADMIN"):
            with st.form(f"reopen_att_{status.id}"):
                reason = st.text_area("Reopen reason (required)")
                if st.form_submit_button("Reopen"):
                    if not reason.strip():
                        st.error("A reason is required.")
                    else:
                        reopen_month(session, status, current_user.id, reason.strip())
                        try_commit(session, "Reopened — attendance is editable again.")
                        st.rerun()
        else:
            st.caption("Only a Super Admin can reopen a finalized month.")
        return

    report = validate_month(session, section_id, school_year_id, year, month)

    col1, col2, col3 = st.columns(3)
    col1.metric("Learners on this sheet", report["roster_size"])
    col2.metric("Class days", report["class_day_count"])
    col3.metric(
        "Male / Female",
        f"{report['sex_totals'].get('MALE', 0)} / {report['sex_totals'].get('FEMALE', 0)}",
    )
    if report["movement_totals"]:
        st.caption(
            "Movements on record: "
            + ", ".join(f"{k} ×{v}" for k, v in sorted(report["movement_totals"].items()))
        )

    for warning in report["warnings"]:
        st.warning(warning)
    for problem in report["problems"][:20]:
        st.error(problem)
    if len(report["problems"]) > 20:
        st.error(f"…and {len(report['problems']) - 20} more.")

    can_finalize = not report["problems"]
    if not can_finalize:
        st.caption("Resolve everything in red above before finalizing (§33).")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Mark for review", disabled=current_state == FinalizationState.FOR_REVIEW):
            row = get_or_create_month_status(session, section_id, school_year_id, year, month)
            row.status = FinalizationState.FOR_REVIEW
            row.version += 1
            try_commit(session, "Marked for review.")
            st.rerun()
    with col_b:
        if st.button("Finalize month", disabled=not can_finalize, type="primary"):
            row = get_or_create_month_status(session, section_id, school_year_id, year, month)
            finalize_month(session, row, current_user.id)
            try_commit(session, "Month finalized — attendance is now read-only.")
            st.rerun()


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER")
    st.title("Attendance")
    st.caption(
        "Daily attendance per learner per class day (§30). Codes: **P** present, "
        "**X** absent, **T-L** tardy/late, **T-C** cutting. Late and cutting still "
        f"count as days present. A **{NOT_ELIGIBLE}** cell is outside that learner's "
        "enrolment window and never counts against them."
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

        months = months_with_class_days(session, sy_choice)
        if not months:
            st.warning(
                "No class days on the academic calendar for this school year — a Super "
                "Admin needs to generate it on the Academic Calendar page first."
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

        status = get_month_status(session, section_choice, year, month)
        current_state = status.status if status else FinalizationState.NOT_STARTED
        editable = current_state in EDITABLE_STATES

        st.divider()
        if st.button("Prepare / refresh this month's sheet", disabled=not editable):
            created = seed_month_records(
                session, section_choice, sy_choice, year, month, current_user.id
            )
            row = get_or_create_month_status(session, section_choice, sy_choice, year, month)
            if row.status == FinalizationState.NOT_STARTED:
                row.status = FinalizationState.OPEN
                row.version += 1
            if try_commit(
                session,
                f"Added {created} attendance row(s) defaulting to Present.",
            ):
                st.rerun()
        st.caption(
            "Creates a Present row for every learner × class day not already encoded, "
            "so a blank cell can keep meaning \"nobody has said yet\" rather than "
            "silently counting as present. Re-run it after a late enrollee joins or "
            "the calendar changes."
        )

        st.divider()
        st.subheader(f"{_calendar.month_name[month]} {year} — {section_by_id[section_choice].name}")

        dataframe = _grid_dataframe(session, roster, class_days)
        if editable:
            edited = st.data_editor(
                dataframe,
                key=f"grid_{section_choice}_{year}_{month}",
                disabled=[LEARNER_COLUMN, SEX_COLUMN],
                column_config={
                    LEARNER_COLUMN: st.column_config.TextColumn(width="medium"),
                    SEX_COLUMN: st.column_config.TextColumn(width="small"),
                    **{
                        # NOT_ELIGIBLE has to be a valid option even though
                        # it's never a real choice — a SelectboxColumn cell
                        # holding a value outside its options renders empty,
                        # which would make an out-of-window day look like an
                        # un-encoded one.
                        str(d.calendar_date.day): st.column_config.SelectboxColumn(
                            str(d.calendar_date.day),
                            options=["", "P", "X", "T-L", "T-C", NOT_ELIGIBLE],
                            width="small",
                        )
                        for d in class_days
                    },
                },
                hide_index=True,
                use_container_width=True,
            )
            if st.button("Save attendance", type="primary"):
                _save_grid(session, roster, class_days, edited, current_user.id)
                st.rerun()
        else:
            st.dataframe(dataframe, hide_index=True, use_container_width=True)
            st.caption("This month is finalized — reopen it below to make changes.")

        st.divider()
        st.subheader("Monthly summary")
        _summary_table(session, roster, class_days)

        st.divider()
        st.subheader("Finalization")
        _finalization_panel(session, current_user, section_choice, sy_choice, year, month)
