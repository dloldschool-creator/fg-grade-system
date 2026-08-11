import streamlit as st

from app.admin_pages import (
    academic_calendar,
    academic_structure,
    attendance,
    audit_log,
    award_policy,
    awards,
    backup,
    data_export,
    data_import,
    enrollment,
    gradebook,
    grade_summary,
    help as help_page,
    grading_policy,
    learners,
    school_info,
    school_years,
    section_offerings,
    sections,
    sf2,
    sf9,
    subject_catalog,
    subject_profiles,
    teacher_assignments,
    term_cards,
    users,
)
from app.auth import change_password_form, get_current_user, login_form, logout

st.set_page_config(page_title="FGNMHS Grading System", layout="wide")


def _render_not_authorized() -> None:
    st.error("You don't have access to any admin pages. Ask a Super Admin to grant you a role.")


current_user = get_current_user()

if current_user is None:
    pg = st.navigation([st.Page(login_form, title="Login", default=True)], position="hidden")
    pg.run()
else:
    with st.sidebar:
        st.write(f"Logged in as **{current_user.full_name}**")
        st.caption(", ".join(sorted(current_user.role_codes)) or "no role")
        change_password_form()
        if st.button("Log out"):
            logout()
            st.rerun()

    is_super_admin = current_user.has_role("SUPER_ADMIN")

    # st.navigation allows at most one default=True page. Whichever
    # role-block below runs first for this user claims it; later blocks
    # pass default=False regardless of what they'd otherwise want.
    pages = []
    if is_super_admin:
        pages += [
            st.Page(school_info.render, title="School Info", icon="🏫", url_path="school-info", default=True),
            st.Page(school_years.render, title="School Years & Terms", icon="📅", url_path="school-years"),
            st.Page(academic_structure.render, title="Academic Structure", icon="🎓", url_path="academic-structure"),
            st.Page(academic_calendar.render, title="Academic Calendar", icon="🗓️", url_path="academic-calendar"),
            st.Page(sections.render, title="Sections", icon="🏷️", url_path="sections"),
            st.Page(subject_catalog.render, title="Subject Catalog", icon="📚", url_path="subject-catalog"),
            st.Page(subject_profiles.render, title="Subject Profiles", icon="🗂️", url_path="subject-profiles"),
            st.Page(section_offerings.render, title="Section Offerings", icon="📋", url_path="section-offerings"),
            st.Page(teacher_assignments.render, title="Teacher Assignments", icon="🧑‍🏫", url_path="teacher-assignments"),
            st.Page(grading_policy.render, title="Grading Policy", icon="✅", url_path="grading-policy"),
            st.Page(award_policy.render, title="Award Policy", icon="🏅", url_path="award-policy"),
            st.Page(data_import.render, title="Import from Excel", icon="📥", url_path="import"),
            st.Page(users.render, title="Users & Roles", icon="👥", url_path="users"),
            st.Page(audit_log.render, title="Audit Log", icon="🧭", url_path="audit-log"),
            st.Page(backup.render, title="Backup", icon="💾", url_path="backup"),
        ]
    # Learner Masterlist, Enrollment, and Grade Summary are all reachable
    # by Registrar and by Adviser — DepEd advisers pick up
    # registrar-adjacent duties for their own section (§3C), so each of
    # these pages scopes itself internally (adviser_user_id filtering on
    # sections) rather than being gated out entirely.
    if current_user.has_role("SUPER_ADMIN", "REGISTRAR", "ADVISER"):
        pages += [
            st.Page(
                learners.render, title="Learner Masterlist", icon="🧑‍🎓", url_path="learners",
                default=not pages,
            ),
            st.Page(enrollment.render, title="Enrollment", icon="📝", url_path="enrollment"),
            st.Page(grade_summary.render, title="Grade Summary", icon="📊", url_path="grade-summary"),
            st.Page(attendance.render, title="Attendance", icon="🗒️", url_path="attendance"),
            st.Page(sf2.render, title="SF2", icon="📄", url_path="sf2"),
            st.Page(sf9.render, title="SF9", icon="🧾", url_path="sf9"),
            st.Page(term_cards.render, title="Term Cards", icon="🎫", url_path="term-cards"),
            st.Page(awards.render, title="Awards", icon="🏆", url_path="awards"),
            st.Page(data_export.render, title="Export", icon="📤", url_path="export"),
        ]
    if current_user.has_role("SUBJECT_TEACHER"):
        pages += [
            st.Page(
                gradebook.render, title="Gradebook", icon="📓", url_path="gradebook",
                default=not pages,
            ),
        ]

    if not pages:
        # Someone whose account exists but has no role yet still gets the
        # guide — it's the one page that explains what they're waiting for.
        pg = st.navigation(
            [
                st.Page(_render_not_authorized, title="Not authorized", default=True),
                st.Page(help_page.render, title="Quick Guide", icon="❓", url_path="help"),
            ]
        )
    else:
        # Appended last so it never claims `default=True`, and so it sits at
        # the foot of the sidebar where a help link is looked for.
        pages.append(
            st.Page(help_page.render, title="Quick Guide", icon="❓", url_path="help")
        )
        pg = st.navigation(pages)
    pg.run()
