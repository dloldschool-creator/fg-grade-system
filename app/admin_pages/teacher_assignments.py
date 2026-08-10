from datetime import datetime, timezone

import streamlit as st

from app.admin_pages._helpers import get_session, render_flashes, try_commit
from app.auth import require_role
from app.models.academic_structure import Section
from app.models.organization import SchoolYear, Term
from app.models.rbac import Role, User, UserRole
from app.models.subjects import SectionSubjectOffering, Subject, TeacherAssignment


def render() -> None:
    current_user = require_role("SUPER_ADMIN")
    st.title("Teacher Assignments")
    st.caption(
        "Teacher → Section → Subject → Term (§47). Reassigning deactivates the old "
        "assignment rather than deleting it, so who taught what stays auditable."
    )
    render_flashes()

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("Create a school year first.")
            return
        sy_by_id = {sy.id: sy for sy in school_years}
        sy_choice = st.selectbox(
            "School year", options=[sy.id for sy in school_years], format_func=lambda v: sy_by_id[v].name
        )

        sections = session.query(Section).filter_by(school_year_id=sy_choice).order_by(Section.name).all()
        if not sections:
            st.warning("No sections for this school year yet.")
            return
        section_by_id = {s.id: s for s in sections}
        section_choice = st.selectbox(
            "Section", options=[s.id for s in sections], format_func=lambda v: section_by_id[v].name
        )

        teachers = (
            session.query(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .filter(Role.code == "SUBJECT_TEACHER", User.is_active.is_(True))
            .order_by(User.full_name)
            .all()
        )
        if not teachers:
            st.info(
                "No users with the SUBJECT_TEACHER role yet — grant that role on the "
                "Users & Roles page first."
            )
            return
        teacher_by_id = {t.id: t for t in teachers}

        offerings = (
            session.query(SectionSubjectOffering)
            .filter_by(section_id=section_choice)
            .order_by(SectionSubjectOffering.term_id, SectionSubjectOffering.display_order)
            .all()
        )
        if not offerings:
            st.info("No subject offerings for this section yet — set those up on the Section Offerings page.")
            return

        subjects_by_id = {s.id: s for s in session.query(Subject).all()}
        terms = session.query(Term).filter_by(school_year_id=sy_choice).order_by(Term.term_number).all()
        term_by_id = {t.id: t for t in terms}

        for offering in offerings:
            subject = subjects_by_id[offering.subject_id]
            term = term_by_id[offering.term_id]
            active_assignment = (
                session.query(TeacherAssignment)
                .filter_by(section_subject_offering_id=offering.id, is_active=True)
                .one_or_none()
            )
            current_label = (
                teacher_by_id[active_assignment.teacher_user_id].full_name
                if active_assignment and active_assignment.teacher_user_id in teacher_by_id
                else "— unassigned —"
            )

            with st.form(f"assign_{offering.id}"):
                col1, col2, col3 = st.columns([3, 3, 2])
                col1.write(f"{term.name} — {subject.official_name}")
                default_index = (
                    [t.id for t in teachers].index(active_assignment.teacher_user_id)
                    if active_assignment and active_assignment.teacher_user_id in teacher_by_id
                    else 0
                )
                teacher_choice = col2.selectbox(
                    "Teacher",
                    options=[t.id for t in teachers],
                    index=default_index,
                    format_func=lambda v: teacher_by_id[v].full_name,
                    key=f"teacher_{offering.id}",
                    label_visibility="collapsed",
                )
                col3.caption(f"Current: {current_label}")

                col1, col2 = st.columns(2)
                assign = col1.form_submit_button("Assign")
                unassign = col2.form_submit_button("Unassign", disabled=active_assignment is None)

                if assign:
                    if active_assignment and active_assignment.teacher_user_id == teacher_choice:
                        st.info("Already assigned to that teacher.")
                    else:
                        now = datetime.now(timezone.utc)
                        if active_assignment:
                            active_assignment.is_active = False
                            active_assignment.unassigned_at = now
                        session.add(
                            TeacherAssignment(
                                section_subject_offering_id=offering.id,
                                teacher_user_id=teacher_choice,
                                assigned_by_user_id=current_user.id,
                                assigned_at=now,
                            )
                        )
                        try_commit(
                            session,
                            f"Assigned {teacher_by_id[teacher_choice].full_name} to "
                            f"{term.name} — {subject.official_name}.",
                        )
                        st.rerun()
                if unassign and active_assignment:
                    active_assignment.is_active = False
                    active_assignment.unassigned_at = datetime.now(timezone.utc)
                    try_commit(session, f"Unassigned {term.name} — {subject.official_name}.")
                    st.rerun()
