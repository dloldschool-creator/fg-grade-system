from datetime import date

import streamlit as st
from sqlalchemy.exc import IntegrityError

from app.admin_pages._helpers import flash, get_session, render_flashes, stateful_tabs, try_commit
from app.auth import require_role
from app.models.academic_structure import Section
from app.models.enums import EnrollmentStatus
from app.models.learners import Enrollment, Learner, LearnerMovement
from app.models.organization import SchoolYear

RESULT_LIMIT = 30


def _section_query(session, school_year_id, adviser_user_id):
    query = session.query(Section).filter_by(school_year_id=school_year_id)
    if adviser_user_id is not None:
        query = query.filter_by(adviser_user_id=adviser_user_id)
    return query.order_by(Section.grade_level_id, Section.display_order).all()


def _enroll_learner_tab(session, adviser_user_id):
    school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
    if not school_years:
        st.warning("Create a school year first (School Years & Terms page).")
        return
    sy_by_id = {sy.id: sy for sy in school_years}
    sy_choice = st.selectbox(
        "School year", options=[sy.id for sy in school_years], format_func=lambda v: sy_by_id[v].name,
        key="enroll_sy",
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

    search = st.text_input("Search learner by name or LRN", key="enroll_search")
    query = session.query(Learner).order_by(Learner.last_name, Learner.first_name)
    if search:
        like = f"%{search}%"
        query = query.filter(
            (Learner.last_name.ilike(like))
            | (Learner.first_name.ilike(like))
            | (Learner.lrn.ilike(like))
        )
    learners = query.limit(RESULT_LIMIT).all()
    if not learners:
        st.info("No matching learners. Add one on the Learner Masterlist page first.")
        return
    learner_by_id = {learner.id: learner for learner in learners}

    with st.form("enroll_learner_form"):
        learner_choice = st.selectbox(
            "Learner",
            options=[learner.id for learner in learners],
            format_func=lambda v: f"{learner_by_id[v].last_name}, {learner_by_id[v].first_name} "
            f"(LRN: {learner_by_id[v].lrn or 'none'})",
        )
        section_choice = st.selectbox(
            "Section",
            options=[s.id for s in sections],
            format_func=lambda v: f"{section_by_id[v].name}",
        )
        if st.form_submit_button("Enroll"):
            section = section_by_id[section_choice]
            session.add(
                Enrollment(
                    learner_id=learner_choice,
                    school_year_id=sy_choice,
                    grade_level_id=section.grade_level_id,
                    section_id=section_choice,
                    enrollment_status=EnrollmentStatus.ENROLLED,
                )
            )
            try_commit(
                session,
                f"Enrolled {learner_by_id[learner_choice].last_name}, "
                f"{learner_by_id[learner_choice].first_name} in {section.name}.",
            )
            st.rerun()


def _bulk_enroll_tab(session, adviser_user_id):
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
    available_learners = learner_query.all()
    if not available_learners:
        st.info("Every learner is already enrolled for this school year.")
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


def _roster_tab(session, adviser_user_id):
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
        st.info("No learners enrolled in this section yet — use the Enroll Learner tab.")
        return

    for enrollment in enrollments:
        learner = session.get(Learner, enrollment.learner_id)
        with st.expander(
            f"{learner.last_name}, {learner.first_name} — {enrollment.enrollment_status.value}"
        ):
            with st.form(f"edit_enrollment_{enrollment.id}"):
                status = st.selectbox(
                    "Enrollment status",
                    options=[s.value for s in EnrollmentStatus],
                    index=[s.value for s in EnrollmentStatus].index(enrollment.enrollment_status.value),
                    key=f"enr_status_{enrollment.id}",
                )
                derogatory = st.checkbox(
                    "Derogatory record", value=enrollment.derogatory_record, key=f"enr_derog_{enrollment.id}"
                )
                general_remarks = st.text_area(
                    "General remarks", value=enrollment.general_remarks or "", key=f"enr_remarks_{enrollment.id}"
                )
                col1, col2, col3 = st.columns(3)
                t1 = col1.text_area("Term 1 adviser comment", value=enrollment.term1_adviser_comment or "", key=f"enr_t1_{enrollment.id}")
                t2 = col2.text_area("Term 2 adviser comment", value=enrollment.term2_adviser_comment or "", key=f"enr_t2_{enrollment.id}")
                t3 = col3.text_area("Term 3 adviser comment", value=enrollment.term3_adviser_comment or "", key=f"enr_t3_{enrollment.id}")

                if st.form_submit_button("Save"):
                    enrollment.enrollment_status = EnrollmentStatus(status)
                    enrollment.derogatory_record = derogatory
                    enrollment.general_remarks = general_remarks or None
                    enrollment.term1_adviser_comment = t1 or None
                    enrollment.term2_adviser_comment = t2 or None
                    enrollment.term3_adviser_comment = t3 or None
                    enrollment.version += 1
                    try_commit(session, "Saved.")
                    st.rerun()

            st.subheader("Movement / status history")
            movements = (
                session.query(LearnerMovement)
                .filter_by(enrollment_id=enrollment.id)
                .order_by(LearnerMovement.effective_date.desc())
                .all()
            )
            if movements:
                st.table(
                    [
                        {
                            "Date": m.effective_date.isoformat(),
                            "Type": m.movement_type.value,
                            "Details": m.details or "",
                            "Remarks": m.remarks or "",
                        }
                        for m in movements
                    ]
                )
            else:
                st.caption("No movements logged yet.")

            with st.form(f"add_movement_{enrollment.id}"):
                st.caption(
                    "Logging a movement also updates this learner's current enrollment status above "
                    "(§27, §32) — e.g. a transferred/dropped/NLS/shifted-out learner still appears in "
                    "the effective month's SF2 with a remark, then drops out of later months."
                )
                movement_type = st.selectbox(
                    "Movement type", options=[s.value for s in EnrollmentStatus], key=f"mv_type_{enrollment.id}"
                )
                effective_date = st.date_input(
                    "Effective date", value=date.today(), key=f"mv_date_{enrollment.id}"
                )
                details = st.text_input("Details", key=f"mv_details_{enrollment.id}")
                col1, col2 = st.columns(2)
                previous_school = col1.text_input("Previous school (if applicable)", key=f"mv_prev_{enrollment.id}")
                receiving_school = col2.text_input("Receiving school (if applicable)", key=f"mv_recv_{enrollment.id}")
                nls_reason = st.text_input("NLS reason (if applicable)", key=f"mv_nls_{enrollment.id}")
                remarks = st.text_input("Remarks", key=f"mv_remarks_{enrollment.id}")

                if st.form_submit_button("Log movement"):
                    session.add(
                        LearnerMovement(
                            enrollment_id=enrollment.id,
                            movement_type=EnrollmentStatus(movement_type),
                            effective_date=effective_date,
                            details=details or None,
                            previous_school=previous_school or None,
                            receiving_school=receiving_school or None,
                            nls_reason=nls_reason or None,
                            remarks=remarks or None,
                        )
                    )
                    enrollment.enrollment_status = EnrollmentStatus(movement_type)
                    enrollment.version += 1
                    try_commit(session, "Movement logged.")
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
        choice = stateful_tabs(
            "enrollment_tab", ["Enroll Learner", "Bulk Enroll", "Section Roster / Movements"]
        )
        if choice == "Enroll Learner":
            _enroll_learner_tab(session, adviser_user_id)
        elif choice == "Bulk Enroll":
            _bulk_enroll_tab(session, adviser_user_id)
        elif choice == "Section Roster / Movements":
            _roster_tab(session, adviser_user_id)
