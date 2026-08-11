import streamlit as st

from app import audit_service
from app.admin_pages._helpers import flash, get_session, render_flashes
from app.auth import require_role
from app.models.rbac import Role, User, UserRole
from app.user_provisioning import UserProvisioningError, provision_user


def render() -> None:
    current_user = require_role("SUPER_ADMIN")
    st.title("Users & Roles")
    render_flashes()

    with get_session() as session:
        roles = session.query(Role).order_by(Role.code).all()
        role_by_id = {r.id: r for r in roles}
        users = session.query(User).order_by(User.full_name).all()

        st.caption("Fixed 6-role catalog (§3), not editable here: " + ", ".join(r.code for r in roles))

        for user in users:
            grants = session.query(UserRole).filter_by(user_id=user.id).all()
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

        last = st.session_state.get("_last_provisioned")
        if last is not None:
            verb = "Password reset for" if last.already_existed else "Created"
            st.success(f"{verb} {last.email}.")
            st.warning("Temporary password (shown once — relay it directly, not by email/chat):")
            st.code(last.temporary_password)
            if st.button("Dismiss"):
                del st.session_state["_last_provisioned"]
                st.rerun()
