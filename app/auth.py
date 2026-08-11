"""Login/session/RBAC helpers for the Streamlit app.

Known limitation: st.session_state resets on a hard page refresh or new
browser tab, so logging in again is needed after either. Not solved this
pass — see docs/schema.md / CLAUDE.md history for why.
"""

from dataclasses import dataclass, field

import streamlit as st

from app.database import SessionLocal
from app.naming import normalize_name
from app.models.rbac import Role, User, UserRole
from app.supabase_clients import get_anon_client

SESSION_KEY = "auth_user"


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
    return st.session_state.get(SESSION_KEY)


def logout() -> None:
    try:
        get_anon_client().auth.sign_out()
    except Exception:
        pass  # best-effort — clearing local session state is what actually matters
    st.session_state.pop(SESSION_KEY, None)


def login_form() -> None:
    st.title("FGNMHS Grading System — Admin Login")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if not submitted:
        return

    if not email or not password:
        st.error("Enter both email and password.")
        return

    try:
        response = get_anon_client().auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except Exception as exc:
        st.error(f"Login failed: {exc}")
        return

    if response.user is None or response.session is None:
        st.error("Login failed — check your email and password.")
        return

    full_name = (response.user.user_metadata or {}).get("full_name", "")
    st.session_state[SESSION_KEY] = _load_or_provision_user(
        response.user.id,
        response.user.email,
        full_name,
        response.session.access_token,
        response.session.refresh_token,
    )
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
