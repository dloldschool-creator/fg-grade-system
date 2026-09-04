from datetime import datetime, timezone

import streamlit as st

from app import audit_service
from app.academic_record_service import capture_academic_record, get_academic_record
from app.admin_pages._helpers import (
    flash,
    get_session,
    render_flashes,
    section_picker,
    try_commit,
)
from app.auth import require_role
from app.display_time import format_time
from app.grading_service import recompute_enrollment_grades, recompute_enrollment_grades_batch
from app.report_card import build_learning_area_rows, load_report_context
from app.models.enums import AveragingMethod, CompletionStatus, FinalizationRecordStatus, FinalizationScopeType, GradeWorkflowStatus
from app.models.grades import (
    AnnualGradeSummary,
    CombinedLearningAreaResult,
    GradeFinalizationRecord,
    SubjectFinalGrade,
    TermGrade,
    TermGradeSummary,
)
from app.models.learners import Enrollment, Learner
from app.roster_order import learner_order_by
from app.models.organization import SchoolYear, Term
from app.models.subjects import CombinedLearningArea, CombinedLearningAreaComponent, SectionSubjectOffering, Subject

DASH = "—"


def _fmt(value):
    """Grades are always whole numbers (every formula in the spec rounds,
    §60) — display as a plain int, not the raw Decimal(5,2)'s "93.00"."""
    return int(value) if value is not None else DASH


def _fmt_units(value):
    """Units are usually whole (2, 3, 6, 12) but the column allows halves,
    so drop a trailing ".00" without hiding a real fraction."""
    if value is None:
        return DASH
    return str(int(value)) if value == int(value) else str(value.normalize())


def _finalization_section(session, current_user, enrollment: Enrollment) -> None:
    st.subheader("Finalization")
    # A School Head reviews finalized records (§3E) but never finalizes or
    # reopens one, so the state is shown and the controls are not drawn.
    read_only = current_user.is_read_only()
    latest_record = (
        session.query(GradeFinalizationRecord)
        .filter_by(scope_type=FinalizationScopeType.ANNUAL_ENROLLMENT, enrollment_id=enrollment.id)
        .order_by(GradeFinalizationRecord.created_at.desc())
        .first()
    )
    is_finalized = latest_record is not None and latest_record.status == FinalizationRecordStatus.FINALIZED

    if is_finalized:
        st.success(f"Finalized {format_time(latest_record.finalized_at)}.")
        snapshot = get_academic_record(session, enrollment.id)
        if snapshot is not None:
            st.caption(
                f"Permanent academic record captured "
                f"{format_time(snapshot.snapshot_at)}"
                f"{f' (revision {snapshot.revision})' if snapshot.revision > 1 else ''} — "
                "this learner's result is now kept exactly as it stands today."
            )
        # Reopening a finalized record is Super-Admin-only (§3A lists
        # "reopen finalized records" as a Super Admin capability, not
        # Registrar/Adviser).
        if current_user.has_role("SUPER_ADMIN") and not read_only:
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
                        reverted = 0
                        for tg in term_grades:
                            if tg.status == GradeWorkflowStatus.FINALIZED:
                                tg.status = GradeWorkflowStatus.DRAFT
                                tg.version += 1
                                reverted += 1
                        # §50 requires a reason on a reopen, which is why
                        # the empty-reason branch above returns first.
                        audit_service.record(
                            session,
                            action=audit_service.GRADE_REOPENED,
                            object_type="enrollments",
                            object_id=enrollment.id,
                            user_id=current_user.id,
                            previous={"status": FinalizationRecordStatus.FINALIZED},
                            new={
                                "status": FinalizationRecordStatus.REOPENED,
                                "term_grades_reverted_to_draft": reverted,
                            },
                            reason=reason,
                        )
                        try_commit(session, "Reopened — term grades reverted to DRAFT for re-submission.")
                        st.rerun()
        return

    summary = session.query(AnnualGradeSummary).filter_by(enrollment_id=enrollment.id).one_or_none()
    if read_only:
        st.caption("Not finalized yet. Finalizing is done by the registrar or adviser.")
        return

    can_finalize = summary is not None and summary.completion_status == CompletionStatus.COMPLETE
    if not can_finalize:
        st.info(
            "Can't finalize yet — this learner's record isn't complete. Encode the "
            "missing grades and Recompute above first."
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
        audit_service.record(
            session,
            action=audit_service.GRADE_FINALIZED,
            object_type="enrollments",
            object_id=enrollment.id,
            user_id=current_user.id,
            new={
                "general_average": summary.general_average,
                "term_grades_finalized": len(term_grades),
            },
        )
        try_commit(
            session,
            "Finalized — grades are read-only until an audited reopen, and the "
            "permanent academic record has been captured.",
        )
        st.rerun()


def _panel_data(session, enrollments, school_year_id) -> dict:
    """The three lookups each learner's panel used to make for itself.

    `load_report_context` already spared the subject table its queries,
    but the two metric rows above it were still one query per learner
    each — and Streamlit runs an expander's body whether or not it is
    open, so a collapsed section still paid for all of them on every
    rerun. Three queries flat, instead of three per learner.
    """
    ids = [e.id for e in enrollments]
    annual = {
        row.enrollment_id: row
        for row in session.query(AnnualGradeSummary)
        .filter(AnnualGradeSummary.enrollment_id.in_(ids))
        .all()
    }
    per_term: dict = {}
    rows = (
        session.query(TermGradeSummary)
        .join(Term, Term.id == TermGradeSummary.term_id)
        .filter(TermGradeSummary.enrollment_id.in_(ids))
        .order_by(Term.term_number)
        .all()
    )
    for row in rows:
        per_term.setdefault(row.enrollment_id, []).append(row)
    term_names = {
        t.id: t.name
        for t in session.query(Term).filter_by(school_year_id=school_year_id).all()
    }
    return {"annual": annual, "terms": per_term, "term_names": term_names}


def _learner_detail(session, current_user, enrollment: Enrollment, context=None, panel=None):
    summary = panel["annual"].get(enrollment.id)

    col1, col2, col3 = st.columns(3)
    col1.metric("General Average", str(_fmt(summary.general_average if summary else None)))
    col2.metric("Lowest Final Grade", str(_fmt(summary.lowest_final_grade if summary else None)))
    col3.metric("Completion", summary.completion_status.value if summary else "not computed yet")
    if summary and summary.failed_subject_count:
        st.caption(f"{summary.failed_subject_count} subject(s) currently below passing.")
    # How the General Average was reached. Shown only when it is weighted,
    # because that is the case where the number cannot be checked by eye —
    # an adviser can average five grades in their head, but not five grades
    # against thirty-nine units. A wrong unit is otherwise invisible.
    if summary is not None and summary.averaging_method == AveragingMethod.UNIT_WEIGHTED:
        st.caption(
            f"Unit-weighted over {_fmt_units(summary.total_units)} units "
            "(DepEd Order 017 s. 2026)."
        )

    # Term Averages (§17) — shown separately from the subject table below
    # because they're computed a different way: the Grade 11 language pair
    # counts as two subjects here, not as the one combined area the Final
    # Grade column uses. This is what a TERM-scoped award (tiered Honors)
    # is judged on.
    term_summaries = panel["terms"].get(enrollment.id, [])
    if term_summaries:
        term_names = panel["term_names"]
        cols = st.columns(len(term_summaries))
        for col, ts in zip(cols, term_summaries):
            col.metric(
                f"{term_names.get(ts.term_id, 'Term')} Average", str(_fmt(ts.term_average))
            )
            if ts.averaging_method == AveragingMethod.UNIT_WEIGHTED:
                col.caption(f"{_fmt_units(ts.total_units)} units")

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
        for row in build_learning_area_rows(session, enrollment, context)
    ]

    if rows:
        st.table(rows)
    elif current_user.is_read_only():
        st.caption("No computed grades yet.")
    else:
        st.caption("No computed grades yet — encode grades on the Gradebook page, or click Recompute below.")

    # Recompute writes to the derived tables, so it counts as changing
    # official data even though it invents nothing (§3E).
    if not current_user.is_read_only():
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
        "One column per subject. Pick a learner below to see their report-card "
        "view, including how the Grade 11 language pair is combined."
    )
    view = st.radio("View", VIEW_OPTIONS, horizontal=True, key="class_summary_view")

    subjects = _section_subjects(session, section_id, school_year_id)
    enrollment_ids = [e.id for e in enrollments]
    summaries = {
        s.enrollment_id: s
        for s in session.query(AnnualGradeSummary)
        .filter(AnnualGradeSummary.enrollment_id.in_(enrollment_ids))
        .all()
    }
    # One context for the whole roster, and one query for every learner
    # name — the database is ~85ms away, so anything issued per learner
    # dominates the page.
    context = load_report_context(session, enrollments)
    learners = {
        l.id: l for l in session.query(Learner).filter(
            Learner.id.in_([e.learner_id for e in enrollments])
        ).all()
    }

    rows = []
    for enrollment in enrollments:
        learner = learners.get(enrollment.learner_id)
        row = {"Learner": f"{learner.last_name}, {learner.first_name}" if learner else "?"}
        if view == "Final":
            for subject in subjects:
                final = context.finals.get((enrollment.id, subject.id))
                row[subject.short_name] = _fmt(final.final_grade) if final else DASH
        else:
            term_number = VIEW_OPTIONS.index(view) + 1
            for subject in subjects:
                offering_id = context.offerings_by_subject.get(subject.id, {}).get(term_number)
                row[subject.short_name] = _fmt(
                    context.term_grades.get((enrollment.id, offering_id))
                    if offering_id is not None
                    else None
                )
        summary = summaries.get(enrollment.id)
        row["General Average"] = _fmt(summary.general_average) if summary else DASH
        row["Completion"] = summary.completion_status.value if summary else "not computed yet"
        rows.append(row)
    st.table(rows)


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")
    st.title("Grade Summary")
    st.caption(
        "Computed from what teachers have encoded in the Gradebook. It updates "
        "automatically when they save, or on demand with the Recompute button."
        if not current_user.is_read_only()
        else "Section summaries and finalized records. Read-only."
    )
    render_flashes()

    # Registrar/Super Admin see every section; an Adviser-only account
    # (no Registrar/Super Admin role) is scoped to sections they're the
    # actual adviser of, per §3C — not a school-wide view. A School Head
    # reviews section summaries school-wide (§3E), so they aren't scoped
    # either — they just can't change anything.
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

        section = section_picker(
            session, sy_choice, key="grade_summary",
            adviser_user_id=current_user.id if adviser_scoped else None,
        )
        if section is None:
            return
        section_choice = section.id

        enrollments = (
            session.query(Enrollment)
            .filter_by(section_id=section_choice, school_year_id=sy_choice)
            .join(Learner, Learner.id == Enrollment.learner_id)
            .order_by(*learner_order_by(Learner))
            .all()
        )
        if not enrollments:
            st.info("No learners enrolled in this section yet.")
            return

        if not current_user.is_read_only():
            if st.button("Recompute all in this section"):
                recompute_enrollment_grades_batch(session, [e.id for e in enrollments])
                flash("success", f"Recomputed {len(enrollments)} learner(s).")
                st.rerun()

        _class_summary(session, enrollments, section_choice, sy_choice)

        st.divider()
        st.subheader("Per-learner detail")
        # Load the roster's grade data once and hand the same context to
        # every learner's panel. Building it per learner cost ~12 queries
        # each, which at ~85ms per round trip is the difference between an
        # instant page and a forty-second one for a full section.
        detail_context = load_report_context(session, enrollments)
        panel = _panel_data(session, enrollments, sy_choice)
        learners = {
            l.id: l
            for l in session.query(Learner)
            .filter(Learner.id.in_([e.learner_id for e in enrollments]))
            .all()
        }
        for enrollment in enrollments:
            learner = learners.get(enrollment.learner_id)
            label = f"{learner.last_name}, {learner.first_name}" if learner else "?"
            with st.expander(label):
                _learner_detail(session, current_user, enrollment, detail_context, panel)
