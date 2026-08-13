"""Resetting a password and deleting a user.

Both exist because of a real incident: an admin created an account, the
connection dropped before the one-time password was read, and the account
was left stranded — nobody knew the password, and it could not be deleted
to start again.

These tests exercise the guards and the shape of the calls. They do not
touch Supabase Auth: creating and deleting real accounts as a side effect
of running the suite is not something a test should do.
"""

import inspect

import pytest

from app import user_provisioning as provisioning


def test_reset_password_cannot_change_what_someone_can_do():
    """Deliberately separate from provision_user, which also grants roles.
    Handing out a new password should never widen access.

    Checked against the parsed body rather than the text, so the docstring
    may explain the rule without appearing to break it.
    """
    import ast

    tree = ast.parse(inspect.getsource(provisioning.reset_password).strip())
    function = tree.body[0]
    body = function.body[1:] if ast.get_docstring(function) else function.body
    names = {
        node.id if isinstance(node, ast.Name) else node.attr
        for statement in body
        for node in ast.walk(statement)
        if isinstance(node, (ast.Name, ast.Attribute))
    }
    assert not {"UserRole", "Role", "role_codes"} & names, f"touches roles: {names}"


def test_reset_password_refuses_an_account_that_does_not_exist(monkeypatch):
    """Otherwise a typo in the email silently succeeds and the admin hands
    out a password for nothing."""
    class _Admin:
        def list_users(self):
            return []

    monkeypatch.setattr(
        provisioning, "get_admin_client",
        lambda: type("C", (), {"auth": type("A", (), {"admin": _Admin()})()})(),
    )
    with pytest.raises(provisioning.UserProvisioningError, match="No account exists"):
        provisioning.reset_password("nobody@example.com")


def test_a_generated_password_is_long_and_random():
    first = provisioning._generate_temporary_password()
    second = provisioning._generate_temporary_password()
    assert first != second
    assert len(first) >= 16


def test_delete_refuses_while_the_user_still_advises_or_teaches():
    """These are the two foreign keys that block the delete, and both need
    a human decision about who takes over — so the message names the page
    to go and fix it on rather than just refusing."""
    source = inspect.getsource(provisioning.delete_user)
    assert "adviser_user_id" in source
    assert "teacher_user_id" in source
    assert "Sections page" in source
    assert "Teacher Assignments page" in source


def test_delete_removes_the_auth_login_too():
    """Leaving the Supabase Auth account behind would let someone sign in
    with no application record to authorise them."""
    assert "admin.delete_user" in inspect.getsource(provisioning.delete_user)


def test_the_auth_login_is_deleted_last():
    """If that call fails the app row is already gone, and a stranded
    login is recoverable — re-adding the email resets it. The reverse
    order could leave a working sign-in with nothing behind it."""
    source = inspect.getsource(provisioning.delete_user)
    assert source.index("session.delete(user)") < source.index("admin.delete_user")


def test_the_page_will_not_delete_the_signed_in_account():
    """Locking yourself out of the only admin account is unrecoverable
    from inside the app."""
    from app.admin_pages import users

    source = inspect.getsource(users.render)
    assert "user.id == current_user.id" in source


def test_deleting_a_user_is_audit_logged():
    from app.admin_pages import users

    source = inspect.getsource(users.render)
    delete_block = source[source.index("Delete user"):]
    assert "audit_service.record" in delete_block
