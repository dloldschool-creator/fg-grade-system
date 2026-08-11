import streamlit as st

from app.admin_pages import (
    academic_calendar,
    academic_structure,
    attendance,
    audit_log,
    award_policy,
    awards,
    backup,
    dashboard,
    data_export,
    data_import,
    enrollment,
    grade_summary,
    gradebook,
    grading_policy,
    help as help_page,
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

# --- Navigation ------------------------------------------------------------
#
# One table rather than per-role blocks appending lists. Two reasons:
#
# 1. **Order follows the work, not the role.** Grouped by role, a Super
#    Admin met eleven setup pages before reaching anything used daily —
#    but School Info is touched once a year and the Gradebook every day.
#    Groups run roughly most-used first, and Setup runs in dependency
#    order: you cannot make a Section without an Academic Structure, an
#    offering without a Section, or a Teacher Assignment without an
#    offering.
#
# 2. **A page appears exactly once.** Previously a principal holding
#    SCHOOL_HEAD and REGISTRAR had the same page added twice, and
#    st.navigation raises on a duplicate url_path — so a de-duplication
#    pass was papering over the real problem. Listing each page once with
#    the full set of roles that may reach it removes the failure mode
#    instead of handling it.
#
# `roles` must match the page's own require_role call — tests/test_navigation.py
# checks that, because a mismatch either hides a page from someone entitled
# to it or shows a sidebar entry that refuses them on click.

GROUPS = [
    (
        "Overview",
        [
            (dashboard, "Dashboard", "📈", "dashboard",
             ("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD")),
        ],
    ),
    (
        "Grades & Attendance",
        [
            (gradebook, "Gradebook", "📓", "gradebook", ("SUBJECT_TEACHER",)),
            (grade_summary, "Grade Summary", "📊", "grade-summary",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")),
            (attendance, "Attendance", "🗒️", "attendance",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER")),
        ],
    ),
    (
        "Learners",
        [
            (learners, "Learner Masterlist", "🧑‍🎓", "learners",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER")),
            (enrollment, "Enrollment", "📝", "enrollment",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER")),
        ],
    ),
    (
        "Forms & Reports",
        [
            (sf9, "SF9", "🧾", "sf9",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")),
            (sf2, "SF2", "📄", "sf2",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")),
            (term_cards, "Term Cards", "🎫", "term-cards",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")),
            (awards, "Awards", "🏆", "awards",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER")),
            (data_export, "Export", "📤", "export",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")),
        ],
    ),
    (
        "Setup",
        [
            (school_info, "School Info", "🏫", "school-info", ("SUPER_ADMIN",)),
            (school_years, "School Years & Terms", "📅", "school-years", ("SUPER_ADMIN",)),
            (academic_calendar, "Academic Calendar", "🗓️", "academic-calendar", ("SUPER_ADMIN",)),
            (academic_structure, "Academic Structure", "🎓", "academic-structure", ("SUPER_ADMIN",)),
            (sections, "Sections", "🏷️", "sections", ("SUPER_ADMIN",)),
            (subject_catalog, "Subject Catalog", "📚", "subject-catalog", ("SUPER_ADMIN",)),
            (subject_profiles, "Subject Profiles", "🗂️", "subject-profiles", ("SUPER_ADMIN",)),
            (section_offerings, "Section Offerings", "📋", "section-offerings", ("SUPER_ADMIN",)),
            (teacher_assignments, "Teacher Assignments", "🧑‍🏫", "teacher-assignments", ("SUPER_ADMIN",)),
            (grading_policy, "Grading Policy", "✅", "grading-policy", ("SUPER_ADMIN",)),
            (award_policy, "Award Policy", "🏅", "award-policy", ("SUPER_ADMIN",)),
        ],
    ),
    (
        "Administration",
        [
            (users, "Users & Roles", "👥", "users", ("SUPER_ADMIN",)),
            (data_import, "Import from Excel", "📥", "import", ("SUPER_ADMIN", "REGISTRAR")),
            (audit_log, "Audit Log", "🧭", "audit-log", ("SUPER_ADMIN",)),
            (backup, "Backup", "💾", "backup", ("SUPER_ADMIN",)),
        ],
    ),
    (
        "Help",
        [
            # Everyone, including an account with no role yet — it is the
            # one page that explains what they are waiting for.
            (help_page, "Quick Guide", "❓", "help", ()),
        ],
    ),
]


def _render_not_authorized() -> None:
    st.error("You don't have access to any admin pages. Ask a Super Admin to grant you a role.")


def build_navigation(user) -> dict:
    """Grouped sidebar for this user, empty groups omitted.

    The first page of the first group carries `default=True`, so whatever
    the user's most-used group turns out to be is also their landing page.
    """
    navigation: dict[str, list] = {}
    claimed_default = False
    for heading, entries in GROUPS:
        visible = []
        for module, title, icon, url_path, roles in entries:
            if roles and not user.has_role(*roles):
                continue
            visible.append(
                st.Page(
                    module.render,
                    title=title,
                    icon=icon,
                    url_path=url_path,
                    default=not claimed_default,
                )
            )
            claimed_default = True
        if visible:
            navigation[heading] = visible
    return navigation


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

    navigation = build_navigation(current_user)

    # A user with only the Help group has no working pages, so say so
    # rather than landing them on the guide with no explanation.
    if set(navigation) == {"Help"}:
        pg = st.navigation(
            [
                st.Page(_render_not_authorized, title="Not authorized", default=True),
                st.Page(help_page.render, title="Quick Guide", icon="❓", url_path="help"),
            ]
        )
    else:
        pg = st.navigation(navigation)
    pg.run()
