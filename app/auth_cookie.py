"""The "Keep me signed in on this device" cookie.

Why it exists: `st.session_state` lives only as long as the browser's
connection to the server. A refresh, a new tab, a laptop waking from
sleep or a Wi-Fi blip starts a new, empty session, which used to mean
signing in again. With the box ticked at sign-in, the browser keeps the
Supabase *refresh token* in a cookie, and `app.auth.get_current_user`
uses it to rebuild the session when the connection is new.

Rules it keeps:

- **Off by default.** Teachers share staffroom machines; a remembered
  session there signs the next person in as the last one, and every grade
  they save is audited under the wrong name.
- **A fixed lifetime from sign-in** (`REMEMBER_HOURS`, 8 — one school
  day), never sliding. Rewriting the cookie with a rotated token keeps
  the original sign-in time, so using the app can't keep it alive
  forever. The server re-checks that time on restore, so a browser that
  ignores the cookie's Expires is still cut off — but the time travels
  *in* the cookie, unsigned, so this bounds an honest browser, not
  someone who already holds the token and edits the cookie. Supabase's
  own session limits are the control for that.
- **Logout and expiry delete it**, and logout also revokes the token at
  Supabase, so a copied cookie stops working.
- **One browser, one cookie.** The value comes from this browser
  session's own component, never from anything shared between sessions,
  and nothing here is cached.

**Both directions go through JavaScript**, a tiny `st.components.v2`
component (same approach as `app/live_search.py`) drawn once per script
run by `streamlit_app.py` and hidden by its CSS. Streamlit can't write a
cookie, and on Streamlit Community Cloud it can't *read* ours either:
`st.context.cookies` never saw it there (found 2026-11-06, the first live
test after v1.9.1 — locally, with no proxy in front, it worked, which is
why the tests passed). So the component reads `document.cookie` and
reports the value once per browser session; the report reruns the script,
and `app.auth` restores from it on that run. The cookie can't be HttpOnly
for the same reason — JavaScript is the only thing that can write or read
it.

**A browser without the cookie reports too**, an empty value, at the cost
of one rerun per browser session (2026-11-06). Until the browser has
spoken the server can't tell "no cookie" from "not reported yet", and
`streamlit_app.py` uses `awaiting_report()` to hold every page's URL open
for that moment instead of drawing the sign-in navigation — which would
throw the URL away, so a refresh of /gradebook landed on the Dashboard.
`GIVE_UP_SECONDS` bounds the wait if the report never comes.

Deliberately dependency-free (only Streamlit and the standard library):
`app.auth` imports it at load, and every page imports `app.auth`, so it
must never reach `app.models` (see CLAUDE.md, "The deployed host runs a
different Python").
"""

import base64
import os
import time
from email.utils import formatdate

import streamlit as st
import streamlit.components.v2 as components

COOKIE_NAME = "fgnmhs_session"
REMEMBER_HOURS = float(os.getenv("REMEMBER_ME_HOURS", "8"))
REMEMBER_SECONDS = int(REMEMBER_HOURS * 3600)

# Bumped if the cookie's format ever changes, so an old cookie is
# discarded rather than misread.
_FORMAT = "v1"

_PENDING_KEY = "auth_cookie_pending"
# One id per browser session, so the browser reports the cookie once per
# session — including a new session in a window that wasn't reloaded (a
# reconnect after the server dropped the old one).
_SID_KEY = "auth_cookie_sid"
# When this session started waiting for the report, and whether it gave up.
_WAIT_SINCE_KEY = "auth_cookie_wait_since"
_GAVE_UP_KEY = "auth_cookie_gave_up"
# The report normally arrives well inside a second; past this, assume the
# component is never going to run and show the sign-in page.
GIVE_UP_SECONDS = 5.0

# Re-invoked on every rerun. The report goes first and once per session id;
# a write is applied once per nonce. Both flags live on `window`, which a
# refresh resets along with the session itself. The component stays
# mounted so a write runs even when the script reruns straight after
# scheduling it, which sign-in and logout both do.
_JS = """
export default function(component) {
  const { data, setStateValue } = component;
  if (!data) return;
  if (data.sid && window.__fgnmhsCookieSid !== data.sid) {
    window.__fgnmhsCookieSid = data.sid;
    const prefix = data.name + '=';
    const found = document.cookie.split('; ').find((c) => c.startsWith(prefix));
    // Empty when there is no cookie: still a report, so the server stops
    // waiting for one (awaiting_report).
    setStateValue('value', found ? found.slice(prefix.length) : '');
  }
  if (data.cookie && window.__fgnmhsCookieNonce !== data.nonce) {
    document.cookie = data.cookie;
    window.__fgnmhsCookieNonce = data.nonce;
  }
}
"""

_NAME = "auth_cookie_sync"
_component = None


def encode(issued_at: float, refresh_token: str) -> str:
    """Cookie value: format, sign-in time and token, base64url so no
    cookie-special character can appear in it."""
    payload = f"{_FORMAT}:{int(issued_at)}:{refresh_token}".encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode(raw: str) -> tuple[float, str] | None:
    """(sign-in time, refresh token), or None for anything malformed."""
    try:
        padded = raw + "=" * (-len(raw) % 4)
        text = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        fmt, issued, token = text.split(":", 2)
    except Exception:
        return None
    if fmt != _FORMAT or not token or not issued.isdigit():
        return None
    return float(issued), token


def is_expired(issued_at: float, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return now - issued_at > REMEMBER_SECONDS or issued_at > now + 300


def _reported_value():
    state = st.session_state.get(_NAME)
    if state is None:
        return None
    return state.get("value") if isinstance(state, dict) else getattr(state, "value", None)


def has_reported() -> bool:
    """True once this browser session's component has reported, cookie or
    not (an empty string means no cookie)."""
    return _reported_value() is not None


def awaiting_report() -> bool:
    """True while the first runs of a browser session are still waiting
    for the browser to say whether it holds the cookie — the window in
    which drawing the sign-in navigation would lose the page in the URL.
    Starts the give-up clock on its first call."""
    if has_reported() or st.session_state.get(_GAVE_UP_KEY):
        return False
    started = st.session_state.setdefault(_WAIT_SINCE_KEY, time.time())
    if time.time() - started > GIVE_UP_SECONDS:
        st.session_state[_GAVE_UP_KEY] = True
        return False
    return True


def render_waiting() -> None:
    """The holding page shown while `awaiting_report()`. Normally the
    browser's report reruns the script within a second and this is never
    seen; the ticking fragment is only the fallback for a report that
    never comes, so nobody is left on a blank page."""
    st.caption("Loading…")

    @st.fragment(run_every=1.0)
    def _tick():
        if not awaiting_report():
            st.rerun(scope="app")

    _tick()


def read_raw() -> str | None:
    """The cookie as this browser session's component reported it, or None
    until it has (and always, for a browser without the cookie).

    Read from the component's state *before* the component is drawn on
    this run: Streamlit applies the browser's report to session state when
    the rerun it triggered starts, so `app.auth` sees it in time to restore
    and to schedule the rotated token's write on the same run.
    Not `st.context.cookies` — see the module docstring."""
    return _reported_value() or None


def _schedule(cookie: str) -> None:
    st.session_state[_PENDING_KEY] = {"cookie": cookie, "nonce": f"{time.time_ns()}"}


def schedule_set(issued_at: float, refresh_token: str) -> None:
    """Write (or rewrite) the cookie, expiring REMEMBER_HOURS after the
    original sign-in."""
    expires = formatdate(issued_at + REMEMBER_SECONDS, usegmt=True)
    _schedule(
        f"{COOKIE_NAME}={encode(issued_at, refresh_token)}; Path=/; "
        f"Expires={expires}; SameSite=Strict; Secure"
    )


def schedule_delete() -> None:
    _schedule(
        f"{COOKIE_NAME}=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; "
        "SameSite=Strict; Secure"
    )


def _mount():
    """Registered with the running runtime, not at import. Same reason as
    `live_search._mount`."""
    global _component
    if _component is None or components.get_bidi_component_manager().get(_NAME) is None:
        # Draws nothing; streamlit_app.py hides its wrapper
        # (.st-key-auth_cookie_sync), which would otherwise add a gap.
        _component = components.component(_NAME, html="", js=_JS)
    return _component


def render_sync() -> None:
    """Reports the browser's cookie (once per session) and applies any
    pending write. Call once per script run, from the entrypoint only, so
    the component has one stable key — and after `get_current_user`, so a
    write it schedules goes out on the same run."""
    if _SID_KEY not in st.session_state:
        st.session_state[_SID_KEY] = f"{time.time_ns()}"
    data = {"name": COOKIE_NAME, "sid": st.session_state[_SID_KEY]}
    data.update(st.session_state.get(_PENDING_KEY) or {})
    _mount()(
        key=_NAME,
        data=data,
        default={"value": None},
        # A callback is what makes the report rerun the script.
        on_value_change=lambda: None,
    )
