import json
from datetime import date

import streamlit as st
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import audit_service
from app.admin_pages._helpers import (
    _forget_stale,
    clear_text_fields,
    flash,
    flush_or_rollback,
    get_session,
    keep_panel_open,
    panel_is_open,
    render_flashes,
    stateful_tabs,
    text_field,
    try_commit,
)
from app.auth import require_role
from app.display_time import format_time
from app.enrollment_section_correction import CorrectionRefused, correct_section
from app.enrollment_withdrawal import (
    WithdrawalRefused,
    authorizer_options,
    load_record,
    withdraw_enrollment,
    withdrawal_history,
)
from app.enrollment_subject_overrides import load_overrides_by_enrollment
from app.finalized_month_changes import (
    apply_movement_change,
    lock_movements,
    movement_note,
    removal_note,
)
from app.grading_service import recompute_enrollment_grades_batch
from app.learner_access import editable_learner_ids
from app.models.academic_structure import Section
from app.models.enums import EnrollmentStatus, OfferingStatus
from app.models.learners import Enrollment, Learner, LearnerMovement
from app.models.organization import SchoolYear, Term
from app.models.rbac import User
from app.models.subjects import EnrollmentSubjectOverride, SectionSubjectOffering, Subject
from app.nls_reasons import NLS_REASONS
from app.text_rules import ADDRESS_MAX, NOTE_MAX, REASON_MAX, SHORT_MAX, clean_text, reason_problem


def _section_query(session, school_year_id, adviser_user_id):
    query = session.query(Section).filter_by(school_year_id=school_year_id)
    if adviser_user_id is not None:
        query = query.filter_by(adviser_user_id=adviser_user_id)
    return query.order_by(Section.grade_level_id, Section.display_order).all()


def _enroll_tab(session, adviser_user_id):
    st.caption(
        "Puts one or more learners into a section. They must already be in "
        "the system — add them on the Learner Masterlist first, one by one "
        "or from an Excel upload."
    )
    st.markdown(
        "1. Pick the school year and the section to enroll into.\n"
        "2. Pick every learner who belongs in that section. Type a name or "
        "LRN in the box to find someone quickly.\n"
        "3. Press **Enroll selected**.\n\n"
        "Only learners **not yet enrolled** for that school year are "
        "listed, so nobody can be enrolled twice. If a learner went into "
        "the wrong section, open them on the Section Roster tab and use "
        "**Correct section** — a movement is for a real change, not a "
        "mistake."
    )
    if adviser_user_id is not None:
        st.caption(
            "You see learners from your own sections and learners you added "
            "yourself. A learner who isn't listed is under another adviser or "
            "the registrar — ask the registrar to enroll them."
        )
    st.divider()

    school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
    if not school_years:
        st.warning("Create a school year first (School Years & Terms page).")
        return
    sy_by_id = {sy.id: sy for sy in school_years}
    sy_choice = st.selectbox(
        "School year", options=[sy.id for sy in school_years], format_func=lambda v: sy_by_id[v].name,
        key="bulk_enroll_sy",
    )

    sections = _section_query(session, sy_choice, adviser_user_id)
    if not sections:
        st.warning(
            "You're not the adviser of any section for this school year yet."
            if adviser_user_id
            else "No sections exist for this school year yet — create one on the Sections page."
        )
        return
    section_by_id = {s.id: s for s in sections}
    section_choice = st.selectbox(
        "Section to enroll into",
        options=[s.id for s in sections],
        format_func=lambda v: section_by_id[v].name,
        key="bulk_enroll_section",
    )

    already_enrolled_ids = {
        e.learner_id for e in session.query(Enrollment).filter_by(school_year_id=sy_choice).all()
    }
    learner_query = session.query(Learner).order_by(Learner.last_name, Learner.first_name)
    if already_enrolled_ids:
        learner_query = learner_query.filter(~Learner.id.in_(already_enrolled_ids))
    # An adviser enrols only learners they own (app/learner_access.py) — the
    # same rule as the Learner Masterlist, so the two pages can't disagree.
    if adviser_user_id is not None:
        owned = editable_learner_ids(session, adviser_user_id)
        if not owned:
            st.info("No learners of yours are waiting to be enrolled.")
            return
        learner_query = learner_query.filter(Learner.id.in_(owned))
    available_learners = learner_query.all()
    if not available_learners:
        st.info(
            "Every learner is already enrolled for this school year."
            if adviser_user_id is None
            else "None of your learners are waiting to be enrolled for this school year."
        )
        return
    learner_by_id = {learner.id: learner for learner in available_learners}

    with st.form("bulk_enroll_form"):
        st.caption(f"{len(available_learners)} learner(s) not yet enrolled in {sy_by_id[sy_choice].name}.")
        selected = st.multiselect(
            "Learners to enroll",
            options=[learner.id for learner in available_learners],
            format_func=lambda v: f"{learner_by_id[v].last_name}, {learner_by_id[v].first_name} "
            f"(LRN: {learner_by_id[v].lrn or 'none'})",
        )
        if st.form_submit_button(f"Enroll selected into {section_by_id[section_choice].name}"):
            if not selected:
                st.error("Select at least one learner.")
            else:
                section = section_by_id[section_choice]
                for learner_id in selected:
                    session.add(
                        Enrollment(
                            learner_id=learner_id,
                            school_year_id=sy_choice,
                            grade_level_id=section.grade_level_id,
                            section_id=section_choice,
                            enrollment_status=EnrollmentStatus.ENROLLED,
                        )
                    )
                try:
                    session.commit()
                    flash("success", f"Enrolled {len(selected)} learner(s) in {section.name}.")
                except IntegrityError:
                    session.rollback()
                    flash("error", "Couldn't enroll — one or more learners may already be enrolled.")
                st.rerun()


_RECORD_CHECK = "enrollment_record_check"

_GROUP_LABELS = {
    "deleted": "Deleted on withdrawal (copy kept)",
    "derived": "Computed — deleted, rebuilt by recompute",
    "blocks": "Blocks withdrawal",
}


def _months_text(months, finalized=()) -> str:
    finalized = set(finalized)
    return ", ".join(
        m.strftime("%b %Y") + (" (finalized)" if m in finalized else "") for m in months
    ) or "none"


def _record_panel(session, enrollment, checked, current_user, panel_id):
    """The record check for one learner, and — for a registrar or admin —
    the withdrawal form under it. Only ever drawn for the one learner whose
    Check record was pressed (app/enrollment_withdrawal.py)."""
    try:
        record = load_record(session, enrollment.id)
    except WithdrawalRefused as refusal:
        st.error(str(refusal))
        return
    # The version guarded on submit is the one the person last *saw*, not the
    # one from when Check record was pressed: a Save or Log movement in this
    # same panel bumps it and reruns, and the panel redraws with the new
    # state. So read the version the previous render showed, then record
    # this render's for the next submit — in that order, because a submit
    # is handled in the same run that redraws the panel, after it. Only a
    # change made elsewhere between seeing and submitting is refused
    # (rule 9). `checked` is the dict in session_state, so this persists.
    shown_version = checked["version"]
    checked["version"] = record.enrollment_version

    left, right = st.columns(2)
    left.markdown(
        f"**{record.name}**  \n"
        f"LRN {record.masked_lrn} · {record.sex.title()} · born {record.birthdate:%d %b %Y}  \n"
        f"Record created by {record.learner_created_by or 'the registrar (not tracked)'}"
    )
    right.markdown(
        f"**{record.section}** · {record.grade_level} · {record.school_year}  \n"
        f"{record.track} — {record.strand}  \n"
        f"Adviser: {record.adviser or 'none assigned'}  \n"
        f"Status **{record.status}** · enrolled {record.enrolled_on:%d %b %Y}"
    )

    for anomaly in record.anomalies:
        st.warning(anomaly)

    if record.movements:
        st.table(
            [
                {"Movement": kind, "Effective": when.isoformat(), "Reason / details": why}
                for kind, when, why in record.movements
            ]
        )
    marked = sum(record.attendance.values())
    st.markdown(
        f"**Attendance:** {marked} day(s) marked — "
        + (", ".join(f"{k.title()} {v}" for k, v in sorted(record.attendance.items())) or "none")
        + (f"; first marked {record.first_marked:%d %b %Y}" if record.first_marked else "")
        + (f"; last present {record.last_present:%d %b %Y}" if record.last_present else "")
        + "  \n"
        f"**Appears on SF2/SF4 for:** {_months_text(record.affected_months, record.finalized_months)}"
    )
    if record.grades_by_term:
        st.table(
            [
                {
                    "Term": term,
                    "Grade rows": sum(v for k, v in counts.items() if k not in ("offerings", "blank")),
                    "Blank": counts.get("blank", 0),
                    "Offerings": counts.get("offerings", 0),
                    "By status": ", ".join(
                        f"{k.title()} {v}"
                        for k, v in sorted(counts.items())
                        if k not in ("offerings", "blank")
                    ),
                }
                for term, counts in record.grades_by_term.items()
            ]
        )
    if record.tables:
        st.dataframe(
            [
                {
                    "Linked data": t.label,
                    "Rows": t.total,
                    "Breakdown": ", ".join(f"{k.title()} {v}" for k, v in sorted(t.by_status.items())),
                    "On withdrawal": _GROUP_LABELS[t.group],
                }
                for t in sorted(record.tables, key=lambda t: (t.group != "blocks", t.label))
            ],
            hide_index=True,
            width="stretch",
        )

    if st.button("Close record", key=f"close_record_{enrollment.id}"):
        st.session_state.pop(_RECORD_CHECK, None)
        keep_panel_open(panel_id)
        st.rerun()

    if not current_user.has_role("SUPER_ADMIN", "REGISTRAR"):
        return

    st.markdown("**Withdraw enrollment**")
    st.caption(
        "Removes this learner from the school year as if never enrolled — for a "
        "learner the school head has directed not to be enrolled in LIS. Not for a "
        "learner who left: log a Dropped, NLS or Transferred Out movement for that. "
        "The learner's own record stays, so they can be enrolled again later."
    )
    if record.blockers:
        st.error(
            "Can't be withdrawn yet — this learner has " + ", ".join(record.blockers) + ". "
            "Reopen finalized grades on Grade Summary and clear award overrides on "
            "Awards first; a frozen academic record needs the system administrator."
        )
        return

    authorizers = authorizer_options(session)
    if not authorizers:
        st.error("No school head is named — set one on School Info first.")
        return
    # Only what a person entered is named here; computed rows are rebuilt
    # anyway and would bury the numbers that matter.
    deleted = [t for t in record.tables if t.group == "deleted"]
    with st.form(f"withdraw_{enrollment.id}"):
        authorized_by = st.selectbox("Authorized by", options=authorizers)
        reason = st.text_area(
            "Reason — required",
            placeholder="e.g. Not enrolled in LIS per school head's directive",
            max_chars=REASON_MAX,
        )
        confirm = st.text_input(
            f"Type the learner's last name ({record.last_name}) to confirm", max_chars=SHORT_MAX
        )
        acknowledged = st.checkbox(
            "I understand this deletes "
            + ", ".join(f"{t.total} {t.label.lower()}" for t in deleted)
            + (", " if deleted else "")
            + "the enrollment and its computed averages. A full copy is kept in the audit log."
            + (
                f" Finalized attendance for {_months_text(record.finalized_months)} will be "
                "reopened and re-finalized automatically."
                if record.finalized_months
                else ""
            )
            + (
                f" SF2/SF4 for {_months_text(record.affected_months)} must be reprinted."
                if record.affected_months
                else ""
            )
        )
        if st.form_submit_button("Withdraw enrollment", type="primary"):
            if not acknowledged:
                flash("error", "Tick the box to confirm you've read what will be deleted.")
                keep_panel_open(panel_id)
                st.rerun()
            try:
                summary = withdraw_enrollment(
                    session,
                    enrollment_id=enrollment.id,
                    expected_version=shown_version,
                    user_id=current_user.id,
                    reason=reason,
                    authorized_by=authorized_by,
                    confirm_last_name=confirm,
                )
            except WithdrawalRefused as refusal:
                session.rollback()
                flash("error", str(refusal))
                keep_panel_open(panel_id)
            except SQLAlchemyError:
                # The deletes flush inside the call, so a lock timeout or an
                # unforeseen constraint surfaces here rather than at commit.
                session.rollback()
                flash(
                    "error",
                    "The withdrawal couldn't be completed and nothing was changed — "
                    "someone may be saving this learner's grades or attendance. "
                    "Try again in a moment.",
                )
                keep_panel_open(panel_id)
            else:
                months = summary["sf2_sf4_months_to_reprint"]
                if try_commit(
                    session,
                    f"Withdrew {record.name} from {record.section}."
                    + (f" Reprint SF2/SF4 for {', '.join(months)} — listed on the Withdrawn tab." if months else ""),
                ):
                    st.session_state.pop(_RECORD_CHECK, None)
            st.rerun()


def _months_awaiting_reprint(session, entries) -> dict:
    """entry id -> what is still owed on the SF2/SF4 months a withdrawal
    affected (§33). One query for every entry, not one each.

    Two kinds of month are listed. A month the withdrawal reopened and
    re-finalized is owed a reprint until someone confirms it
    (`reprint_pending`). A month that was *not* finalized at withdrawal was
    never reopened, so it has no flag — but an SF2 may well have been
    printed from it (the SF2 page allows that, with a warning), so it is
    shown as "(not finalized yet)" until it is finalized, when the first
    official print will already exclude the learner. A month with no
    status row was never started and owes nothing.
    """
    import uuid
    from datetime import date

    from app.attendance_service import reprint_pending
    from app.models.attendance import AttendanceMonthStatus
    from app.models.enums import FinalizationState

    def first_of(month_text):
        try:
            year, month = month_text.split("-")
            return date(int(year), int(month), 1)
        except ValueError:
            return None

    wanted = {}
    for e in entries:
        data = e.new_value or {}
        try:
            section_id = uuid.UUID(str(data.get("section_id")))
        except ValueError:
            continue
        months = [m for m in (data.get("sf2_sf4_months_to_reprint") or []) if first_of(m)]
        wanted[e.id] = (section_id, months)
    sections = {s for s, _ in wanted.values()}
    months_in_play = {first_of(m) for _, months in wanted.values() for m in months}
    if not sections or not months_in_play:
        return {entry_id: [] for entry_id in wanted}
    statuses = {
        (s.section_id, s.year_month.strftime("%Y-%m")): s
        for s in session.query(AttendanceMonthStatus).filter(
            AttendanceMonthStatus.section_id.in_(sections),
            AttendanceMonthStatus.year_month.in_(months_in_play),
        )
    }

    def owed(section_id, month):
        status = statuses.get((section_id, month))
        if status is None:
            return None
        if reprint_pending(status):
            return month
        if status.status != FinalizationState.FINALIZED:
            return f"{month} (not finalized yet)"
        return None

    return {
        entry_id: [o for o in (owed(section_id, m) for m in months) if o]
        for entry_id, (section_id, months) in wanted.items()
    }


def _withdrawn_tab(session):
    st.caption(
        "Every enrollment withdrawn so far, newest first, read from the audit log. "
        "*SF2/SF4 months affected* is what the withdrawal recorded: the copies that "
        "showed the learner. *Reprint not yet confirmed* is the live state: months "
        "the withdrawal reopened leave it when the adviser or registrar confirms the "
        "reprint on the Attendance & SF2 or SF2 page, and a month marked *not finalized "
        "yet* leaves it once finalized. An audit archive (Audit Log page) moves entries older "
        "than its cutoff into the export file — a withdrawal archived that way "
        "leaves this list, and the export file then holds its copy."
    )
    entries = withdrawal_history(session)
    if not entries:
        st.info("No enrollments have been withdrawn.")
        return
    user_ids = {e.user_id for e in entries if e.user_id}
    names = (
        {u.id: u.full_name for u in session.query(User).filter(User.id.in_(user_ids)).all()}
        if user_ids
        else {}
    )
    still_to_confirm = _months_awaiting_reprint(session, entries)
    st.dataframe(
        [
            {
                "When": format_time(e.created_at),
                "Learner": (e.new_value or {}).get("learner"),
                "LRN": "••••" + ((e.new_value or {}).get("lrn_last4") or "—"),
                "Section": (e.new_value or {}).get("section"),
                "Adviser": (e.new_value or {}).get("adviser"),
                "Authorized by": (e.new_value or {}).get("authorized_by"),
                "Withdrawn by": names.get(e.user_id, "—"),
                "Reason": e.reason,
                "SF2/SF4 months affected": ", ".join(
                    (e.new_value or {}).get("sf2_sf4_months_to_reprint", [])
                ),
                # The live state, from the month's own flag — the column above is
                # what the withdrawal recorded and never changes.
                "Reprint not yet confirmed": ", ".join(still_to_confirm.get(e.id, [])),
                "Re-finalized": ", ".join((e.new_value or {}).get("months_reopened_and_refinalized", [])),
            }
            for e in entries
        ],
        hide_index=True,
        width="stretch",
    )
    by_id = {str(e.id): e for e in entries}
    picked = st.selectbox(
        "See what was removed",
        options=list(by_id),
        format_func=lambda v: f"{(by_id[v].new_value or {}).get('learner')} — {format_time(by_id[v].created_at)}",
    )
    entry = by_id[picked]
    rows = (entry.previous_value or {}).get("rows", {})
    st.table([{"Table": table, "Rows copied": len(copied)} for table, copied in rows.items()])
    st.download_button(
        "Download the full copy (JSON)",
        data=json.dumps(
            {"withdrawal": entry.new_value, "reason": entry.reason, "copy": entry.previous_value},
            indent=2,
        ),
        file_name=f"withdrawn-{(entry.new_value or {}).get('learner', 'learner')}.json",
        mime="application/json",
    )


def _roster_tab(session, adviser_user_id, current_user):
    school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
    if not school_years:
        return
    sy_by_id = {sy.id: sy for sy in school_years}
    sy_choice = st.selectbox(
        "School year", options=[sy.id for sy in school_years], format_func=lambda v: sy_by_id[v].name,
        key="roster_sy",
    )

    sections = _section_query(session, sy_choice, adviser_user_id)
    if not sections:
        st.info(
            "You're not the adviser of any section for this school year yet."
            if adviser_user_id
            else "No sections for this school year yet."
        )
        return
    section_by_id = {s.id: s for s in sections}
    section_choice = st.selectbox(
        "Section", options=[s.id for s in sections], format_func=lambda v: section_by_id[v].name,
        key="roster_section",
    )

    enrollments = (
        session.query(Enrollment)
        .filter_by(school_year_id=sy_choice, section_id=section_choice)
        .join(Learner, Learner.id == Enrollment.learner_id)
        .order_by(Learner.last_name, Learner.first_name)
        .all()
    )
    if not enrollments:
        st.info("No learners enrolled in this section yet — use the Enroll tab.")
        return

    # Both lookups batched above the loop: an expander's body runs whether
    # or not it is open, so every panel's learner and movement history was
    # a round trip each on every rerun.
    learners = {
        learner.id: learner
        for learner in session.query(Learner)
        .filter(Learner.id.in_([e.learner_id for e in enrollments]))
        .all()
    }
    movements_by_enrollment: dict = {}
    for movement in (
        session.query(LearnerMovement)
        .filter(LearnerMovement.enrollment_id.in_([e.id for e in enrollments]))
        .order_by(LearnerMovement.effective_date.desc())
        .all()
    ):
        movements_by_enrollment.setdefault(movement.enrollment_id, []).append(movement)

    # Subject substitutions (app/enrollment_subject_overrides.py) — every
    # learner here shares this one section and school year, so the
    # candidate offering lists for the add-override form are the same for
    # all of them and are loaded once, not per learner. `year_offerings`
    # covers both: an original is always one of this section's own
    # offerings (a subset of it), a substitute can be any of them.
    overrides_by_enrollment = load_overrides_by_enrollment(session, [e.id for e in enrollments])
    year_offerings = (
        session.query(SectionSubjectOffering).filter_by(school_year_id=sy_choice).all()
    )
    year_offering_by_id = {o.id: o for o in year_offerings}
    section_offerings = [o for o in year_offerings if o.section_id == section_choice]
    subject_by_id = {
        s.id: s
        for s in session.query(Subject)
        .filter(Subject.id.in_({o.subject_id for o in year_offerings}))
        .all()
    } if year_offerings else {}
    term_by_id = {
        t.id: t
        for t in session.query(Term)
        .filter(Term.id.in_({o.term_id for o in year_offerings}))
        .all()
    } if year_offerings else {}
    override_section_ids = {o.section_id for o in year_offerings} - {section_choice}
    other_section_by_id = {
        s.id: s
        for s in session.query(Section).filter(Section.id.in_(override_section_ids)).all()
    } if override_section_ids else {}

    def _offering_label(offering) -> str:
        subject = subject_by_id.get(offering.subject_id)
        term = term_by_id.get(offering.term_id)
        label = f"{subject.official_name if subject else '?'} — {term.name if term else '?'}"
        if offering.section_id != section_choice:
            other_section = other_section_by_id.get(offering.section_id)
            label += f" ({other_section.name if other_section else 'another section'})"
        return label

    for enrollment in enrollments:
        learner = learners.get(enrollment.learner_id)
        if learner is None:
            continue
        panel_id = f"enrollment_{enrollment.id}"
        with st.expander(
            f"{learner.last_name}, {learner.first_name} — {enrollment.enrollment_status.value}",
            expanded=panel_is_open(panel_id),
        ):
            # Read-only by design (2026-09-05): an editable dropdown here
            # used to set enrollment_status directly with no date, reason,
            # or history row — a second path to the same field as "Log
            # movement" below that skipped §27's history requirement, and
            # would silently leave SF9's exit-status line blank since that
            # reads LearnerMovement, not this field. "Log movement" is the
            # only way to change it now, and already keeps it in sync.
            st.caption(f"Enrollment status: **{enrollment.enrollment_status.value}** — changed only by logging a movement below.")
            with st.form(f"edit_enrollment_{enrollment.id}"):
                derogatory = st.checkbox(
                    "Derogatory record", value=enrollment.derogatory_record, key=f"enr_derog_{enrollment.id}"
                )
                general_remarks = st.text_area(
                    "General remarks",
                    value=enrollment.general_remarks or "",
                    key=f"enr_remarks_{enrollment.id}",
                    max_chars=NOTE_MAX,
                )
                col1, col2, col3 = st.columns(3)
                t1 = col1.text_area("Term 1 adviser comment", value=enrollment.term1_adviser_comment or "", key=f"enr_t1_{enrollment.id}", max_chars=NOTE_MAX)
                t2 = col2.text_area("Term 2 adviser comment", value=enrollment.term2_adviser_comment or "", key=f"enr_t2_{enrollment.id}", max_chars=NOTE_MAX)
                t3 = col3.text_area("Term 3 adviser comment", value=enrollment.term3_adviser_comment or "", key=f"enr_t3_{enrollment.id}", max_chars=NOTE_MAX)

                if st.form_submit_button("Save"):
                    enrollment.derogatory_record = derogatory
                    enrollment.general_remarks = general_remarks or None
                    enrollment.term1_adviser_comment = t1 or None
                    enrollment.term2_adviser_comment = t2 or None
                    enrollment.term3_adviser_comment = t3 or None
                    enrollment.version += 1
                    try_commit(session, "Saved.")
                    st.rerun()

            st.subheader("Learner record")
            checked = st.session_state.get(_RECORD_CHECK)
            if checked and checked["enrollment_id"] == str(enrollment.id):
                _record_panel(session, enrollment, checked, current_user, panel_id)
            else:
                st.caption(
                    "Everything linked to this learner's enrollment — section, adviser, "
                    "attendance, grades, movements — and any problems found in it."
                )
                # Behind a button: the check costs ~10 round trips, and this
                # body runs for every learner on every rerun.
                if st.button("Check record", key=f"check_record_{enrollment.id}"):
                    st.session_state[_RECORD_CHECK] = {
                        "enrollment_id": str(enrollment.id),
                        "version": enrollment.version,
                    }
                    keep_panel_open(panel_id)
                    st.rerun()

            st.subheader("Correct section")
            st.caption(
                "For a learner enrolled in the wrong section by mistake. Not for a "
                "real change — log a movement for that. Attendance moves with the "
                "learner; refused once they have grades or subject substitutions, "
                "or a month of their attendance is finalized."
            )
            other_sections = [s for s in sections if s.id != enrollment.section_id]
            if not other_sections:
                st.caption("There is no other section you can move them to.")
            else:
                with st.form(f"correct_section_{enrollment.id}"):
                    target_id = st.selectbox(
                        "Correct section",
                        options=[s.id for s in other_sections],
                        format_func=lambda v: section_by_id[v].name,
                        key=f"correct_section_to_{enrollment.id}",
                    )
                    correction_reason = st.text_input(
                        "Reason — required",
                        key=f"correct_section_reason_{enrollment.id}",
                        max_chars=REASON_MAX,
                    )
                    if st.form_submit_button("Move to this section"):
                        try:
                            new_section = correct_section(
                                session,
                                enrollment=enrollment,
                                expected_section_id=section_choice,
                                new_section_id=target_id,
                                user_id=current_user.id,
                                reason=correction_reason,
                                allowed_section_ids=(
                                    None if adviser_user_id is None else {s.id for s in sections}
                                ),
                            )
                        except CorrectionRefused as refusal:
                            session.rollback()
                            flash("error", str(refusal))
                        else:
                            try_commit(
                                session,
                                f"Moved {learner.last_name}, {learner.first_name} to {new_section.name}.",
                            )
                        st.rerun()

            st.subheader("Movement / status history")
            movements = movements_by_enrollment.get(enrollment.id, [])
            # Registrar-only, same as every other delete in this app
            # (§54) — a movement drives SF2's counts and SF9's exit line,
            # so removing one wrongly is a bigger mistake than an
            # adviser's own section usually needs to make.
            may_delete_movement = current_user.has_role("SUPER_ADMIN", "REGISTRAR")
            if not movements:
                st.caption("No movements logged yet.")
            elif not may_delete_movement:
                st.table(
                    [
                        {
                            "Date": m.effective_date.isoformat(),
                            "Type": m.movement_type.value,
                            "NLS reason": m.nls_reason or "",
                            "Details": m.details or "",
                            "Remarks": m.remarks or "",
                        }
                        for m in movements
                    ]
                )
            else:
                rows = [
                    {
                        "Remove": False,
                        "Date": m.effective_date.isoformat(),
                        "Type": m.movement_type.value,
                        "NLS reason": m.nls_reason or "",
                        "Details": m.details or "",
                        "Remarks": m.remarks or "",
                    }
                    for m in movements
                ]
                edited = st.data_editor(
                    rows,
                    key=f"movement_history_{enrollment.id}",
                    hide_index=True,
                    disabled=["Date", "Type", "NLS reason", "Details", "Remarks"],
                    column_config={
                        "Remove": st.column_config.CheckboxColumn(
                            "Remove", help="Tick a duplicate or mis-logged entry, then remove it below."
                        ),
                    },
                    on_change=keep_panel_open,
                    args=(panel_id,),
                )
                to_remove = [m for m, row in zip(movements, edited) if row["Remove"]]
                if to_remove:
                    reason_key = f"movement_delete_reason_{enrollment.id}"
                    reason = st.text_input(
                        f"Reason for removing {len(to_remove)} ticked movement(s) — required",
                        key=reason_key,
                        max_chars=REASON_MAX,
                        on_change=keep_panel_open,
                        args=(panel_id,),
                    )
                    if st.button(
                        f"Remove {len(to_remove)} ticked movement(s)",
                        key=f"movement_delete_btn_{enrollment.id}",
                    ):
                        problem = reason_problem(reason)
                        if problem:
                            flash("error", problem)
                        else:
                            # Before any write: locks the learner and reads
                            # the log as it is now, not as this page rendered.
                            log_before = lock_movements(session, enrollment)
                            for m in to_remove:
                                # Recorded before the row goes, and rolled
                                # back with it if the delete is refused —
                                # same rule as Learners' own Delete.
                                audit_service.record(
                                    session,
                                    action=audit_service.LEARNER_MOVEMENT_DELETED,
                                    object_type="learner_movements",
                                    object_id=m.id,
                                    user_id=current_user.id,
                                    previous={
                                        "movement_type": m.movement_type,
                                        "effective_date": m.effective_date,
                                        "nls_reason": m.nls_reason,
                                        "details": m.details,
                                        "remarks": m.remarks,
                                    },
                                    new=None,
                                    reason=clean_text(reason),
                                )
                                session.delete(m)
                            # enrollment_status isn't derived on read — "Log
                            # movement" sets it directly — so removing the
                            # movement that set it would otherwise leave a
                            # status with no movement behind it. Recompute
                            # from whichever movement is now the most
                            # recent, or back to ENROLLED (the model's own
                            # default) if none are left.
                            remaining = [m for m in movements if m not in to_remove]
                            enrollment.enrollment_status = (
                                max(remaining, key=lambda m: (m.effective_date, m.created_at)).movement_type
                                if remaining
                                else EnrollmentStatus.ENROLLED
                            )
                            enrollment.version += 1
                            # Flushed inside the friendly-error handling, so
                            # the queries below can't raise it as a traceback.
                            if not flush_or_rollback(session):
                                st.rerun()
                            effect = apply_movement_change(
                                session,
                                enrollment,
                                before=log_before,
                                changed=to_remove,
                                user_id=current_user.id,
                                note=removal_note(learner, to_remove, clean_text(reason)),
                            )
                            if try_commit(session, f"Removed {len(to_remove)} movement(s).") and effect.message():
                                flash("warning", effect.message())
                        st.rerun()

            st.caption(
                "Logging a movement also updates the learner's status above. They "
                "still appear on that month's SF2 with a remark, then drop off the "
                "months after it."
            )
            st.caption(
                "**NLS and Dropped** share one line on SF2's summary box — the "
                "official form counts them together — and both need a reason from "
                "the SF2 form's own Legend 2: pick the main cause, then the "
                "specific reason under it (or \"Others\" to type your own). That "
                "reason then prints as \"*<Status> as of <date> due to <main "
                "cause> - <reason>*\" on both SF2's Remarks column and SF9's "
                "exit-status line — so pick the closest match rather than typing "
                "free text."
            )
            st.caption(
                "**Transferred In** needs the Previous school (Receiving school "
                "doesn't apply and is disabled); **Transferred Out** needs the "
                "Receiving school instead. These print as \"*Transferred In as of "
                "<date> from <school>*\" and \"*Transferred Out as of <date> to "
                "<school>*\" on SF2's Remarks column, per the form's own header."
            )
            # Movement type, and the NLS/Dropped reason cascade, sit
            # outside the form: a widget inside `st.form` only reports its
            # new value once the form is submitted, so a sub-reason list
            # couldn't react to a main-reason change made in the same
            # interaction. `_forget_stale` drops a stored choice the
            # current options no longer contain — same pattern as
            # `section_filters`'s grade/strand cascade. A live widget in an
            # expander needs on_change=keep_panel_open or its own rerun
            # collapses the panel mid-pick (st.expander has no memory) —
            # same as the subject-substitution picker below.
            movement_type = st.selectbox(
                "Movement type",
                options=[s.value for s in EnrollmentStatus],
                key=f"mv_type_{enrollment.id}",
                on_change=keep_panel_open,
                args=(panel_id,),
            )
            is_nls_or_dropped = movement_type in (EnrollmentStatus.NLS.value, EnrollmentStatus.DROPPED.value)
            is_transferred_in = movement_type == EnrollmentStatus.TRANSFERRED_IN.value
            is_transferred_out = movement_type == EnrollmentStatus.TRANSFERRED_OUT.value

            main_reason = sub_reason = None
            if is_nls_or_dropped:
                main_key = f"mv_reason_main_{enrollment.id}"
                sub_key = f"mv_reason_sub_{enrollment.id}"
                main_options = list(NLS_REASONS.keys())
                _forget_stale(main_key, main_options)
                main_reason = st.selectbox(
                    "NLS/Dropped reason — main cause (SF2 Legend 2)",
                    options=main_options,
                    key=main_key,
                    on_change=keep_panel_open,
                    args=(panel_id,),
                )
                sub_options = NLS_REASONS[main_reason]
                if sub_options:
                    _forget_stale(sub_key, sub_options)
                    sub_reason = st.selectbox(
                        "Specific reason",
                        options=sub_options,
                        key=sub_key,
                        on_change=keep_panel_open,
                        args=(panel_id,),
                    )
                else:
                    # "Others (Specify)" carries no fixed sub-reason on the
                    # template itself.
                    sub_reason = st.text_input(
                        "Specify reason",
                        key=sub_key,
                        max_chars=SHORT_MAX,
                        on_change=keep_panel_open,
                        args=(panel_id,),
                    )

            with st.form(f"add_movement_{enrollment.id}"):
                effective_date = st.date_input(
                    "Effective date", value=date.today(), key=f"mv_date_{enrollment.id}"
                )
                # One movement form per enrollment, so the form name — and
                # with it everything clear_text_fields touches — is scoped
                # to this learner.
                movement_form = f"add_movement_{enrollment.id}"
                details = None
                if not is_nls_or_dropped:
                    details = text_field("Details", key=f"{movement_form}.details", max_chars=NOTE_MAX)
                col1, col2 = st.columns(2)
                previous_school = text_field(
                    "Previous school" + (" (required)" if is_transferred_in else " (if applicable)"),
                    key=f"{movement_form}.previous_school",
                    container=col1,
                    max_chars=ADDRESS_MAX,
                    disabled=is_transferred_out,
                )
                receiving_school = text_field(
                    "Receiving school" + (" (required)" if is_transferred_out else " (if applicable)"),
                    key=f"{movement_form}.receiving_school",
                    container=col2,
                    max_chars=ADDRESS_MAX,
                    disabled=is_transferred_in,
                )
                remarks = text_field("Remarks", key=f"{movement_form}.remarks", max_chars=NOTE_MAX)

                if st.form_submit_button("Log movement"):
                    error = None
                    if is_transferred_in and not (previous_school or "").strip():
                        error = "Previous school is required for a Transferred In movement."
                    elif is_transferred_out and not (receiving_school or "").strip():
                        error = "Receiving school is required for a Transferred Out movement."

                    if error:
                        flash("error", error)
                    else:
                        previous_status = enrollment.enrollment_status
                        nls_reason = main_reason if is_nls_or_dropped else None
                        stored_details = sub_reason if is_nls_or_dropped else details
                        # The disabled field is inapplicable to this
                        # movement type, so whatever it's still holding
                        # from an earlier pick is dropped rather than
                        # stored — a Transferred In row must never carry
                        # a Receiving school, and vice versa.
                        stored_previous_school = None if is_transferred_out else (previous_school or None)
                        stored_receiving_school = None if is_transferred_in else (receiving_school or None)
                        log_before = lock_movements(session, enrollment)
                        new_movement = LearnerMovement(
                            enrollment_id=enrollment.id,
                            movement_type=EnrollmentStatus(movement_type),
                            effective_date=effective_date,
                            details=stored_details or None,
                            previous_school=stored_previous_school,
                            receiving_school=stored_receiving_school,
                            nls_reason=nls_reason,
                            remarks=remarks or None,
                        )
                        session.add(new_movement)
                        enrollment.enrollment_status = EnrollmentStatus(movement_type)
                        enrollment.version += 1
                        # §50 names "learner transferred" and "learner
                        # dropped" specifically; both arrive through this
                        # one form, so every movement type is logged
                        # rather than a subset.
                        audit_service.record(
                            session,
                            action=audit_service.LEARNER_MOVEMENT_RECORDED,
                            object_type="enrollments",
                            object_id=enrollment.id,
                            user_id=current_user.id,
                            previous={"enrollment_status": previous_status},
                            new={
                                "enrollment_status": movement_type,
                                "effective_date": effective_date,
                                "receiving_school": stored_receiving_school,
                                "previous_school": stored_previous_school,
                            },
                            reason=(
                                f"{nls_reason} - {stored_details}"
                                if nls_reason and stored_details
                                else nls_reason or stored_details or remarks or None
                            ),
                        )
                        # A date reaching back into a finalized month changes
                        # that month's SF2/SF4 — flag it for reprint.
                        if not flush_or_rollback(session):
                            st.rerun()
                        effect = apply_movement_change(
                            session,
                            enrollment,
                            before=log_before,
                            changed=[new_movement],
                            user_id=current_user.id,
                            note=movement_note(
                                "logged", learner, new_movement.movement_type, effective_date
                            ),
                        )
                        if try_commit(session, "Movement logged."):
                            clear_text_fields(movement_form)
                            if effect.message():
                                flash("warning", effect.message())
                    st.rerun()

            st.subheader("Subject substitutions")
            st.caption(
                "For an irregular learner taking a different elective in place of "
                "one of this section's usual subjects — for one or more terms. The "
                "learner's Term/General Average and report card use the substitute "
                "subject instead of the original for every term listed here."
            )
            existing_overrides = overrides_by_enrollment.get(enrollment.id, [])
            if existing_overrides:
                for ov in existing_overrides:
                    original = year_offering_by_id.get(ov.original_section_subject_offering_id)
                    substitute = year_offering_by_id.get(ov.substitute_section_subject_offering_id)
                    st.write(
                        f"**{_offering_label(original) if original else '?'}** → "
                        f"**{_offering_label(substitute) if substitute else '?'}** — {ov.reason}"
                    )
                    with st.form(f"remove_override_{ov.id}"):
                        remove_reason = st.text_area(
                            "Reason for removing",
                            key=f"remove_override_reason_{ov.id}",
                            max_chars=REASON_MAX,
                        )
                        if st.form_submit_button("Remove this substitution"):
                            problem = reason_problem(remove_reason)
                            if problem:
                                st.error(problem)
                            else:
                                audit_service.record(
                                    session,
                                    action=audit_service.SUBJECT_OVERRIDE_REMOVED,
                                    object_type="enrollment_subject_overrides",
                                    object_id=ov.id,
                                    user_id=current_user.id,
                                    previous={
                                        "original_section_subject_offering_id": ov.original_section_subject_offering_id,
                                        "substitute_section_subject_offering_id": ov.substitute_section_subject_offering_id,
                                    },
                                    new=None,
                                    reason=clean_text(remove_reason),
                                )
                                session.delete(ov)
                                if try_commit(session, "Substitution removed."):
                                    recompute_enrollment_grades_batch(session, [enrollment.id])
                                st.rerun()
            else:
                st.caption("No substitutions for this learner.")

            st.markdown("**Add a subject substitution**")
            if not section_offerings:
                st.caption("This section has no offerings yet.")
            else:
                # Outside the form below so picking one triggers an
                # immediate rerun — the substitute list depends on which
                # term the chosen original belongs to. That rerun would
                # otherwise collapse this very panel (st.expander has no
                # memory), taking the in-progress pick with it — hence
                # expanded=panel_is_open(...) above and on_change here.
                original_choice = st.selectbox(
                    "Original subject (this section's usual subject they're excused from)",
                    options=[o.id for o in section_offerings],
                    format_func=lambda v: _offering_label(year_offering_by_id[v]),
                    key=f"override_original_{enrollment.id}",
                    on_change=keep_panel_open,
                    args=(panel_id,),
                )
                original_offering = year_offering_by_id.get(original_choice)
                substitute_options = [
                    o.id
                    for o in year_offerings
                    if original_offering is not None
                    and o.term_id == original_offering.term_id
                    and o.id != original_choice
                    and o.status != OfferingStatus.PLACEHOLDER
                ]
                with st.form(f"add_override_{enrollment.id}"):
                    if not substitute_options:
                        st.caption("No other usable offering exists for that term yet.")
                        substitute_choice = None
                    else:
                        substitute_choice = st.selectbox(
                            "Substitute subject (what they take instead, that same term)",
                            options=substitute_options,
                            format_func=lambda v: _offering_label(year_offering_by_id[v]),
                            key=f"override_substitute_{enrollment.id}",
                        )
                    override_reason = st.text_area(
                        "Reason",
                        key=f"override_reason_{enrollment.id}",
                        placeholder="e.g. already passed this subject at their previous school",
                        max_chars=REASON_MAX,
                    )
                    if st.form_submit_button("Add substitution"):
                        if substitute_choice is None:
                            st.error("No substitute offering available for that term.")
                        elif problem := reason_problem(override_reason):
                            st.error(problem)
                        else:
                            override = EnrollmentSubjectOverride(
                                enrollment_id=enrollment.id,
                                original_section_subject_offering_id=original_choice,
                                substitute_section_subject_offering_id=substitute_choice,
                                reason=clean_text(override_reason),
                                created_by_user_id=current_user.id,
                            )
                            session.add(override)
                            try:
                                # A duplicate (this original slot already
                                # has a substitution) raises here, before
                                # any audit row is written for a change
                                # that didn't actually happen.
                                session.flush()
                            except IntegrityError:
                                session.rollback()
                                flash(
                                    "error",
                                    "That subject already has a substitution for this "
                                    "learner — remove it first.",
                                )
                                st.rerun()
                            audit_service.record(
                                session,
                                action=audit_service.SUBJECT_OVERRIDE_CREATED,
                                object_type="enrollment_subject_overrides",
                                object_id=override.id,
                                user_id=current_user.id,
                                previous=None,
                                new={
                                    "original_section_subject_offering_id": original_choice,
                                    "substitute_section_subject_offering_id": substitute_choice,
                                },
                                reason=clean_text(override_reason),
                            )
                            if try_commit(session, "Substitution added."):
                                recompute_enrollment_grades_batch(session, [enrollment.id])
                            st.rerun()


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER")
    st.title("Enrollment")
    render_flashes()

    # Registrar/Super Admin manage enrollment school-wide; an Adviser-only
    # account is scoped to sections they actually advise (§3C — DepEd
    # advisers pick up registrar-adjacent duties for their own section).
    adviser_user_id = None if current_user.has_role("SUPER_ADMIN", "REGISTRAR") else current_user.id

    with get_session() as session:
        tabs = ["Enroll", "Section Roster / Movements"]
        if adviser_user_id is None:
            tabs.append("Withdrawn")
        choice = stateful_tabs("enrollment_tab", tabs)
        if choice == "Enroll":
            _enroll_tab(session, adviser_user_id)
        elif choice == "Section Roster / Movements":
            _roster_tab(session, adviser_user_id, current_user)
        elif choice == "Withdrawn":
            _withdrawn_tab(session)
