import pandas as pd
import streamlit as st

from app import audit_service
from app.admin_pages._helpers import (
    clear_text_fields,
    flash,
    get_session,
    keep_panel_open,
    panel_is_open,
    render_flashes,
    text_field,
)
from app.auth import require_role
from app.display_time import format_time
from app.import_pipeline import apply_mapping, missing_required, read_table, suggest_mapping
from app.models.rbac import Role, User, UserRole
from app.user_import import (
    USER_COLUMNS,
    USER_FILE,
    partition_existing,
    template_bytes,
    validate_users,
)
from app.user_provisioning import (
    UserProvisioningError,
    delete_user,
    provision_user,
    provision_users,
    reset_password,
)

MAX_ERRORS_SHOWN = 50


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


def _account_state(user) -> str:
    """Whether this account has been used, and whether its holder still
    has the password you gave them.

    Both read straight off the `users` row the page already loaded — the
    point of storing them there rather than asking Supabase per panel.

    "Never signed in" is only true going forward: `last_login_at` was
    never written until 2026-08-16, so an account created before then
    shows it until its holder next signs in.
    """
    if user.last_login_at is None:
        signed_in = "Never signed in"
    else:
        signed_in = f"Last signed in {format_time(user.last_login_at)}"

    if user.password_changed_at is None:
        password = (
            "🔑 **still on the temporary password** — they'll be asked to choose "
            "their own before they can use the app"
        )
    else:
        password = f"Password set by them {format_time(user.password_changed_at)}"
    return f"{signed_in} · {password}"


def _show_bulk_result() -> None:
    """The passwords a bulk run produced, shown once.

    Same rule as the single-account case and the same reason: nothing here
    is stored anywhere readable, so an admin who navigates away before
    copying them has to reset each account by hand. Deliberately a copyable
    block rather than a download — a spreadsheet of live passwords in the
    Downloads folder is exactly the thing this flow is trying to avoid.
    """
    outcome = st.session_state.get("_last_bulk_provisioned")
    if outcome is None:
        return

    if outcome.provisioned:
        st.success(f"Created {len(outcome.provisioned)} account(s).")
        st.warning(
            "Temporary passwords — shown once. Copy them now and hand each one over "
            "in person or by phone, not by email or chat.",
            icon="🔑",
        )
        st.code(
            "\n".join(f"{p.email}\t{p.temporary_password}" for p in outcome.provisioned),
            language=None,
        )
        st.caption(
            "Each person signs in with theirs, then sets their own from "
            "**Change Password** in the sidebar."
        )

    if outcome.skipped:
        st.info(
            f"{len(outcome.skipped)} address(es) already had an account and were left "
            "untouched: " + ", ".join(outcome.skipped) + ". Their passwords are "
            "unchanged — use **Reset password** on the person's own panel above if "
            "that's what you meant."
        )

    if outcome.failed:
        st.error(f"{len(outcome.failed)} account(s) could not be created.")
        st.table([{"Email": email, "Problem": message} for email, message in outcome.failed])
        st.caption(
            "The rest of the file was created. Fix these rows and upload just them — "
            "the accounts that worked will be skipped."
        )

    if st.button("Done — hide these"):
        del st.session_state["_last_bulk_provisioned"]
        st.rerun()
    st.divider()


def _bulk_add(current_user, roles, existing_emails) -> None:
    """Upload an .xlsx of email / full name / roles and create the lot.

    `roles` and `existing_emails` come from the queries the page already
    ran to draw itself, so checking a file costs **no** extra round trips
    — which matters because Streamlit re-runs this whole function on every
    click anywhere on the page, upload still in hand.
    """
    st.subheader("Bulk add from Excel")
    st.caption(
        "One row per person. The first row must be the column headers. Nothing is "
        "created until you confirm, and an address that already has an account is "
        "skipped rather than reset."
    )

    role_codes = [r.code for r in roles]
    st.table(
        [
            {
                "Column": column.label,
                "Required": "yes" if column.required else "optional",
                "Example": example,
            }
            for column, example in zip(
                USER_COLUMNS,
                (
                    "juan.delacruz@deped.gov.ph",
                    "JUAN P. DELA CRUZ",
                    "ADVISER, SUBJECT_TEACHER",
                ),
            )
        ]
    )
    st.caption(
        "Roles go in one cell, separated by commas. Leave the cell blank and the "
        "account is created with no role — they can sign in but see nothing until "
        "you grant one above. Valid codes: " + ", ".join(sorted(role_codes)) + "."
    )
    st.download_button(
        "Download the blank template (.xlsx)",
        data=template_bytes(),
        file_name="users-template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    uploaded = st.file_uploader("Excel file (.xlsx)", type=["xlsx"], key="upload_users")
    if uploaded is None:
        return

    headers, rows = read_table(uploaded.getvalue(), uploaded.name)
    if not rows:
        st.error("No data rows found — is the first row the header?")
        return

    # Matched on the file's own headers and each column's known spellings.
    # Three columns doesn't justify the Import page's manual mapping step;
    # if a required one can't be found, say which and what it may be called.
    mapping = suggest_mapping(headers, USER_FILE)
    absent = missing_required(mapping, USER_FILE)
    if absent:
        st.error(
            f"Couldn't find these column(s) in the file: {', '.join(absent)}. "
            f"Its headers are: {', '.join(h for h in headers if h)}."
        )
        return

    mapped = apply_mapping(rows, mapping)
    result = validate_users(
        mapped, role_codes=role_codes, existing_emails=existing_emails
    )
    to_create, already = partition_existing(result.parsed)

    col1, col2, col3 = st.columns(3)
    col1.metric("Rows read", len(mapped))
    col2.metric("To create", len(to_create))
    col3.metric("Errors", len(result.errors))

    if result.errors:
        st.error(
            f"{len(result.errors)} problem(s) found. Nothing has been created — fix "
            "the file and upload it again."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {"Row": e.row_number, "Column": e.column or "", "Problem": e.message}
                    for e in result.errors[:MAX_ERRORS_SHOWN]
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
        if len(result.errors) > MAX_ERRORS_SHOWN:
            st.caption(f"…and {len(result.errors) - MAX_ERRORS_SHOWN} more.")
        return

    if already:
        st.info(
            f"{len(already)} row(s) already have an account and will be skipped: "
            + ", ".join(r["email"] for r in already)
        )

    if not to_create:
        st.warning("Nothing left to create — every address in the file already has an account.")
        return

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Email": r["email"],
                    "Full name": r["full_name"],
                    "Roles": ", ".join(r["role_codes"]) or "— none —",
                }
                for r in to_create
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )

    without_roles = sum(1 for r in to_create if not r["role_codes"])
    if without_roles:
        st.warning(
            f"{without_roles} of these have no role, so they'll be able to sign in "
            "and nothing else until you grant one."
        )

    if st.button(f"Create {len(to_create)} account(s)", type="primary"):
        try:
            outcome = provision_users(to_create, actor_user_id=current_user.id)
        except UserProvisioningError as exc:
            flash("error", str(exc))
        else:
            st.session_state["_last_bulk_provisioned"] = outcome
            flash(
                "success",
                f"Created {len(outcome.provisioned)} account(s) — the temporary "
                "passwords are at the top of this page. Copy them before doing "
                "anything else.",
            )
        st.rerun()


def render() -> None:
    current_user = require_role("SUPER_ADMIN")
    st.title("Users & Roles")
    render_flashes()
    # Shown at the top, not beside whichever control produced it: the
    # password appears exactly once, and a reset triggered from a user
    # far down a long list would otherwise scroll off the screen.
    _show_temporary_password()
    _show_bulk_result()

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
            with st.expander(
                f"{user.full_name} — {user.email}  "
                f"[{', '.join(sorted(current_codes)) or 'no role'}]"
                f"{'  🔑' if user.password_changed_at is None else ''}",
                expanded=panel_is_open(user.id),
            ):
                # Both columns are on the row already loaded above, so
                # this costs nothing extra — no query per panel.
                st.caption(_account_state(user))
                col1, col2 = st.columns(2)
                is_active = col1.checkbox(
                    "Active", value=user.is_active, key=f"user_active_{user.id}",
                    on_change=keep_panel_open, args=(user.id,),
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
                    # Without this, changing the roles collapsed the panel
                    # and took the Save roles button with it.
                    on_change=keep_panel_open, args=(user.id,),
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
                            st.session_state["_last_provisioned"] = reset_password(
                                user.email, actor_user_id=current_user.id
                            )
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
                        # Ticking this enables Delete — and used to close
                        # the panel before it could be reached.
                        on_change=keep_panel_open, args=(user.id,),
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
            "Creates the account and shows a temporary password once. Give it to the "
            "person directly — they set their own the first time they sign in. "
            "**Pressing Create twice for the same email issues a second password and "
            "invalidates the first**, because an email that already has an account is "
            "reset rather than duplicated. The boxes empty themselves after a "
            "successful create so that can't happen by accident."
        )
        with st.form("add_user"):
            email = text_field("Email", key="add_user.email")
            full_name = text_field("Full name", key="add_user.full_name")
            role_choice = st.multiselect(
                "Roles", options=[r.id for r in roles], format_func=lambda v: role_by_id[v].code
            )
            if st.form_submit_button("Create"):
                if not email or not full_name:
                    st.error("Email and full name are required.")
                else:
                    try:
                        result = provision_user(
                            email,
                            full_name,
                            [role_by_id[rid].code for rid in role_choice],
                            actor_user_id=current_user.id,
                        )
                    except UserProvisioningError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["_last_provisioned"] = result
                        # Toasts over wherever you are looking, which is
                        # down here by the button and not up there by the
                        # password. render_flashes handles that.
                        flash(
                            "success",
                            f"Created {result.email} — the temporary password is at "
                            "the top of this page. Copy it before doing anything else.",
                        )
                        # Both halves matter, and this form had neither.
                        # The password renders at the *top* of a page this
                        # form sits at the bottom of, so pressing Create
                        # changed nothing you could see from here — and
                        # the boxes kept the email, so pressing it again
                        # was the natural thing to do. That reset the
                        # account and invalidated the password already
                        # written down. Diagnosed 2026-08-16 from an
                        # auth.users row updated 40s after it was created.
                        clear_text_fields("add_user")
                        st.rerun()

        st.divider()
        # Given the roles and the email list the page already loaded, so a
        # file is checked without a single extra query.
        _bulk_add(current_user, roles, {u.email.lower() for u in users if u.email})

