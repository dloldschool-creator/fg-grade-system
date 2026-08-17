import streamlit as st

from app.admin_pages._helpers import (
    get_session,
    render_flashes,
    section_picker,
    try_commit,
)
from app.auth import require_role
from app.models.organization import SchoolYear
from app.models.rbac import Role, User, UserRole
from app.teacher_assignment_service import (
    assign_subject,
    load_section_subjects,
    may_assign,
    unassign_subject,
)

UNASSIGNED = "— unassigned —"


def _teacher_options(session) -> list:
    return (
        session.query(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .filter(Role.code == "SUBJECT_TEACHER", User.is_active.is_(True))
        .order_by(User.full_name)
        .all()
    )


def render() -> None:
    # An adviser may assign within their own section (§3C). They are not
    # assigning *themselves*: the Gradebook grants encoding rights from
    # this table, so self-service would let a teacher grant themselves a
    # roster. Granting stays a decision someone else makes.
    current_user = require_role("SUPER_ADMIN", "ADVISER")
    is_admin = current_user.has_role("SUPER_ADMIN")

    st.title("Teacher Assignments")
    st.caption(
        "Name the teacher for a subject and it applies to every term that subject "
        "runs. Reassigning keeps the old assignment on record, so who taught what "
        "is never lost."
    )
    render_flashes()

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("Create a school year first.")
            return
        sy_by_id = {sy.id: sy for sy in school_years}
        sy_choice = st.selectbox(
            "School year",
            options=[sy.id for sy in school_years],
            format_func=lambda v: sy_by_id[v].name,
        )

        section = section_picker(
            session,
            sy_choice,
            key="teacher_assignments",
            adviser_user_id=None if is_admin else current_user.id,
            empty_message=(
                None
                if is_admin
                else "You're not the adviser of any section in that school year."
            ),
        )
        if section is None:
            return

        # Belt and braces: the picker already scoped the list, but the
        # permission is checked against the section that came back rather
        # than trusted from how it was chosen.
        if not may_assign(section, current_user):
            st.error("You can only assign teachers in a section you advise.")
            return

        teachers = _teacher_options(session)
        if not teachers:
            st.info(
                "No users with the SUBJECT_TEACHER role yet — grant that role on the "
                "Users & Roles page first."
            )
            return
        teacher_by_id = {str(t.id): t for t in teachers}
        options = [UNASSIGNED] + [str(t.id) for t in teachers]

        rows = load_section_subjects(session, section.id, sy_choice)
        if not rows:
            st.info(
                "No subject offerings for this section yet — those are set up on the "
                "Section Subject Offerings page."
            )
            return

        unassigned = sum(1 for row in rows if row.is_unassigned)
        st.write(
            f"**{section.name}** — {len(rows)} subject(s), "
            + (f"**{unassigned} without a teacher**." if unassigned else "all assigned.")
        )

        for row in rows:
            terms = "".join(str(n) for n in row.term_numbers)
            with st.form(f"assign_{row.subject.id}"):
                col1, col2, col3 = st.columns([4, 4, 2])
                col1.write(f"**{row.subject.official_name}**")
                col1.caption(
                    f"Term{'s' if len(row.term_numbers) > 1 else ''} {terms}"
                    f"  ·  {len(row.offerings)} offering(s)"
                )

                sole = row.sole_teacher_id
                index = options.index(sole) if sole in options else 0
                choice = col2.selectbox(
                    "Teacher",
                    options=options,
                    index=index,
                    format_func=lambda v: v if v == UNASSIGNED else teacher_by_id[v].full_name,
                    key=f"teacher_{row.subject.id}",
                    label_visibility="collapsed",
                )

                if row.is_split:
                    # Legal, and picking one to display would hide it.
                    names = ", ".join(
                        sorted(
                            teacher_by_id[t].full_name
                            for t in row.holders
                            if t in teacher_by_id
                        )
                    )
                    col3.caption(f"Split across terms: {names}")
                elif sole:
                    col3.caption(f"Current: {teacher_by_id[sole].full_name}")
                else:
                    col3.caption("Current: none")

                col1, col2 = st.columns(2)
                save = col1.form_submit_button("Assign")
                clear = col2.form_submit_button(
                    "Unassign", disabled=row.is_unassigned
                )

                if save:
                    if choice == UNASSIGNED:
                        st.warning("Pick a teacher, or use Unassign.")
                    else:
                        written, replaced = assign_subject(
                            session,
                            row,
                            choice,
                            actor_user_id=current_user.id,
                            section=section,
                        )
                        if not written:
                            st.info("Already assigned to that teacher.")
                        else:
                            message = (
                                f"{teacher_by_id[choice].full_name} now teaches "
                                f"{row.subject.official_name} ({written} term"
                                f"{'s' if written != 1 else ''})."
                            )
                            if replaced:
                                message += " The previous assignment was kept on record."
                            # try_commit, not a bare commit: two people
                            # assigning the same offering at once is an
                            # IntegrityError from the partial unique index,
                            # and it should read as a message rather than
                            # crash the page.
                            if try_commit(session, message):
                                st.rerun()
                if clear:
                    cleared = unassign_subject(
                        session, row, actor_user_id=current_user.id, section=section
                    )
                    if cleared and try_commit(
                        session,
                        f"{row.subject.official_name} has no teacher assigned now.",
                    ):
                        st.rerun()
