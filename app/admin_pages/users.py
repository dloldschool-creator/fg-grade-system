import streamlit as st

from app import audit_service
from app.admin_pages._helpers import flash, get_session, render_flashes
from app.auth import require_role
from app.models.rbac import Role, User, UserRole
from app.user_provisioning import (
    UserProvisioningError,
    delete_user,
    provision_user,
    reset_password,
)


def _show_temporary_password() -> None:
    """Displays a just-issued password, once.

    It is never stored anywhere readable, so if this is missed the only
    way back is another reset. It stays on screen until dismissed rather
    than vanishing on the next rerun, because losing it is exactly how
    accounts get stranded.
    """
    last = st.session_state.get("_last_provisioned")
    if last is None:
        return
    verb = "Password reset for" if last.already_existed else "Created"
    st.success(f"{verb} {last.email}.")
    st.warning(
        "Temporary password — shown once. Copy it now and hand it over in "
        "person or by phone, not by email or chat.",
        icon="🔑",
    )
    st.code(last.temporary_password)
    st.caption(
        "They sign in with this, then set their own from **Change Password** "
        "in the sidebar."
    )
    if st.button("Done — hide this"):
        del st.session_state["_last_provisioned"]
        st.rerun()
    st.divider()


def render() -> None:
    current_user = require_role("SUPER_ADMIN")
    st.title("Users & Roles")
    render_flashes()
    # Shown at the top, not beside whichever control produced it: the
    # password appears exactly once, and a reset triggered from a user
    # far down a long list would otherwise scroll off the screen.
    _show_temporary_password()

    with get_session() as session:
        roles = session.query(Role).order_by(Role.code).all()
        role_by_id = {r.id: r for r in roles}
        users = session.query(User).order_by(User.full_name).all()

        st.caption("Available roles: " + ", ".join(r.code for r in roles))

        # One query for every grant rather than one per user. Forty
        # teacher accounts is forty round trips otherwise, on every rerun.
        grants_by_user: dict = {}
        for grant in session.query(UserRole).all():
            grants_by_user.setdefault(grant.user_id, []).append(grant)

        for user in users:
            grants = grants_by_user.get(user.id, [])
            current_codes = {role_by_id[g.role_id].code for g in grants}
            with st.expander(f"{user.full_name} — {user.email}  [{', '.join(sorted(current_codes)) or 'no role'}]"):
                col1, col2 = st.columns(2)
                is_active = col1.checkbox(
                    "Active", value=user.is_active, key=f"user_active_{user.id}"
                )
                if is_active != user.is_active:
                    audit_service.record(
                        session,
                        action=audit_service.USER_ROLES_CHANGED,
                        object_type="users",
                        object_id=user.id,
                        user_id=current_user.id,
                        previous={"is_active": user.is_active, "user": user.email},
                        new={"is_active": is_active},
                    )
                    user.is_active = is_active
                    session.commit()
                    st.rerun()

                selected = st.multiselect(
                    "Roles",
                    options=[r.id for r in roles],
                    default=[g.role_id for g in grants],
                    format_func=lambda v: role_by_id[v].code,
                    key=f"user_roles_{user.id}",
                )
                if st.button("Save roles", key=f"save_roles_{user.id}"):
                    existing_role_ids = {g.role_id for g in grants}
                    selected_set = set(selected)
                    for role_id in selected_set - existing_role_ids:
                        session.add(UserRole(user_id=user.id, role_id=role_id))
                    for grant in grants:
                        if grant.role_id not in selected_set:
                            session.delete(grant)
                    new_codes = {role_by_id[r].code for r in selected_set}
                    if new_codes != current_codes:
                        # §50's "user permission changed" — the one entry
                        # that explains how somebody came to be able to do
                        # everything else in this log.
                        audit_service.record(
                            session,
                            action=audit_service.USER_ROLES_CHANGED,
                            object_type="users",
                            object_id=user.id,
                            user_id=current_user.id,
                            previous={"roles": sorted(current_codes), "user": user.email},
                            new={"roles": sorted(new_codes)},
                        )
                    session.commit()
                    flash("success", "Roles updated.")
                    st.rerun()

                st.divider()
                col_reset, col_delete = st.columns(2)

                with col_reset:
                    st.caption(
                        "Issues a new temporary password. Use this if the one shown "
                        "when the account was created was never written down."
                    )
                    if st.button("Reset password", key=f"reset_{user.id}"):
                        try:
                            st.session_state["_last_provisioned"] = reset_password(user.email)
                        except UserProvisioningError as exc:
                            flash("error", str(exc))
                        st.rerun()

                with col_delete:
                    st.caption(
                        "Removes the account and its sign-in. Work they recorded — "
                        "grades, attendance, the audit trail — is kept."
                    )
                    confirmed = st.checkbox(
                        "I'm sure", key=f"confirm_delete_{user.id}",
                        help="Deleting cannot be undone.",
                    )
                    if st.button(
                        "Delete user", key=f"delete_{user.id}", disabled=not confirmed,
                        type="secondary",
                    ):
                        if user.id == current_user.id:
                            flash("error", "You can't delete the account you're signed in with.")
                        else:
                            try:
                                delete_user(user.email)
                            except UserProvisioningError as exc:
                                flash("error", str(exc))
                            else:
                                audit_service.record(
                                    session,
                                    action=audit_service.USER_ROLES_CHANGED,
                                    object_type="users",
                                    object_id=user.id,
                                    user_id=current_user.id,
                                    previous={"user": user.email, "roles": sorted(current_codes)},
                                    new={"deleted": True},
                                )
                                session.commit()
                                flash("success", f"Deleted {user.email}.")
                        st.rerun()

        st.divider()
        st.subheader("Add user")
        st.caption(
            "Creates the account with a generated temporary password (shown once below) — "
            "relay it to the person directly; they change it via Change Password after "
            "logging in. If the email already has a Supabase Auth account, its password is "
            "reset instead of creating a duplicate."
        )
        with st.form("add_user"):
            email = st.text_input("Email")
            full_name = st.text_input("Full name")
            role_choice = st.multiselect(
                "Roles", options=[r.id for r in roles], format_func=lambda v: role_by_id[v].code
            )
            if st.form_submit_button("Create"):
                if not email or not full_name:
                    st.error("Email and full name are required.")
                else:
                    try:
                        result = provision_user(
                            email, full_name, [role_by_id[rid].code for rid in role_choice]
                        )
                    except UserProvisioningError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["_last_provisioned"] = result

