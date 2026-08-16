"""The School Head read-only guarantee (§3E: "cannot change official data").

The risk with a read-only role is that it's enforced by remembering to
hide each control, and one missed control is a silent hole. So these
tests check the rule itself and the *shape* of the pages it's granted,
rather than clicking through screens.
"""

import ast
import inspect
from pathlib import Path

import pytest

from app.auth import EDITING_ROLES, AuthUser

PAGES = Path(__file__).resolve().parent.parent / "app" / "admin_pages"


def _user(*roles: str) -> AuthUser:
    return AuthUser(
        id="00000000-0000-0000-0000-000000000001",
        supabase_auth_user_id="x",
        email="head@example.com",
        full_name="TEST HEAD",
        access_token="",
        refresh_token="",
        role_codes=set(roles),
    )


def test_a_school_head_alone_is_read_only():
    assert _user("SCHOOL_HEAD").is_read_only()


def test_an_account_with_no_roles_is_read_only():
    """Failing closed matters more here than being helpful — a brand-new
    account is provisioned with no roles until an admin grants one."""
    assert _user().is_read_only()


@pytest.mark.parametrize("role", sorted(EDITING_ROLES))
def test_a_working_role_is_not_read_only(role):
    assert not _user(role).is_read_only()


def test_a_head_who_also_advises_edits_normally():
    """The limit describes the account, not the job title — a principal
    who advises a section still marks that section's attendance."""
    assert not _user("SCHOOL_HEAD", "ADVISER").is_read_only()


def test_school_head_is_not_an_editing_role():
    assert "SCHOOL_HEAD" not in EDITING_ROLES


# --- The pages it can reach ------------------------------------------------

# Everything wired for SCHOOL_HEAD in streamlit_app.py.
GRANTED = ["dashboard", "grade_summary", "sf9", "sf2", "sf4", "term_cards", "data_export"]

# Report pages generate documents and never write school data. Grade
# Summary does have write paths, so it must gate them explicitly — it is
# checked separately below.
PURELY_READ_ONLY = ["dashboard", "sf9", "sf2", "sf4", "term_cards"]

# Calls that change official data. `record_export` is excluded on purpose:
# it appends an export-job audit row, which is a record *of* the read, not
# a change to a learner's data.
WRITING_CALLS = {
    "recompute_enrollment_grades",
    "capture_academic_record",
    "set_award_override",
    "clear_award_override",
    "try_delete",
    "finalize_month",
    "reopen_month",
    "seed_month_records",
}


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


@pytest.mark.parametrize("module", PURELY_READ_ONLY)
def test_pages_granted_to_a_school_head_do_not_write(module):
    """These are safe by construction rather than by gating, which is the
    stronger guarantee — there is no control to forget to hide."""
    called = _called_names(PAGES / f"{module}.py")
    assert not (called & WRITING_CALLS), f"{module} calls {called & WRITING_CALLS}"


@pytest.mark.parametrize("module", GRANTED)
def test_every_page_a_school_head_reaches_admits_the_role(module):
    """A page in the navigation whose own require_role omits SCHOOL_HEAD
    would show the sidebar entry and then refuse it."""
    source = (PAGES / f"{module}.py").read_text(encoding="utf-8")
    assert "SCHOOL_HEAD" in source, f"{module} does not admit SCHOOL_HEAD"


def test_grade_summary_gates_its_write_paths_on_the_read_only_check():
    """Grade Summary is the one granted page that *can* write — Recompute,
    Finalize and Reopen. Each must sit behind is_read_only()."""
    from app.admin_pages import grade_summary

    source = inspect.getsource(grade_summary)
    assert source.count("is_read_only()") >= 3, (
        "expected Recompute (both single and section-wide) and the "
        "finalization block to each check is_read_only()"
    )
