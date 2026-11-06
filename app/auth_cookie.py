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
- **One browser, one cookie.** It is read from the connecting browser's
  own request (`st.context.cookies`), never from anything shared between
  sessions, and nothing here is cached.

Streamlit can read cookies but not write them, so writing is a tiny
`st.components.v2` component (same approach as `app/live_search.py`)
that sets `document.cookie`. It is drawn once per script run by
`streamlit_app.py` and hidden by its CSS. The cookie can't be HttpOnly for that
reason — JavaScript is the only thing that can write it.

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

# Re-invoked on every rerun with the same data, so the write is applied
# once per nonce (remembered on `window`, which a refresh resets along with
# the session itself). The component must stay mounted so this runs even
# when the script reruns straight after scheduling a write, which sign-in
# and logout both do.
_JS = """
export default function(component) {
  const { data } = component;
  if (!data || !data.nonce || !data.cookie) return;
  if (window.__fgnmhsCookieNonce === data.nonce) return;
  document.cookie = data.cookie;
  window.__fgnmhsCookieNonce = data.nonce;
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


def read_raw() -> str | None:
    """The cookie as this browser sent it when it connected. None outside
    a real browser session (tests, AppTest) as well as when absent."""
    try:
        return st.context.cookies.get(COOKIE_NAME) or None
    except Exception:
        return None


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
    """Applies any pending write in the browser. Call once per script run,
    from the entrypoint only, so the component has one stable key."""
    pending = st.session_state.get(_PENDING_KEY)
    if not pending:
        return
    _mount()(key=_NAME, data=pending)
