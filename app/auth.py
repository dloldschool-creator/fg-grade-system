"""Login/session/RBAC helpers for the Streamlit app.

Known limitation: st.session_state resets on a hard page refresh or new
browser tab, so logging in again is needed after either. Not solved this
pass — see docs/schema.md / CLAUDE.md history for why.
"""

import base64
import os
import time
from dataclasses import dataclass, field
from functools import lru_cache

import streamlit as st

from app.database import SessionLocal
from app.naming import normalize_name
from app.models.rbac import Role, User, UserRole
from app.supabase_clients import get_anon_client

SESSION_KEY = "auth_user"
LAST_SEEN_KEY = "auth_last_seen"

# Roles that may change official data. SCHOOL_HEAD is deliberately absent
# (§3E) — see AuthUser.is_read_only. This is now every role there is:
# ATTENDANCE_ENCODER was removed on 2026-08-16 (advisers encode their own
# section's attendance), so a role that grants no editing is the read-only
# viewer and nothing else. That removal is also why the School Head is
# §3E and not §3F — the withdrawn role's letter was closed up.
EDITING_ROLES = frozenset({"SUPER_ADMIN", "REGISTRAR", "ADVISER", "SUBJECT_TEACHER"})

# §53's "inactivity/session timeout where appropriate". Teachers encode
# grades on shared staffroom machines, so an abandoned tab shouldn't stay
# signed in — but the window has to be long enough to survive a class
# period of reading a roster without typing. 60 minutes is the compromise;
# override with SESSION_TIMEOUT_MINUTES.
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "60"))
TIMED_OUT_FLAG = "auth_timed_out"


@dataclass
class AuthUser:
    id: str  # our users.id (UUID, as str)
    supabase_auth_user_id: str
    email: str
    full_name: str
    access_token: str
    refresh_token: str
    role_codes: set[str] = field(default_factory=set)

    def has_role(self, *codes: str) -> bool:
        """SUPER_ADMIN implicitly satisfies any role check — a Super Admin
        shouldn't need a separate explicit grant just to reach a page.
        Note this only gates *page access*; a page's own data query (e.g.
        Gradebook filtering by teacher_assignments.teacher_user_id) still
        scopes to what that user is actually assigned to, so a Super
        Admin without a real assignment sees an empty/no-data state, not
        someone else's records."""
        if "SUPER_ADMIN" in self.role_codes:
            return True
        return bool(self.role_codes.intersection(codes))

    def is_read_only(self) -> bool:
        """True for a School Head with no working role alongside it.

        §3E is explicit that this role "cannot change official data", so
        the check is deliberately *positive* about who may edit rather
        than listing what a viewer can't do: a role added later is
        read-only here until someone decides otherwise, which is the safe
        direction to fail.

        Someone holding School Head *and* a working role (a principal who
        also advises a section) edits normally — the read-only status
        describes the account, not the title.
        """
        if not self.role_codes:
            return True
        return not self.role_codes.intersection(EDITING_ROLES)


def _load_or_provision_user(
    supabase_auth_user_id: str, email: str, full_name: str, access_token: str, refresh_token: str
) -> AuthUser:
    """Looks up the `users` row for this Supabase Auth account, creating
    one (with no roles) on first login. A brand-new row has no role until
    an existing Super Admin grants one via the Users screen."""
    session = SessionLocal()
    try:
        user = (
            session.query(User).filter_by(supabase_auth_user_id=supabase_auth_user_id).one_or_none()
        )
        if user is None:
            user = User(
                supabase_auth_user_id=supabase_auth_user_id,
                email=email,
                full_name=normalize_name(full_name) or email,
            )
            session.add(user)
            session.flush()
            session.commit()

        role_codes = {
            role.code
            for role in (
                session.query(Role)
                .join(UserRole, UserRole.role_id == Role.id)
                .filter(UserRole.user_id == user.id)
                .all()
            )
        }
        return AuthUser(
            id=str(user.id),
            supabase_auth_user_id=supabase_auth_user_id,
            email=user.email,
            full_name=user.full_name,
            access_token=access_token,
            refresh_token=refresh_token,
            role_codes=role_codes,
        )
    finally:
        session.close()


def get_current_user() -> AuthUser | None:
    """Also enforces the inactivity timeout (§53).

    Every page calls this through `require_role`, and Streamlit re-runs the
    whole script on each interaction — so this function sees every action
    the user takes, which makes it the natural place to both check the
    idle window and refresh it. A session that has gone past the window is
    cleared here rather than merely hidden, so the tokens go too."""
    user = st.session_state.get(SESSION_KEY)
    if user is None:
        return None

    now = time.time()
    last_seen = st.session_state.get(LAST_SEEN_KEY)
    if last_seen is not None and (now - last_seen) > SESSION_TIMEOUT_MINUTES * 60:
        logout()
        st.session_state[TIMED_OUT_FLAG] = True
        return None

    st.session_state[LAST_SEEN_KEY] = now
    return user


def logout() -> None:
    try:
        get_anon_client().auth.sign_out()
    except Exception:
        pass  # best-effort — clearing local session state is what actually matters
    st.session_state.pop(SESSION_KEY, None)
    st.session_state.pop(LAST_SEEN_KEY, None)


SEAL_PATH = os.path.join(os.path.dirname(__file__), "assets", "fgnmhs_seal.png")

# Shown if the school record can't be read (first run before seeding, or
# the database being unreachable) — the sign-in page should still render
# something sensible rather than an error.
FALLBACK_SCHOOL_NAME = "Francisco G. Nepomuceno Memorial High School"

_LOGIN_STYLES = """
<style>
  /* Scoped to .login-heading, which only the sign-in page draws. */
  .login-heading { text-align: center; margin-bottom: 0.25rem; }
  /* The seal is an <img> inside this block rather than st.image(), so it
     inherits the centring above. Targeting Streamlit's own image wrapper
     with CSS is brittle — its test id and DOM nesting change between
     releases, and it didn't centre reliably here. */
  .login-heading img.seal {
      width: 96px; height: auto;
      display: block; margin: 0 auto 0.35rem auto;
  }
  .login-heading h1 {
      font-size: 1.55rem; line-height: 1.3; font-weight: 700;
      margin: 0.6rem 0 0.2rem 0;
  }
  .login-heading p { margin: 0; opacity: 0.75; font-size: 0.95rem; }
  .login-heading .eyebrow {
      text-transform: uppercase; letter-spacing: 0.14em;
      font-size: 0.7rem; opacity: 0.6;
  }
</style>
"""


@lru_cache(maxsize=1)
def _seal_img_tag() -> str:
    """The seal as an inline data URI so it can live inside the centred
    heading block. Cached — the file never changes at runtime, and this
    would otherwise re-read and re-encode on every rerun."""
    if not os.path.exists(SEAL_PATH):
        return ""
    with open(SEAL_PATH, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return f'<img class="seal" src="data:image/png;base64,{encoded}" alt="" />'


def _school_display_name() -> str:
    """Reads the school name so renaming it on School Info flows through
    to the sign-in page. Falls back rather than raising: if the database
    is unreachable the page should still render (login will fail with a
    clear message when submitted, which is more useful than a stack
    trace on load)."""
    try:
        from app.models.organization import School

        session = SessionLocal()
        try:
            school = session.query(School).first()
            return school.school_name if school else FALLBACK_SCHOOL_NAME
        finally:
            session.close()
    except Exception:
        return FALLBACK_SCHOOL_NAME


def login_form() -> None:
    st.markdown(_LOGIN_STYLES, unsafe_allow_html=True)

    # Constrain the form to a readable column — the app runs in "wide"
    # layout, which would otherwise stretch two inputs across the screen.
    _, centre, _ = st.columns([1, 1.4, 1])
    with centre:
        st.markdown(
            f"""
            <div class="login-heading">
              {_seal_img_tag()}
              <div class="eyebrow">Senior High School</div>
              <h1>{_school_display_name()}</h1>
              <p>Grading, Attendance &amp; Forms System</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.write("")

        if st.session_state.pop(TIMED_OUT_FLAG, False):
            st.info(
                f"You were signed out after {SESSION_TIMEOUT_MINUTES} minutes of "
                "inactivity. Please sign in again."
            )

        with st.form("login_form"):
            email = st.text_input("Email", placeholder="name@deped.gov.ph")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", use_container_width=True)

        st.caption(
            "Use the account issued to you by the school. If you were given a "
            "temporary password, you can change it from the sidebar once signed in."
        )
        # Also shown here, not just in the sidebar: after a push the first
        # thing you see is this page, and checking whether the deploy
        # landed shouldn't require signing in first.
        from app.version import version_line

        st.caption(version_line())

    if not submitted:
        return

    # Errors render in the same narrow column as the form, not full-width
    # across the wide layout.
    with centre:
        if not email or not password:
            st.error("Enter both email and password.")
            return

        try:
            response = get_anon_client().auth.sign_in_with_password(
                {"email": email, "password": password}
            )
        except Exception as exc:
            st.error(f"Sign-in failed: {exc}")
            return

        if response.user is None or response.session is None:
            st.error("Sign-in failed — check your email and password.")
            return

    full_name = (response.user.user_metadata or {}).get("full_name", "")
    st.session_state[SESSION_KEY] = _load_or_provision_user(
        response.user.id,
        response.user.email,
        full_name,
        response.session.access_token,
        response.session.refresh_token,
    )
    st.session_state[LAST_SEEN_KEY] = time.time()
    st.rerun()


def change_password_form() -> None:
    """Renders inline (e.g. in the sidebar) for any logged-in user —
    needed for someone who just got a generated temporary password from
    an admin (see app/user_provisioning.py) to set their own."""
    user = get_current_user()
    if user is None:
        return
    with st.expander("Change password"):
        with st.form("change_password_form"):
            new_password = st.text_input("New password", type="password")
            confirm_password = st.text_input("Confirm new password", type="password")
            if st.form_submit_button("Update password"):
                if not new_password:
                    st.error("Enter a new password.")
                elif new_password != confirm_password:
                    st.error("Passwords don't match.")
                else:
                    client = get_anon_client()
                    client.auth.set_session(user.access_token, user.refresh_token)
                    try:
                        client.auth.update_user({"password": new_password})
                    except Exception as exc:
                        st.error(f"Couldn't update password: {exc}")
                    else:
                        st.success("Password updated.")


def require_role(*codes: str) -> AuthUser:
    """Call at the top of a page's render function. Stops the page (and
    shows a message) if the current user doesn't hold any of `codes`."""
    user = get_current_user()
    if user is None:
        st.warning("Please log in.")
        st.stop()
    if not user.has_role(*codes):
        st.error("You don't have access to this page.")
        st.stop()
    return user
