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
    insights,
    learners,
    parent_notices,
    school_info,
    school_years,
    section_offerings,
    sections,
    sf2,
    sf4,
    sf9,
    subject_catalog,
    subject_profiles,
    subject_units,
    teacher_assignments,
    term_cards,
    users,
)
from app.auth import (
    change_password_form,
    get_current_user,
    login_form,
    logout,
    password_change_required,
)
from app.version import version_line

st.set_page_config(page_title="FGNMHS Grading System", layout="wide")

# Every button in the school's blue, not just the ones marked
# type="primary" (asked for 2026-09-26: two button styles side by side read
# as two kinds of action). Streamlit's theme has no setting for secondary
# buttons, so this restyles them here, once, for every page including
# sign-in. Disabled buttons keep Streamlit's greyed look — a blue disabled
# button would read as clickable.
st.markdown(
    """
    <style>
      [data-testid^="stBaseButton-secondary"]:not(:disabled) {
          background-color: #1B4F9C;
          border-color: #1B4F9C;
          color: #FFFFFF;
      }
      [data-testid^="stBaseButton-secondary"]:not(:disabled):hover,
      [data-testid^="stBaseButton-secondary"]:not(:disabled):focus-visible {
          background-color: #153E7A;
          border-color: #153E7A;
          color: #FFFFFF;
      }
      [data-testid^="stBaseButton-secondary"]:not(:disabled) p {
          color: #FFFFFF;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

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
            (dashboard, "Dashboard", ":material/dashboard:", "dashboard",
             ("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD")),
            (insights, "Insights", ":material/insights:", "insights",
             ("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD", "ADVISER",
              "SUBJECT_TEACHER")),
        ],
    ),
    (
        "Grades & Attendance",
        [
            (gradebook, "Gradebook", ":material/edit_note:", "gradebook", ("SUBJECT_TEACHER",)),
            (grade_summary, "Grade Summary", ":material/summarize:", "grade-summary",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")),
            # "& SF2": advisers print their SF2 here, and look for the
            # word SF2 in the sidebar because the workbook's SF2 sheet was
            # where they typed attendance.
            (attendance, "Attendance & SF2", ":material/event_available:", "attendance",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER")),
        ],
    ),
    (
        "Learners",
        [
            (learners, "Learner Masterlist", ":material/school:", "learners",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER")),
            (enrollment, "Enrollment", ":material/how_to_reg:", "enrollment",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER")),
        ],
    ),
    (
        "Forms & Reports",
        [
            (sf9, "SF9", ":material/description:", "sf9",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")),
            # No adviser entry: they went here to encode. They print from
            # Attendance & SF2 instead, once the month is finalized.
            (sf2, "SF2", ":material/fact_check:", "sf2",
             ("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD")),
            # School-wide rather than per-section, and signed by the
            # School Head — so no adviser entry.
            (sf4, "SF4", ":material/swap_horiz:", "sf4", ("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD")),
            (term_cards, "Term Cards", ":material/badge:", "term-cards",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")),
            # The School Head sees the sent record read-only; every write
            # on the page is gated on is_read_only().
            (parent_notices, "Parent Notices", ":material/mail:", "parent-notices",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")),
            (awards, "Awards", ":material/emoji_events:", "awards",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER")),
            (data_export, "Export", ":material/download:", "export",
             ("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")),
        ],
    ),
    (
        "Setup",
        [
            (school_info, "School Info", ":material/apartment:", "school-info", ("SUPER_ADMIN",)),
            (school_years, "School Years & Terms", ":material/date_range:", "school-years", ("SUPER_ADMIN",)),
            (academic_calendar, "Academic Calendar", ":material/calendar_month:", "academic-calendar", ("SUPER_ADMIN",)),
            (academic_structure, "Academic Structure", ":material/account_tree:", "academic-structure", ("SUPER_ADMIN",)),
            (sections, "Sections", ":material/groups:", "sections", ("SUPER_ADMIN",)),
            (subject_catalog, "Subject Catalog", ":material/menu_book:", "subject-catalog", ("SUPER_ADMIN",)),
            (subject_units, "Subject Units", ":material/balance:", "subject-units", ("SUPER_ADMIN",)),
            (subject_profiles, "Subject Profiles", ":material/folder_copy:", "subject-profiles", ("SUPER_ADMIN",)),
            (section_offerings, "Section Subject Offerings", ":material/checklist:", "section-offerings", ("SUPER_ADMIN",)),
            (teacher_assignments, "Teacher Assignments", ":material/assignment_ind:", "teacher-assignments", ("SUPER_ADMIN", "ADVISER")),
            (grading_policy, "Grading Policy", ":material/rule:", "grading-policy", ("SUPER_ADMIN",)),
            (award_policy, "Award Policy", ":material/military_tech:", "award-policy", ("SUPER_ADMIN",)),
        ],
    ),
    (
        "Administration",
        [
            (users, "Users & Roles", ":material/manage_accounts:", "users", ("SUPER_ADMIN",)),
            (data_import, "Import from Excel", ":material/upload_file:", "import", ("SUPER_ADMIN", "REGISTRAR")),
            (audit_log, "Audit Log", ":material/history:", "audit-log", ("SUPER_ADMIN",)),
            (backup, "Backup", ":material/backup:", "backup", ("SUPER_ADMIN",)),
        ],
    ),
    (
        "Help",
        [
            # Everyone, including an account with no role yet — it is the
            # one page that explains what they are waiting for.
            (help_page, "Quick Guide", ":material/help:", "help", ()),
        ],
    ),
]


def _quick_guide_page(**extra) -> st.Page:
    """The Quick Guide's `st.Page`, from its GROUPS row, so the title, icon
    and url_path are written once."""
    _module, title, icon, url_path, _roles = next(
        entry for _heading, entries in GROUPS for entry in entries if entry[0] is help_page
    )
    return st.Page(help_page.render, title=title, icon=icon, url_path=url_path, **extra)


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
elif current_user.must_change_password:
    # No sidebar and no navigation while someone is still on the
    # temporary password an admin issued and read. Building the menu and
    # letting each page refuse would leave the app looking usable and
    # every entry in it a dead end; withholding the menu says what is
    # actually being asked. `require_role` carries the same check as a
    # backstop for a page reached directly by URL.
    pg = st.navigation(
        [st.Page(password_change_required, title="Set your password", default=True)],
        position="hidden",
    )
    pg.run()
else:
    with st.sidebar:
        st.write(f"Logged in as **{current_user.full_name}**")
        st.caption(", ".join(sorted(current_user.role_codes)) or "no role")
        change_password_form()
        if st.button("Log out"):
            logout()
            st.rerun()
        # Which commit is live and how long it has been up — so "did my
        # push deploy?" is answerable at a glance rather than by hunting
        # for a changed word in the UI.
        st.caption(version_line())

    navigation = build_navigation(current_user)

    # A user with only the Help group has no working pages, so say so
    # rather than landing them on the guide with no explanation.
    if set(navigation) == {"Help"}:
        pg = st.navigation(
            [
                st.Page(_render_not_authorized, title="Not authorized", default=True),
                _quick_guide_page(),
            ]
        )
    else:
        pg = st.navigation(navigation)
    pg.run()
