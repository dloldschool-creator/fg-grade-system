"""The "Keep me signed in on this device" cookie (app/auth_cookie.py).

Asked for 2026-11-06: a refresh signed people out, and dropped
connections made the 60-minute idle limit feel shorter. The rules worth
pinning are the ones a later edit could quietly loosen on a shared
staffroom machine: off by default, a fixed 8 hours from sign-in, deleted
on logout, and logout revoking only *this* device's session.
"""

import inspect
import time

from app import auth, auth_cookie


def test_round_trip():
    raw = auth_cookie.encode(1_700_000_000, "abc-DEF_123.xyz")
    assert auth_cookie.decode(raw) == (1_700_000_000.0, "abc-DEF_123.xyz")
    # Nothing a cookie header would split on.
    assert not set(raw) & set(" ;,=\"")


def test_malformed_cookies_are_refused():
    for raw in ("", "not-base64!!", auth_cookie.encode(1, "")[:-2], "djI6MTI6dG9r"):
        assert auth_cookie.decode(raw) is None, raw


def test_the_default_lifetime_is_eight_hours():
    """Read from the source, not the module constant, so a developer's
    REMEMBER_ME_HOURS override doesn't fail it."""
    assert 'os.getenv("REMEMBER_ME_HOURS", "8")' in inspect.getsource(auth_cookie)


def test_lifetime_counts_from_sign_in():
    signed_in = 1_700_000_000
    lifetime = auth_cookie.REMEMBER_SECONDS
    assert not auth_cookie.is_expired(signed_in, signed_in + lifetime - 1)
    assert auth_cookie.is_expired(signed_in, signed_in + lifetime + 1)
    # A sign-in time in the future is a tampered cookie, not a long session.
    assert auth_cookie.is_expired(signed_in + 3600, signed_in)


def test_set_cookie_expires_from_the_original_sign_in(monkeypatch):
    from email.utils import formatdate

    state = {}
    monkeypatch.setattr(auth_cookie.st, "session_state", state)
    auth_cookie.schedule_set(0, "tok")
    cookie = state[auth_cookie._PENDING_KEY]["cookie"]
    assert f"Expires={formatdate(auth_cookie.REMEMBER_SECONDS, usegmt=True)}" in cookie
    assert "SameSite=Strict" in cookie and "Secure" in cookie and "Path=/" in cookie


# --- Review fixes (2026-11-06) ----------------------------------------------


def test_anon_client_does_not_refresh_tokens_in_the_background(monkeypatch):
    """The library's default timer spent the cookie's refresh token about
    an hour after sign-in, so the next restore presented a used token and
    Supabase revoked the session: 8 hours became one."""
    from app import supabase_clients

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    captured = {}

    def fake_create_client(url, key, options=None):
        captured["options"] = options

    monkeypatch.setattr(supabase_clients, "create_client", fake_create_client)
    supabase_clients.get_anon_client()
    assert captured["options"].auto_refresh_token is False


class _FakeAuth:
    def __init__(self, exc):
        self.exc = exc

    def refresh_session(self, token):
        raise self.exc


class _FakeClient:
    def __init__(self, exc):
        self.auth = _FakeAuth(exc)


def _restore_with(monkeypatch, exc):
    """Runs a restore whose refresh_session raises `exc`; returns the
    cookie write it scheduled, if any."""
    state = {}
    cookie = auth_cookie.encode(time.time(), "tok")
    monkeypatch.setattr(auth.st, "session_state", state)
    monkeypatch.setattr(auth_cookie.st, "session_state", state)
    monkeypatch.setattr(auth_cookie, "read_raw", lambda: cookie)
    monkeypatch.setattr(auth, "get_anon_client", lambda: _FakeClient(exc))
    assert auth._restore_remembered_session() is None
    return state.get(auth_cookie._PENDING_KEY)


def test_a_network_failure_keeps_the_cookie(monkeypatch):
    import httpx
    from supabase_auth.errors import AuthRetryableError

    assert _restore_with(monkeypatch, httpx.ConnectError("down")) is None
    assert _restore_with(monkeypatch, AuthRetryableError("502", 502)) is None


def test_a_rejected_token_deletes_the_cookie(monkeypatch):
    from supabase_auth.errors import AuthApiError

    pending = _restore_with(monkeypatch, AuthApiError("Invalid Refresh Token", 400, None))
    assert pending is not None and "Expires=Thu, 01 Jan 1970" in pending["cookie"]


def test_a_rotated_token_is_adopted_and_re_remembered(monkeypatch):
    from types import SimpleNamespace

    state = {auth.REMEMBERED_SINCE_KEY: 1_700_000_000.0}
    monkeypatch.setattr(auth.st, "session_state", state)
    monkeypatch.setattr(auth_cookie.st, "session_state", state)
    user = auth.AuthUser(
        id="u", supabase_auth_user_id="s", email="e", full_name="n",
        access_token="old-access", refresh_token="old",
    )
    auth._adopt_tokens(user, SimpleNamespace(access_token="new-access", refresh_token="new"))
    assert (user.access_token, user.refresh_token) == ("new-access", "new")
    cookie = state[auth_cookie._PENDING_KEY]["cookie"]
    assert auth_cookie.encode(1_700_000_000, "new") in cookie


def test_change_password_adopts_the_token_set_session_returns():
    source = inspect.getsource(auth.change_password_form)
    assert source.index("set_session(") < source.index("_adopt_tokens(")
    # set_session sits inside the try, so a spent token is a message.
    assert source.index("try:") < source.index("set_session(")


def test_the_school_name_is_escaped_on_the_sign_in_page():
    assert "html.escape(_school_display_name())" in inspect.getsource(auth.login_form)


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
    """The browser's report stays in session state, so after a logout the
    deleted cookie is still visible — a second try would sign back in.
    The try is marked only once a report exists: the first run of every
    session comes before the browser has said anything."""
    source = inspect.getsource(auth._restore_remembered_session)
    tried = source.index("RESTORE_TRIED_KEY] = True")
    assert source.index("read_raw()") < tried < source.index("refresh_session(")


def test_read_raw_comes_from_the_browser_report_not_st_context(monkeypatch):
    """On Streamlit Community Cloud st.context.cookies never carried the
    cookie; the component's report is the only source."""
    assert "st.context.cookies.get" not in inspect.getsource(auth_cookie)
    state = {}
    monkeypatch.setattr(auth_cookie.st, "session_state", state)
    assert auth_cookie.read_raw() is None
    state[auth_cookie._NAME] = {"value": None}
    assert auth_cookie.read_raw() is None
    state[auth_cookie._NAME] = {"value": "abc"}
    assert auth_cookie.read_raw() == "abc"


def test_rotated_token_is_saved_before_the_database_lookup():
    source = inspect.getsource(auth._restore_remembered_session)
    assert source.index("schedule_set(") < source.index("_load_or_provision_user(")


def test_a_browser_without_the_cookie_still_reports(monkeypatch):
    """An empty report is what lets the server stop waiting. If the JS went
    back to reporting only a found cookie, every signed-out visitor would
    sit on the holding page until GIVE_UP_SECONDS."""
    assert "setStateValue('value', found ? found.slice(prefix.length) : '')" in auth_cookie._JS
    state = {auth_cookie._NAME: {"value": ""}}
    monkeypatch.setattr(auth_cookie.st, "session_state", state)
    assert auth_cookie.has_reported()
    assert auth_cookie.read_raw() is None  # reported, but no cookie
    assert not auth_cookie.awaiting_report()


def test_waiting_for_the_report_is_bounded(monkeypatch):
    state = {}
    monkeypatch.setattr(auth_cookie.st, "session_state", state)
    now = [1000.0]
    monkeypatch.setattr(auth_cookie.time, "time", lambda: now[0])
    assert auth_cookie.awaiting_report()
    now[0] += auth_cookie.GIVE_UP_SECONDS - 1
    assert auth_cookie.awaiting_report()
    now[0] += 2
    assert not auth_cookie.awaiting_report()
    # Gave up for good: a late clock reading can't put it back.
    now[0] = 1000.0
    assert not auth_cookie.awaiting_report()


def test_a_report_ends_the_wait(monkeypatch):
    state = {}
    monkeypatch.setattr(auth_cookie.st, "session_state", state)
    assert auth_cookie.awaiting_report()
    state[auth_cookie._NAME] = {"value": "abc"}
    assert not auth_cookie.awaiting_report()
