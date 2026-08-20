"""Audit logging (§50, CLAUDE.md rule 8).

Every sensitive change records **who, what, which object, the previous
value, the new value, when, and why where a reason is required** — plus
IP and user agent where the request context makes them available.

`audit_logs` is append-only by design: this module offers no update or
delete, and no page exposes one either. §50 is explicit that normal
teachers must not be able to delete audit history, and the simplest way
to guarantee that is for the capability not to exist in the application
at all.

Entries are added to the caller's session and committed with it, so an
audit row and the change it describes land together or not at all. A
change that rolls back leaves no misleading audit trail behind.
"""

import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy.orm import Session

from app.models.admin import AuditLog

# --- Action vocabulary -----------------------------------------------------
# Kept as constants rather than free strings so the viewer can filter on
# them and so a typo doesn't silently create a new action.

GRADE_CREATED = "GRADE_CREATED"
GRADE_CHANGED = "GRADE_CHANGED"
GRADE_SUBMITTED = "GRADE_SUBMITTED"
GRADE_FINALIZED = "GRADE_FINALIZED"
GRADE_REOPENED = "GRADE_REOPENED"

ATTENDANCE_CHANGED = "ATTENDANCE_CHANGED"
ATTENDANCE_MONTH_FINALIZED = "ATTENDANCE_MONTH_FINALIZED"
ATTENDANCE_MONTH_REOPENED = "ATTENDANCE_MONTH_REOPENED"

LEARNER_MOVEMENT_RECORDED = "LEARNER_MOVEMENT_RECORDED"
SUBJECT_OFFERING_CHANGED = "SUBJECT_OFFERING_CHANGED"
# Changing a subject's units silently changes every Term Average and
# General Average computed from that point on, and a wrong unit produces a
# plausible wrong number rather than an error (DO 017 s. 2026, Annex E).
# That makes it exactly the kind of change §50 exists to make attributable.
SUBJECT_UNITS_CHANGED = "SUBJECT_UNITS_CHANGED"
CALENDAR_DAY_CHANGED = "CALENDAR_DAY_CHANGED"
AWARD_OVERRIDDEN = "AWARD_OVERRIDDEN"
AWARD_OVERRIDE_CLEARED = "AWARD_OVERRIDE_CLEARED"
USER_ROLES_CHANGED = "USER_ROLES_CHANGED"
# Creating an account and issuing a password are separate facts from
# changing what someone may do, and filing them under USER_ROLES_CHANGED
# would put a claim in the log that isn't true — a password reset alters
# no role. Added 2026-08-16, after a Super Admin looked for the account
# they had just created and found nothing: `provision_user` and
# `reset_password` had never written an entry at all, which is rule 8 and
# §50 both. Sensitive changes made outside a page's own session need the
# recording done where the change is, not where it was requested.
USER_CREATED = "USER_CREATED"
USER_PASSWORD_RESET = "USER_PASSWORD_RESET"
# A teaching assignment is what the Gradebook checks to decide who may
# encode a section's grades, so assigning one grants access and clearing
# one revokes it — §50's "role and permission changes" in everything but
# name. Unlogged until 2026-08-17, which was tolerable while only a Super
# Admin could do it and is not now that an adviser can assign within
# their own section.
TEACHER_ASSIGNED = "TEACHER_ASSIGNED"
TEACHER_UNASSIGNED = "TEACHER_UNASSIGNED"
DATA_IMPORTED = "DATA_IMPORTED"
BACKUP_DOWNLOADED = "BACKUP_DOWNLOADED"

# Actions §50 requires a reason for. Recording one without a reason is a
# programming error, not a user error, so it raises rather than writing a
# row that can't answer "why".
REASON_REQUIRED = {
    GRADE_REOPENED,
    ATTENDANCE_MONTH_REOPENED,
    AWARD_OVERRIDDEN,
    CALENDAR_DAY_CHANGED,
}


def jsonable(value):
    """Coerces a value into something JSONB can hold.

    Grades are Decimal, dates are date, ids are UUID and statuses are
    Enum — none of which psycopg can serialise into JSONB directly, and
    all of which routinely appear in a before/after pair.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        # Grades are whole numbers by construction (§60); keeping them as
        # int makes the log readable rather than "93.00".
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if is_dataclass(value):
        return jsonable(asdict(value))
    return str(value)


def record(
    session: Session,
    *,
    action: str,
    object_type: str,
    object_id,
    user_id=None,
    previous=None,
    new=None,
    reason: str | None = None,
) -> AuditLog:
    """Appends one audit entry to the caller's session.

    Deliberately not committed here: the entry belongs to the same
    transaction as the change it describes, so they succeed or fail
    together.
    """
    if action in REASON_REQUIRED and not (reason or "").strip():
        raise ValueError(f"{action} requires a reason (§50)")

    ip_address, user_agent = _request_metadata()
    entry = AuditLog(
        user_id=user_id,
        action=action,
        object_type=object_type,
        object_id=object_id if isinstance(object_id, uuid.UUID) else uuid.UUID(str(object_id)),
        previous_value=jsonable(previous) if previous is not None else None,
        new_value=jsonable(new) if new is not None else None,
        reason=reason,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.add(entry)
    return entry


def _request_metadata() -> tuple[str | None, str | None]:
    """IP and user agent when running inside a Streamlit request (§50's
    "where appropriate"). Returns (None, None) from a script, a test or a
    background job, where there is no request to describe — that's a
    normal state, not a failure."""
    try:
        import streamlit as st

        context = getattr(st, "context", None)
        if context is None:
            return None, None
        ip_address = getattr(context, "ip_address", None)
        headers = getattr(context, "headers", None) or {}
        user_agent = headers.get("User-Agent") or headers.get("user-agent")
        return ip_address, user_agent
    except Exception:
        # Audit logging must never be the reason a legitimate change
        # fails, and metadata is the optional part of the entry.
        return None, None


def changes(previous: dict, new: dict) -> tuple[dict, dict]:
    """Narrows a before/after pair to just the fields that actually
    differ, so the log reads as "what changed" rather than a full record
    dump on every edit."""
    differing = [k for k in new if previous.get(k) != new.get(k)]
    return (
        {k: previous.get(k) for k in differing},
        {k: new.get(k) for k in differing},
    )


def recent(session: Session, *, limit: int = 200, action: str | None = None,
           object_type: str | None = None, user_id=None) -> list[AuditLog]:
    """Read path for the viewer. There is intentionally no counterpart
    that deletes or edits."""
    query = session.query(AuditLog)
    if action:
        query = query.filter(AuditLog.action == action)
    if object_type:
        query = query.filter(AuditLog.object_type == object_type)
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    return query.order_by(AuditLog.created_at.desc()).limit(limit).all()
