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
import uuid
from dataclasses import dataclass, field

from app import audit_service
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


@dataclass
class BulkOutcome:
    """What one bulk run actually did. Three lists rather than a count,
    because a run that half-worked is the normal failure here — each
    account is its own remote call — and the admin has to know which
    third of the file to look at."""

    provisioned: list[ProvisionedUser] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


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


def reset_password(email: str, *, actor_user_id=None) -> ProvisionedUser:
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

    # Re-arms the first-login gate: the account is back on a password an
    # administrator generated, read, and relayed by hand, which is the
    # exact state `users.password_changed_at IS NULL` describes. Without
    # this, "reset" would hand out a shared secret that never has to be
    # replaced. Not a permission change — it alters no role.
    session = SessionLocal()
    try:
        row = session.query(User).filter_by(email=email).one_or_none()
        if row is not None:
            row.password_changed_at = None
            # Handing someone a password for an account that is not theirs
            # is a sensitive change (rule 8) and was not being recorded.
            # The password itself is never written here — only that one was
            # issued, by whom, and for whom.
            audit_service.record(
                session,
                action=audit_service.USER_PASSWORD_RESET,
                object_type="users",
                object_id=row.id,
                user_id=actor_user_id,
                new={"user": row.email, "must_change_password": True},
            )
            session.commit()
    finally:
        session.close()

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


def provision_user(
    email: str, full_name: str, role_codes: list[str], *, actor_user_id=None
) -> ProvisionedUser:
    """Creates (or resets the password for an existing) Supabase Auth
    account for `email`, links/creates the matching `users` row, and
    grants each role in `role_codes` via `user_roles`. Idempotent for the
    `users`/`user_roles` side — safe to call again for the same
    email/role combination; each call does generate and set a fresh
    temporary password, though, so only call it when you intend to hand
    out a new one.

    `actor_user_id` is who is doing it, for the audit entry. It is
    optional because scripts/bootstrap_admin.py creates the very first
    Super Admin when there is nobody to attribute it to — that entry
    records the account with a null actor rather than not existing.
    """
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
        created = user is None
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
                session.add(
                    UserRole(user_id=user.id, role_id=role.id, granted_by_user_id=actor_user_id)
                )

        # Rule 8 and §50. This path had never written an entry, so an
        # account could appear in the system with nothing anywhere saying
        # who made it — noticed by a Super Admin looking for the user they
        # had just created. `created` distinguishes the two things this
        # function does: a new account, or a fresh password for one that
        # already existed (which is what a second press of Create does).
        audit_service.record(
            session,
            action=audit_service.USER_CREATED if created else audit_service.USER_PASSWORD_RESET,
            object_type="users",
            object_id=user.id,
            user_id=actor_user_id,
            new={
                "user": user.email,
                "full_name": user.full_name,
                "roles": sorted(role_codes),
                "created": created,
            },
        )
        session.commit()
    finally:
        session.close()

    return provisioned


def provision_users(rows: list[dict], *, actor_user_id=None) -> BulkOutcome:
    """Creates many accounts in one pass — `rows` of
    `{"email", "full_name", "role_codes"}`, already validated by
    `app/user_import.py`.

    **Not a loop over `provision_user`.** That function asks Supabase for
    the entire user list to find out whether the address is taken, and
    then opens its own session and queries the roles table once per role;
    forty teachers would be forty full listings of every account in the
    school plus a few hundred round trips. Here the listing happens once
    and the whole database side is a single transaction.

    **An address that already has an account is skipped, not reset.**
    `provision_user` resets, which is right for the one-at-a-time form
    where the admin meant that specific person. Re-running a file would
    otherwise invalidate the password of every teacher already in it.

    The remote half cannot be rolled back, so it runs first and to
    completion: each failure is recorded and the rest continue. Only the
    accounts that were really created reach the transaction. If the commit
    then fails, those logins exist without application rows — re-uploading
    the same file repairs it, because the second run sees them as existing
    and `provision_user` on the individual address links the row up.
    """
    admin = get_admin_client().auth.admin
    # One listing for the whole file.
    taken = {(u.email or "").lower() for u in admin.list_users()}

    outcome = BulkOutcome()
    wanted_roles: dict[str, list[str]] = {}
    names: dict[str, str] = {}

    for row in rows:
        email = str(row["email"]).strip()
        full_name = normalize_name(row.get("full_name")) or email
        if email.lower() in taken:
            outcome.skipped.append(email)
            continue

        temp_password = _generate_temporary_password()
        try:
            response = admin.create_user(
                {
                    "email": email,
                    "password": temp_password,
                    "email_confirm": True,
                    "user_metadata": {"full_name": full_name},
                }
            )
        except Exception as exc:  # one bad address must not strand the rest
            outcome.failed.append((email, str(exc)))
            continue
        if response.user is None:
            outcome.failed.append((email, "Supabase did not return a user"))
            continue

        # Guards against a file that repeats an address in a form the
        # validator's own duplicate check didn't catch (different case,
        # say) — the second create would otherwise be attempted.
        taken.add(email.lower())
        outcome.provisioned.append(
            ProvisionedUser(
                user_id=response.user.id,
                email=email,
                temporary_password=temp_password,
                already_existed=False,
            )
        )
        wanted_roles[response.user.id] = list(row.get("role_codes") or [])
        names[response.user.id] = full_name

    if not outcome.provisioned:
        return outcome

    session = SessionLocal()
    try:
        # Both loaded once, above the loop: at forty accounts, a role
        # lookup per row is the difference between one round trip and a
        # hundred.
        roles_by_code = {r.code: r for r in session.query(Role).all()}
        auth_ids = [uuid.UUID(str(p.user_id)) for p in outcome.provisioned]
        existing_rows = {
            str(u.supabase_auth_user_id): u
            for u in session.query(User).filter(User.supabase_auth_user_id.in_(auth_ids)).all()
        }

        created: list[tuple[User, list[str]]] = []
        for provisioned in outcome.provisioned:
            user = existing_rows.get(str(provisioned.user_id))
            if user is None:
                user = User(
                    supabase_auth_user_id=uuid.UUID(str(provisioned.user_id)),
                    email=provisioned.email,
                    full_name=names.get(provisioned.user_id) or provisioned.email,
                )
                session.add(user)
            created.append((user, wanted_roles.get(provisioned.user_id, [])))

        # One flush for every new user, not one each: this codebase
        # declares no relationship() between `users` and `user_roles`, so
        # SQLAlchemy cannot work out that the grant needs the user's id
        # first. Flushing here is what makes those ids exist.
        session.flush()

        for user, codes in created:
            for code in codes:
                role = roles_by_code.get(code)
                if role is None:
                    # validate_users rejects an unknown code before this
                    # runs, so this is a belt-and-braces skip rather than
                    # a reason to abandon accounts already created.
                    continue
                session.add(
                    UserRole(user_id=user.id, role_id=role.id, granted_by_user_id=actor_user_id)
                )
            # §50's "user permission changed" — the entry that explains
            # how a batch of people came to be able to do anything.
            audit_service.record(
                session,
                action=audit_service.USER_CREATED,
                object_type="users",
                object_id=user.id,
                user_id=actor_user_id,
                new={"user": user.email, "roles": sorted(codes), "created": True, "bulk": True},
            )

        session.commit()
    finally:
        session.close()

    return outcome
