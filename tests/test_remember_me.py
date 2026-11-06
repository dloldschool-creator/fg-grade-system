"""The "Keep me signed in on this device" cookie (app/auth_cookie.py).

Asked for 2026-11-06: a refresh signed people out, and dropped
connections made the 60-minute idle limit feel shorter. The rules worth
pinning are the ones a later edit could quietly loosen on a shared
staffroom machine: off by default, a fixed 8 hours from sign-in, deleted
on logout, and logout revoking only *this* device's session.
"""

import inspect

from app import auth, auth_cookie


def test_round_trip():
    raw = auth_cookie.encode(1_700_000_000, "abc-DEF_123.xyz")
    assert auth_cookie.decode(raw) == (1_700_000_000.0, "abc-DEF_123.xyz")
    # Nothing a cookie header would split on.
    assert not set(raw) & set(" ;,=\"")


def test_malformed_cookies_are_refused():
    for raw in ("", "not-base64!!", auth_cookie.encode(1, "")[:-2], "djI6MTI6dG9r"):
        assert auth_cookie.decode(raw) is None, raw


def test_lifetime_is_eight_hours_from_sign_in():
    assert auth_cookie.REMEMBER_HOURS == 8
    signed_in = 1_700_000_000
    assert not auth_cookie.is_expired(signed_in, signed_in + 8 * 3600 - 1)
    assert auth_cookie.is_expired(signed_in, signed_in + 8 * 3600 + 1)
    # A sign-in time in the future is a tampered cookie, not a long session.
    assert auth_cookie.is_expired(signed_in + 3600, signed_in)


def test_set_cookie_expires_from_the_original_sign_in(monkeypatch):
    state = {}
    monkeypatch.setattr(auth_cookie.st, "session_state", state)
    auth_cookie.schedule_set(0, "tok")
    cookie = state[auth_cookie._PENDING_KEY]["cookie"]
    assert "Expires=Thu, 01 Jan 1970 08:00:00 GMT" in cookie
    assert "SameSite=Strict" in cookie and "Secure" in cookie and "Path=/" in cookie


def test_remember_me_is_off_by_default():
    source = inspect.getsource(auth.login_form)
    checkbox = source[source.index("st.checkbox(") :]
    assert "value=False" in checkbox[: checkbox.index("help=")]


def test_signing_in_without_it_clears_an_old_cookie():
    source = inspect.getsource(auth.login_form)
    assert "auth_cookie.schedule_delete()" in source.split("if remember:")[1]


def test_logout_deletes_the_cookie_and_revokes_only_this_device():
    source = inspect.getsource(auth.logout)
    assert "auth_cookie.schedule_delete()" in source
    assert '{"scope": "local"}' in source
    assert "REMEMBERED_SINCE_KEY" in source


def test_restore_is_tried_once_per_browser_session():
    """st.context.cookies is frozen at connect time, so after a logout the
    deleted cookie is still visible — a second try would sign back in."""
    source = inspect.getsource(auth._restore_remembered_session)
    assert source.index("RESTORE_TRIED_KEY] = True") < source.index("read_raw()")


def test_rotated_token_is_saved_before_the_database_lookup():
    source = inspect.getsource(auth._restore_remembered_session)
    assert source.index("schedule_set(") < source.index("_load_or_provision_user(")
