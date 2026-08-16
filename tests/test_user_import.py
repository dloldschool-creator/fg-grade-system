"""Bulk-adding users from a spreadsheet.

The rules worth pinning are the ones where the wrong behaviour would look
like success: a re-uploaded file quietly resetting forty passwords, a
role cell that reads fine to a person but matches no code, and a
provisioning loop that asks Supabase for the whole user list once per row.

Everything in `app/user_import.py` is pure, so none of this needs a
database or a Supabase account.
"""

import ast
import inspect

import openpyxl

from app import user_provisioning
from app.import_pipeline import missing_required, read_table, suggest_mapping, apply_mapping
from app.user_import import (
    USER_FILE,
    partition_existing,
    split_roles,
    template_bytes,
    validate_users,
)

ROLES = ["SUPER_ADMIN", "REGISTRAR", "ADVISER", "SUBJECT_TEACHER", "SCHOOL_HEAD"]


def rows(*items):
    return [{"__row__": index, **item} for index, item in enumerate(items, start=2)]


def check(items, existing=()):
    return validate_users(items, role_codes=ROLES, existing_emails=existing)


# --- Roles in one cell -----------------------------------------------------


def test_roles_are_accepted_however_the_list_was_written():
    """Commas, semicolons, slashes and line breaks are all how a person
    writes a list into one cell."""
    for raw in ("ADVISER, SUBJECT_TEACHER", "ADVISER;SUBJECT_TEACHER",
                "ADVISER / SUBJECT_TEACHER", "ADVISER\nSUBJECT_TEACHER"):
        assert split_roles(raw) == ["ADVISER", "SUBJECT_TEACHER"]


def test_a_role_typed_the_way_it_reads_still_matches():
    """The codes carry underscores for the database's sake; nobody types
    them back that way."""
    assert split_roles("subject teacher") == ["SUBJECT_TEACHER"]


def test_a_blank_roles_cell_is_allowed():
    """An account with no role can sign in and do nothing, which is a real
    state — refusing the whole file over it would be worse."""
    result = check(rows({"email": "a@b.ph", "full_name": "A B", "roles": ""}))
    assert result.ok
    assert result.parsed[0]["role_codes"] == []


def test_an_unknown_role_names_the_ones_that_exist():
    result = check(rows({"email": "a@b.ph", "full_name": "A B", "roles": "TEACHER III"}))
    assert not result.ok
    assert "TEACHER_III" in result.errors[0].message
    assert "SUBJECT_TEACHER" in result.errors[0].message


# --- Emails ----------------------------------------------------------------


def test_a_missing_at_sign_is_caught_not_provisioned():
    result = check(rows({"email": "juan.delacruz", "full_name": "A B", "roles": ""}))
    assert not result.ok
    assert result.errors[0].column == "Email"


def test_the_same_address_twice_in_one_file_points_at_the_first_row():
    result = check(
        rows(
            {"email": "a@b.ph", "full_name": "A B", "roles": ""},
            {"email": "A@B.ph", "full_name": "C D", "roles": ""},
        )
    )
    assert not result.ok
    assert "row 2" in result.errors[0].message


def test_an_email_is_stored_lowercased_and_the_name_uppercased():
    """Same normalisation the rest of the app uses, so a bulk-created
    adviser prints on SF9 the way a hand-created one does."""
    result = check(rows({"email": " Juan.DelaCruz@Deped.gov.ph ", "full_name": " juan  dela cruz ",
                         "roles": "adviser"}))
    assert result.parsed[0]["email"] == "juan.delacruz@deped.gov.ph"
    assert result.parsed[0]["full_name"] == "JUAN DELA CRUZ"


# --- The address that already has an account -------------------------------


def test_an_existing_account_is_skipped_rather_than_reset():
    """Re-uploading last month's file is the obvious mistake here.
    `provision_user` resets the password of an address it already knows,
    which is right for one deliberate click and catastrophic for forty:
    every teacher in the file would be locked out of a password they
    already had, mid-term.
    """
    result = check(
        rows(
            {"email": "old@b.ph", "full_name": "O B", "roles": "ADVISER"},
            {"email": "new@b.ph", "full_name": "N B", "roles": "ADVISER"},
        ),
        existing=["OLD@b.ph"],
    )
    assert result.ok, "an existing account is not an error"
    to_create, already = partition_existing(result.parsed)
    assert [r["email"] for r in to_create] == ["new@b.ph"]
    assert [r["email"] for r in already] == ["old@b.ph"]


# --- The file itself -------------------------------------------------------


def test_the_template_reads_back_through_the_real_upload_path():
    """Whatever the template ships with has to survive `read_table` and
    map itself, or the first thing an admin downloads fails to upload."""
    headers, data_rows = read_table(template_bytes(), "users-template.xlsx")
    assert data_rows == [], "a worked example in the template gets uploaded as a real person"
    mapping = suggest_mapping(headers, USER_FILE)
    assert missing_required(mapping, USER_FILE) == []


def test_the_school_s_own_header_spellings_map_without_being_asked():
    workbook = openpyxl.Workbook()
    workbook.active.append(["Email Address", "Name", "Role"])
    workbook.active.append(["a@b.ph", "A B", "ADVISER"])
    import io

    buffer = io.BytesIO()
    workbook.save(buffer)

    headers, data_rows = read_table(buffer.getvalue(), "staff.xlsx")
    mapping = suggest_mapping(headers, USER_FILE)
    assert missing_required(mapping, USER_FILE) == []
    result = check(apply_mapping(data_rows, mapping))
    assert result.ok and result.parsed[0]["role_codes"] == ["ADVISER"]


# --- Cost ------------------------------------------------------------------


def test_bulk_provisioning_lists_supabase_users_once_not_once_per_row():
    """`admin.list_users()` returns every account in the school. Calling
    it per row — which is what looping over `provision_user` would do —
    turns a forty-teacher file into forty full listings plus a session
    each. Checked structurally: the call must not sit inside a loop.
    """
    tree = ast.parse(inspect.getsource(user_provisioning.provision_users).strip())
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)):
            assert not any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "list_users"
                for n in ast.walk(node)
            ), "list_users() is inside a loop"


def test_validating_a_file_costs_no_queries():
    """The Users page re-runs top to bottom on every click with the upload
    still in hand. Validation is handed the roles and emails the page
    already loaded, so it must never open a session of its own."""
    source = inspect.getsource(validate_users)
    assert "session" not in source and "query" not in source


def test_the_roles_a_bulk_account_gets_are_audit_logged():
    """Rule 8, and §50's "user permission changed" in particular — the
    entry that explains how a batch of people came to be able to act."""
    source = inspect.getsource(user_provisioning.provision_users)
    assert "audit_service.record" in source
    assert "USER_ROLES_CHANGED" in source


# --- The page, through Streamlit's own runtime -----------------------------

# `_bulk_add` takes its roles and emails as arguments and never opens a
# session, which is what lets it run here at all — no database, no login,
# no Supabase. The upload and the provisioning are the two things that
# reach outside, so both are replaced. What is being checked is that the
# widgets, the preview and the confirm button really do run end to end;
# a page that raises on the second rerun looks perfect in the source.
PAGE_SCRIPT = """
import io, uuid
from types import SimpleNamespace

import openpyxl
import streamlit as st

from app.admin_pages import users as page
from app.user_provisioning import BulkOutcome, ProvisionedUser

workbook = openpyxl.Workbook()
workbook.active.append(["Email", "Full Name", "Roles"])
workbook.active.append(["new@b.ph", "Juan Dela Cruz", "ADVISER, SUBJECT_TEACHER"])
workbook.active.append(["old@b.ph", "Maria Santos", "REGISTRAR"])
buffer = io.BytesIO()
workbook.save(buffer)

st.file_uploader = lambda *a, **k: SimpleNamespace(
    name="staff.xlsx", getvalue=lambda: buffer.getvalue()
)

calls = st.session_state.setdefault("calls", [])
def fake_provision(rows, *, actor_user_id=None):
    calls.append([r["email"] for r in rows])
    return BulkOutcome(
        provisioned=[ProvisionedUser("id-1", "new@b.ph", "s3cret-temp-pw", False)],
        skipped=["old@b.ph"],
    )
page.provision_users = fake_provision

page._show_bulk_result()
page._bulk_add(
    SimpleNamespace(id=uuid.uuid4()),
    [SimpleNamespace(code=c) for c in
     ["ADVISER", "REGISTRAR", "SUBJECT_TEACHER", "SUPER_ADMIN"]],
    {"old@b.ph"},
)
"""


def _page():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(PAGE_SCRIPT)
    app.run()
    return app


def test_the_page_previews_the_file_and_says_what_it_will_skip():
    app = _page()
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Rows read"] == "2"
    assert metrics["To create"] == "1"
    assert metrics["Errors"] == "0"
    assert any("already have an account" in i.value for i in app.info)


def test_confirming_provisions_only_the_new_rows_and_shows_the_passwords():
    app = _page()
    create = next(b for b in app.button if "Create" in b.label)
    assert create.label == "Create 1 account(s)"
    create.click().run()

    assert not app.exception
    assert app.session_state["calls"] == [["new@b.ph"]], "an existing account was re-provisioned"
    # The password appears exactly once and is never stored — if the page
    # fails to print it the account is stranded until someone resets it.
    assert any("s3cret-temp-pw" in c.value for c in app.code)
    assert any("left untouched" in i.value for i in app.info)


# --- The double-submit that invalidated a password already written down ----

# Diagnosed 2026-08-16 from a live account: auth.users created at 04:22:44
# and updated at 04:23:24, with the app row untouched — so `provision_user`
# ran a second time rather than `reset_password`. The admin had copied the
# first password; the second one silently replaced it, and sign-in failed
# with "Invalid login credentials" against an account that was in every
# other respect healthy.
#
# The form made it the natural thing to do: the password renders at the
# TOP of the page while the form sits at the bottom, so pressing Create
# changed nothing visible from where the button is — and st.form keeps its
# values in the frontend, so the email was still sitting there.
ADD_USER_SCRIPT = """
import uuid
from types import SimpleNamespace

import streamlit as st

from app.admin_pages import users as page
from app.admin_pages._helpers import clear_text_fields, flash, render_flashes, text_field
from app.user_provisioning import ProvisionedUser

calls = st.session_state.setdefault("calls", [])
roles = [SimpleNamespace(id=uuid.uuid4(), code="ADVISER")]
role_by_id = {r.id: r for r in roles}

render_flashes()

with st.form("add_user"):
    email = text_field("Email", key="add_user.email")
    full_name = text_field("Full name", key="add_user.full_name")
    role_choice = st.multiselect("Roles", options=[r.id for r in roles],
                                 format_func=lambda v: role_by_id[v].code)
    if st.form_submit_button("Create"):
        if not email or not full_name:
            st.error("Email and full name are required.")
        else:
            calls.append(email)
            st.session_state["_last_provisioned"] = ProvisionedUser(
                "id-1", email, "pw-%d" % len(calls), False)
            flash("success", "Created %s" % email)
            clear_text_fields("add_user")
            st.rerun()
"""


def _add_user_page():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(ADD_USER_SCRIPT)
    app.run()
    return app


def test_the_add_form_empties_itself_so_create_cannot_run_twice():
    """The regression that cost a live password.

    Pressing Create a second time re-provisions the same email, and
    `provision_user` resets an address it already knows — so the password
    the admin had just copied stopped working. Clearing the boxes turns
    the second press into a validation error instead.
    """
    app = _add_user_page()
    app.text_input[0].set_value("test@test.com").run()
    app.text_input[1].set_value("TEST ONLY").run()
    app.button[0].click().run()

    assert app.session_state["calls"] == ["test@test.com"]
    # The real fix: a *new* widget key, so nothing is left to re-submit.
    # Deleting the session_state key instead passes this and still ships
    # the bug — see _helpers.clear_text_fields.
    assert app.text_input[0].value in ("", None)

    app.button[0].click().run()
    assert app.session_state["calls"] == ["test@test.com"], (
        "Create ran twice — the second call resets the account and "
        "invalidates the password already handed out"
    )
    assert any("required" in e.value for e in app.error)


def test_the_result_is_flashed_so_something_happens_where_you_clicked():
    """The password renders at the top of a long page; the button is at
    the bottom. Without a toast, a successful create looks like nothing
    happened, which is what invites the second press."""
    app = _add_user_page()
    app.text_input[0].set_value("test@test.com").run()
    app.text_input[1].set_value("TEST ONLY").run()
    app.button[0].click().run()
    assert any("Created test@test.com" in s.value for s in app.success)


def test_the_real_add_form_clears_and_reruns():
    """The script above proves the *pattern*; this proves the page uses
    it. Without both, the page can drift back to a plain `st.text_input`
    that keeps its value and the pattern test still passes."""
    import inspect

    from app.admin_pages import users

    source = inspect.getsource(users.render)
    block = source[source.index('st.form("add_user")'):]
    assert 'text_field("Email"' in block, "a plain text_input keeps its value across the rerun"
    assert 'clear_text_fields("add_user")' in block
    assert "st.rerun()" in block
