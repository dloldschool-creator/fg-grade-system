from datetime import datetime, timezone

import streamlit as st

from app.academic_record_service import capture_academic_record, get_academic_record
from app.admin_pages._helpers import flash, get_session, render_flashes, try_commit
from app.auth import require_role
from app.grading_service import recompute_enrollment_grades
from app.report_card import build_learning_area_rows
from app.models.academic_structure import Section
from app.models.enums import CompletionStatus, FinalizationRecordStatus, FinalizationScopeType, GradeWorkflowStatus
from app.models.grades import (
    AnnualGradeSummary,
    CombinedLearningAreaResult,
    GradeFinalizationRecord,
    SubjectFinalGrade,
    TermGrade,
    TermGradeSummary,
)
from app.models.learners import Enrollment, Learner
from app.models.organization import SchoolYear, Term
from app.models.subjects import CombinedLearningArea, CombinedLearningAreaComponent, SectionSubjectOffering, Subject

DASH = "—"


def _fmt(value):
    """Grades are always whole numbers (every formula in the spec rounds,
    §60) — display as a plain int, not the raw Decimal(5,2)'s "93.00"."""
    return int(value) if value is not None else DASH


def _term_grades_by_subject(session, enrollment: Enrollment) -> dict:
    """subject_id -> {term_number: official_grade | None}, straight from
    term_grades — independent of subject_final_grades, since a subject
    can have some terms encoded and others not (that's exactly what makes
    it Incomplete)."""
    offerings = (
        session.query(SectionSubjectOffering)
        .filter_by(section_id=enrollment.section_id, school_year_id=enrollment.school_year_id)
        .all()
    )
    terms = {t.id: t for t in session.query(Term).filter_by(school_year_id=enrollment.school_year_id).all()}
    term_grades = {
        tg.section_subject_offering_id: tg
        for tg in session.query(TermGrade).filter_by(enrollment_id=enrollment.id).all()
    }
    result: dict = {}
    for offering in offerings:
        term = terms.get(offering.term_id)
        if term is None:
            continue
        tg = term_grades.get(offering.id)
        result.setdefault(offering.subject_id, {})[term.term_number] = tg.official_grade if tg else None
    return result


def _finalization_section(session, current_user, enrollment: Enrollment) -> None:
    st.subheader("Finalization")
    latest_record = (
        session.query(GradeFinalizationRecord)
        .filter_by(scope_type=FinalizationScopeType.ANNUAL_ENROLLMENT, enrollment_id=enrollment.id)
        .order_by(GradeFinalizationRecord.created_at.desc())
        .first()
    )
    is_finalized = latest_record is not None and latest_record.status == FinalizationRecordStatus.FINALIZED

    if is_finalized:
        st.success(f"Finalized {latest_record.finalized_at:%Y-%m-%d %H:%M}.")
        snapshot = get_academic_record(session, enrollment.id)
        if snapshot is not None:
            st.caption(
                f"Permanent academic record captured "
                f"{snapshot.snapshot_at:%Y-%m-%d %H:%M}"
                f"{f' (revision {snapshot.revision})' if snapshot.revision > 1 else ''} — "
                "frozen against later subject renames or policy changes (§38)."
            )
        # Reopening a finalized record is Super-Admin-only (§3A lists
        # "reopen finalized records" as a Super Admin capability, not
        # Registrar/Adviser).
        if current_user.has_role("SUPER_ADMIN"):
            with st.form(f"reopen_{enrollment.id}"):
                reason = st.text_area("Reopen reason (required)", key=f"reopen_reason_{enrollment.id}")
                if st.form_submit_button("Reopen"):
                    if not reason:
                        st.error("A reason is required.")
                    else:
                        now = datetime.now(timezone.utc)
                        latest_record.status = FinalizationRecordStatus.REOPENED
                        latest_record.reopened_by_user_id = current_user.id
                        latest_record.reopened_at = now
                        latest_record.reopen_reason = reason
                        term_grades = session.query(TermGrade).filter_by(enrollment_id=enrollment.id).all()
                        for tg in term_grades:
                            if tg.status == GradeWorkflowStatus.FINALIZED:
                                tg.status = GradeWorkflowStatus.DRAFT
                                tg.version += 1
                        try_commit(session, "Reopened — term grades reverted to DRAFT for re-submission.")
                        st.rerun()
        return

    summary = session.query(AnnualGradeSummary).filter_by(enrollment_id=enrollment.id).one_or_none()
    can_finalize = summary is not None and summary.completion_status == CompletionStatus.COMPLETE
    if not can_finalize:
        st.info(
            "Can't finalize yet — annual record isn't COMPLETE (§23). Encode the missing "
            "grades and Recompute above first."
        )
    if st.button("Finalize", key=f"finalize_{enrollment.id}", disabled=not can_finalize):
        now = datetime.now(timezone.utc)
        session.add(
            GradeFinalizationRecord(
                scope_type=FinalizationScopeType.ANNUAL_ENROLLMENT,
                enrollment_id=enrollment.id,
                finalized_by_user_id=current_user.id,
                finalized_at=now,
                status=FinalizationRecordStatus.FINALIZED,
            )
        )
        term_grades = session.query(TermGrade).filter_by(enrollment_id=enrollment.id).all()
        for tg in term_grades:
            tg.status = GradeWorkflowStatus.FINALIZED
            tg.finalized_by_user_id = current_user.id
            tg.finalized_at = now
            tg.version += 1
        # Freeze the permanent academic record (§38). Everything it shows
        # is copied now, so renaming a subject or editing the grading
        # policy in a later year can't rewrite this one.
        capture_academic_record(session, enrollment.id, current_user.id)
        try_commit(
            session,
            "Finalized — grades are read-only until an audited reopen, and the "
            "permanent academic record has been captured.",
        )
        st.rerun()


def _learner_detail(session, current_user, enrollment: Enrollment):
    summary = session.query(AnnualGradeSummary).filter_by(enrollment_id=enrollment.id).one_or_none()

    col1, col2, col3 = st.columns(3)
    col1.metric("General Average", str(_fmt(summary.general_average if summary else None)))
    col2.metric("Lowest Final Grade", str(_fmt(summary.lowest_final_grade if summary else None)))
    col3.metric("Completion", summary.completion_status.value if summary else "not computed yet")
    if summary and summary.failed_subject_count:
        st.caption(f"{summary.failed_subject_count} subject(s) currently below passing.")

    # Term Averages (§17) — shown separately from the subject table below
    # because they're computed a different way: the Grade 11 language pair
    # counts as two subjects here, not as the one combined area the Final
    # Grade column uses. This is what a TERM-scoped award (tiered Honors)
    # is judged on.
    term_summaries = (
        session.query(TermGradeSummary)
        .join(Term, Term.id == TermGradeSummary.term_id)
        .filter(TermGradeSummary.enrollment_id == enrollment.id)
        .order_by(Term.term_number)
        .all()
    )
    if term_summaries:
        term_names = {t.id: t.name for t in session.query(Term).filter_by(
            school_year_id=enrollment.school_year_id
        ).all()}
        cols = st.columns(len(term_summaries))
        for col, ts in zip(cols, term_summaries):
            col.metric(
                f"{term_names.get(ts.term_id, 'Term')} Average", str(_fmt(ts.term_average))
            )

    # Rows come from app/report_card.py, the single implementation of the
    # §16 combined-language display rule — shared with the generated SF9
    # so the screen and the printed form can never disagree.
    rows = [
        {
            "Learning Area": row.display_name,
            "Term 1": _fmt(row.term_grades.get(1)),
            "Term 2": _fmt(row.term_grades.get(2)),
            "Term 3": _fmt(row.term_grades.get(3)),
            "Final Grade": "" if row.is_component else _fmt(row.final_grade),
            "Remark": "" if row.is_component else (row.remark or DASH),
        }
        for row in build_learning_area_rows(session, enrollment)
    ]

    if rows:
        st.table(rows)
    else:
        st.caption("No computed grades yet — encode grades on the Gradebook page, or click Recompute below.")

    if st.button("Recompute", key=f"recompute_{enrollment.id}"):
        recompute_enrollment_grades(session, enrollment.id)
        flash("success", "Recomputed.")
        st.rerun()

    st.divider()
    _finalization_section(session, current_user, enrollment)


def _section_subjects(session, section_id, school_year_id) -> list[Subject]:
    """Every subject offered to this section across the year, in offering
    display order, deduplicated across terms."""
    offerings = (
        session.query(SectionSubjectOffering)
        .filter_by(section_id=section_id, school_year_id=school_year_id)
        .order_by(SectionSubjectOffering.display_order)
        .all()
    )
    subject_ids: list = []
    seen = set()
    for offering in offerings:
        if offering.subject_id not in seen:
            seen.add(offering.subject_id)
            subject_ids.append(offering.subject_id)
    return [session.get(Subject, sid) for sid in subject_ids]


VIEW_OPTIONS = ["Term 1", "Term 2", "Term 3", "Final"]


def _class_summary(session, enrollments: list[Enrollment], section_id, school_year_id) -> None:
    st.subheader("Class summary")
    st.caption(
        "Each subject's own grades for the selected view — the combined-language "
        "parent/component display (§16) is in the per-learner detail below instead. "
        "General Average still correctly counts the combined pair once, computed the "
        "same way as the per-learner detail."
    )
    view = st.radio("View", VIEW_OPTIONS, horizontal=True, key="class_summary_view")

    subjects = _section_subjects(session, section_id, school_year_id)
    summaries = {
        s.enrollment_id: s
        for s in session.query(AnnualGradeSummary)
        .filter(AnnualGradeSummary.enrollment_id.in_([e.id for e in enrollments]))
        .all()
    }

    rows = []
    for enrollment in enrollments:
        learner = session.get(Learner, enrollment.learner_id)
        row = {"Learner": f"{learner.last_name}, {learner.first_name}"}
        if view == "Final":
            finals = {
                f.subject_id: f
                for f in session.query(SubjectFinalGrade).filter_by(enrollment_id=enrollment.id).all()
            }
            for subject in subjects:
                final = finals.get(subject.id)
                row[subject.short_name] = _fmt(final.final_grade) if final else DASH
        else:
            term_number = VIEW_OPTIONS.index(view) + 1
            term_grades = _term_grades_by_subject(session, enrollment)
            for subject in subjects:
                row[subject.short_name] = _fmt(term_grades.get(subject.id, {}).get(term_number))
        summary = summaries.get(enrollment.id)
        row["General Average"] = _fmt(summary.general_average) if summary else DASH
        row["Completion"] = summary.completion_status.value if summary else "not computed yet"
        rows.append(row)
    st.table(rows)


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER")
    st.title("Grade Summary")
    st.caption(
        "Read-only view of the derived grade tables (§48) — recomputed from Gradebook "
        "entries automatically, or on demand with the Recompute button below."
    )
    render_flashes()

    # Registrar/Super Admin see every section; an Adviser-only account
    # (no Registrar/Super Admin role) is scoped to sections they're the
    # actual adviser of, per §3C — not a school-wide view.
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
            msg = (
                "You're not the adviser of any section for this school year yet."
                if adviser_scoped
                else "No sections for this school year yet."
            )
            st.warning(msg)
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

        if st.button("Recompute all in this section"):
            for enrollment in enrollments:
                recompute_enrollment_grades(session, enrollment.id)
            flash("success", f"Recomputed {len(enrollments)} learner(s).")
            st.rerun()

        _class_summary(session, enrollments, section_choice, sy_choice)

        st.divider()
        st.subheader("Per-learner detail")
        for enrollment in enrollments:
            learner = session.get(Learner, enrollment.learner_id)
            with st.expander(f"{learner.last_name}, {learner.first_name}"):
                _learner_detail(session, current_user, enrollment)
