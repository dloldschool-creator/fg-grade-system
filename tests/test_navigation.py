"""The sidebar's shape and its agreement with the pages themselves.

The navigation table and each page's own `require_role` are two
statements of the same rule, and they drift silently: a page can admit a
role the sidebar never offers it (invisible feature) or offer one the
page then refuses (a menu entry that errors on click).
"""

import ast
import re
from pathlib import Path

import pytest

import streamlit_app
from app.auth import EDITING_ROLES
from app.seed import ROLES

PAGES = Path(streamlit_app.__file__).resolve().parent / "app" / "admin_pages"
SEEDED = {code for code, _ in ROLES}

ENTRIES = [
    (heading, module, title, url_path, roles)
    for heading, entries in streamlit_app.GROUPS
    for module, title, _icon, url_path, roles in entries
]


def _required_roles(module) -> set[str]:
    """The roles a page's own require_role() admits."""
    source = Path(module.__file__).read_text(encoding="utf-8")
    match = re.search(r"require_role\(([^)]*)\)", source)
    if match is None:
        return set()
    return set(re.findall(r"[\"'](\w+)[\"']", match.group(1)))


# --- Agreement between the sidebar and the pages ---------------------------


@pytest.mark.parametrize(
    "title, module, roles",
    [(t, m, r) for _, m, t, _u, r in ENTRIES if r],
    ids=[t for _, _m, t, _u, r in ENTRIES if r],
)
def test_the_sidebar_offers_exactly_what_the_page_admits(title, module, roles):
    """SUPER_ADMIN satisfies every check implicitly (AuthUser.has_role),
    so it is allowed to be absent from a page's own guard."""
    admitted = _required_roles(module)
    if not admitted:
        pytest.skip(f"{title} has no require_role to compare against")
    offered = set(roles)
    assert offered - admitted <= {"SUPER_ADMIN"}, (
        f"the sidebar offers {title} to {sorted(offered - admitted)}, "
        "who the page then refuses"
    )
    assert admitted - offered <= {"SUPER_ADMIN"}, (
        f"{title} admits {sorted(admitted - offered)} but the sidebar never "
        "shows it to them"
    )


# --- Shape -----------------------------------------------------------------


def test_no_page_is_listed_twice():
    """st.navigation raises on a duplicate url_path. Listing each page
    once with every role that may reach it removes the failure rather
    than de-duplicating afterwards."""
    paths = [url_path for _h, _m, _t, url_path, _r in ENTRIES]
    assert len(paths) == len(set(paths)), "duplicate url_path in the nav table"


def test_every_role_code_used_is_real():
    for _heading, _module, title, _url, roles in ENTRIES:
        assert set(roles) <= SEEDED, f"{title} names a role that isn't seeded"


def test_daily_work_comes_before_setup():
    """The ordering rule worth keeping: School Info is touched once a
    year, the Gradebook every day. Grouping by role put eleven setup
    pages ahead of everything a teacher uses."""
    headings = [heading for heading, _ in streamlit_app.GROUPS]
    assert headings.index("Grades & Attendance") < headings.index("Setup")
    assert headings.index("Overview") == 0
    assert headings[-1] == "Help"


def test_setup_runs_in_dependency_order():
    """You cannot make a Section without an Academic Structure, an
    offering without a Section, or a Teacher Assignment without an
    offering — so a first-time setup should be able to work straight down
    the list."""
    setup = next(entries for heading, entries in streamlit_app.GROUPS if heading == "Setup")
    order = [url for _m, _t, _i, url, _r in setup]
    for earlier, later in [
        ("school-years", "academic-calendar"),
        ("academic-structure", "sections"),
        ("sections", "section-offerings"),
        ("subject-catalog", "section-offerings"),
        ("section-offerings", "teacher-assignments"),
    ]:
        assert order.index(earlier) < order.index(later), f"{earlier} must precede {later}"


def test_the_guide_is_reachable_by_a_user_with_no_role():
    guide = next(e for e in ENTRIES if e[3] == "help")
    assert guide[4] == (), "the Quick Guide must not be role-gated"


def test_a_read_only_account_gets_no_page_that_edits():
    """Cross-check against the read-only rule: every group a School Head
    sees must consist of pages the role is actually allowed on."""
    head_only = {"SCHOOL_HEAD"}
    assert not head_only & EDITING_ROLES
    visible = [t for _h, _m, t, _u, roles in ENTRIES if roles and "SCHOOL_HEAD" in roles]
    assert set(visible) == {
        "Dashboard", "Grade Summary", "SF9", "SF2", "SF4", "Term Cards", "Export",
    }, "adding a page for a School Head is a deliberate act — confirm it cannot write"
