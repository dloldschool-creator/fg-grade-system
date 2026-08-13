"""Single code path for provisioning a user — shared by the in-app "Add
User" admin screen and scripts/bootstrap_admin.py (the one-time first-admin
script).

Uses a generated temporary password rather than Supabase's invite-email
link: the invite flow redirects the browser with the session token in the
URL *fragment* (`#access_token=...`), which a JavaScript frontend would
read client-side — a pure-Python Streamlit backend never sees it at all.
Instead, the caller shows the returned temporary password once (never
logged, never sent through this codebase again), the admin relays it to
the person out-of-band, and they change it via the in-app "Change
Password" control on first login (an ordinary authenticated API call —
no email link involved).
"""

import secrets
from dataclasses import dataclass

from app.database import SessionLocal
from app.naming import normalize_name
from app.models.academic_structure import Section
from app.models.rbac import Role, User, UserRole
from app.models.subjects import TeacherAssignment
from app.supabase_clients import get_admin_client


class UserProvisioningError(Exception):
    pass


@dataclass
class ProvisionedUser:
    user_id: str
    email: str
    temporary_password: str
    already_existed: bool


def _generate_temporary_password() -> str:
    return secrets.token_urlsafe(12)


def _create_or_reset_auth_user(email: str, full_name: str) -> ProvisionedUser:
    admin = get_admin_client().auth.admin
    temp_password = _generate_temporary_password()

    existing = next(
        (u for u in admin.list_users() if (u.email or "").lower() == email.lower()), None
    )
    if existing is not None:
        admin.update_user_by_id(existing.id, {"password": temp_password})
        return ProvisionedUser(
            user_id=existing.id, email=email, temporary_password=temp_password, already_existed=True
        )

    response = admin.create_user(
        {
            "email": email,
            "password": temp_password,
            "email_confirm": True,
            "user_metadata": {"full_name": full_name},
        }
    )
    if response.user is None:
        raise UserProvisioningError(f"Supabase did not return a user for {email}")
    return ProvisionedUser(
        user_id=response.user.id,
        email=email,
        temporary_password=temp_password,
        already_existed=False,
    )


def reset_password(email: str) -> ProvisionedUser:
    """Issues a fresh temporary password for an account that already exists.

    Needed because the password is shown exactly once. If the admin loses
    it — the browser closed, the connection dropped mid-create — the
    account is otherwise stranded: nobody knows the password and the
    account cannot be signed into to change it.

    Deliberately separate from `provision_user`, which also grants roles.
    Resetting a password should not be able to alter what someone can do.
    """
    admin = get_admin_client().auth.admin
    existing = next(
        (u for u in admin.list_users() if (u.email or "").lower() == email.lower()), None
    )
    if existing is None:
        raise UserProvisioningError(f"No account exists for {email}.")

    temp_password = _generate_temporary_password()
    admin.update_user_by_id(existing.id, {"password": temp_password})
    return ProvisionedUser(
        user_id=existing.id, email=email, temporary_password=temp_password, already_existed=True
    )


def delete_user(email: str) -> None:
    """Removes the account entirely — the app row, its roles, and the
    Supabase Auth login.

    Refuses while the person still advises a section or is assigned to
    teach, because those are the two foreign keys that block the delete
    and both need a human decision about who takes over.

    Everything historical about them survives: who submitted a grade, who
    finalized a month, and every audit entry are all `ON DELETE SET NULL`,
    so the record keeps its shape and simply stops naming them.
    """
    session = SessionLocal()
    try:
        user = session.query(User).filter_by(email=email).one_or_none()
        if user is not None:
            advises = session.query(Section).filter_by(adviser_user_id=user.id).count()
            if advises:
                raise UserProvisioningError(
                    f"{email} still advises {advises} section(s). Assign a different "
                    "adviser on the Sections page first."
                )
            teaches = (
                session.query(TeacherAssignment).filter_by(teacher_user_id=user.id).count()
            )
            if teaches:
                raise UserProvisioningError(
                    f"{email} still has {teaches} teaching assignment(s). Reassign them "
                    "on the Teacher Assignments page first."
                )
            session.query(UserRole).filter_by(user_id=user.id).delete()
            session.delete(user)
            session.commit()
    finally:
        session.close()

    # Last, and outside the transaction: if this fails the app row is
    # already gone, and a stranded Auth login is recoverable — re-adding
    # the same email resets it. The reverse order could leave someone
    # able to sign in with no account behind it.
    admin = get_admin_client().auth.admin
    existing = next(
        (u for u in admin.list_users() if (u.email or "").lower() == email.lower()), None
    )
    if existing is not None:
        admin.delete_user(existing.id)


def provision_user(email: str, full_name: str, role_codes: list[str]) -> ProvisionedUser:
    """Creates (or resets the password for an existing) Supabase Auth
    account for `email`, links/creates the matching `users` row, and
    grants each role in `role_codes` via `user_roles`. Idempotent for the
    `users`/`user_roles` side — safe to call again for the same
    email/role combination; each call does generate and set a fresh
    temporary password, though, so only call it when you intend to hand
    out a new one."""
    # Same uppercase rule as learner names, so advisers and teachers
    # print consistently on SF2/SF9 signature lines and certificates.
    full_name = normalize_name(full_name) or email
    provisioned = _create_or_reset_auth_user(email, full_name)

    session = SessionLocal()
    try:
        user = (
            session.query(User)
            .filter_by(supabase_auth_user_id=provisioned.user_id)
            .one_or_none()
        )
        if user is None:
            user = User(
                supabase_auth_user_id=provisioned.user_id,
                email=email,
                full_name=full_name,
            )
            session.add(user)
            session.flush()

        for code in role_codes:
            role = session.query(Role).filter_by(code=code).one_or_none()
            if role is None:
                raise UserProvisioningError(f"Unknown role code: {code}")
            existing_grant = (
                session.query(UserRole)
                .filter_by(user_id=user.id, role_id=role.id)
                .one_or_none()
            )
            if existing_grant is None:
                session.add(UserRole(user_id=user.id, role_id=role.id))

        session.commit()
    finally:
        session.close()

    return provisioned
