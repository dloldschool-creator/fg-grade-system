"""Archiving old audit_logs entries (§50) — export, then delete, never a bare purge.

`app/audit_service.py` is deliberately append-only: no code anywhere updates
or deletes a row, because §50 requires that normal users can't erase audit
history. This module is the one deliberate exception to that, added when
the viewer (`app/admin_pages/audit_log.py`) only ever showed the newest
`PAGE_SIZE` rows with no pagination — at 543 entries, older ones were
already unreachable on screen even though nothing had deleted them. The
viewer is paginated now, so every entry is reachable; this module still
exists for the case pagination doesn't help — a log grown large enough
that the table itself becomes the cost, not just reaching the old rows.

Three things keep this from weakening §50's guarantee rather than just
moving it:

- **Never the recent window.** `MIN_AGE_DAYS` below is a hard floor —
  `delete_before` refuses a cutoff newer than that, regardless of who asks.
- **The caller must hold a matching export.** `delete_before` takes the row
  count the caller exported and re-counts before touching anything; a
  mismatch (a concurrent write, or a stale export from a different cutoff)
  refuses rather than silently deleting a different set than what was
  downloaded.
- **The deletion audits itself.** `delete_before` writes an
  `AUDIT_LOG_ARCHIVED` entry — timestamped now, so newer than the cutoff —
  before deleting anything, in the same transaction. That row, with its
  required reason, is the only trace left of who did this.
"""

import csv
import io
from datetime import datetime, timedelta, timezone

from app import audit_service
from app.models.admin import AuditLog
from app.models.organization import School
from app.models.rbac import User

# Roughly one term (Term 1 closes 2026-09-15 per CLAUDE.md) — a fresh
# archive can never eat into the term just finished, only older history.
MIN_AGE_DAYS = 90

# Where the page suggests archiving, not a hard cap. Postgres and Supabase's
# free tier both handle far more rows than this; the constraint being
# addressed is the viewer, not storage.
SUGGEST_THRESHOLD = 5000


def max_cutoff() -> datetime:
    """The newest `before` value archiving will ever accept."""
    return datetime.now(timezone.utc) - timedelta(days=MIN_AGE_DAYS)


def count_before(session, before: datetime) -> int:
    return session.query(AuditLog).filter(AuditLog.created_at < before).count()


def export_csv(session, before: datetime) -> tuple[bytes, int]:
    """Every column of every entry older than `before`, oldest first — the
    downloadable record the admin is expected to keep once these rows leave
    the database. Returns (csv bytes, row count)."""
    users = {u.id: u.full_name for u in session.query(User).all()}
    rows = (
        session.query(AuditLog)
        .filter(AuditLog.created_at < before)
        .order_by(AuditLog.created_at.asc())
        .all()
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "created_at",
            "user",
            "action",
            "object_type",
            "object_id",
            "previous_value",
            "new_value",
            "reason",
            "ip_address",
            "user_agent",
        ]
    )
    for entry in rows:
        who = users.get(entry.user_id, "(deleted user)") if entry.user_id else "(system)"
        writer.writerow(
            [
                entry.created_at.isoformat(),
                who,
                entry.action,
                entry.object_type,
                entry.object_id,
                entry.previous_value or "",
                entry.new_value or "",
                entry.reason or "",
                entry.ip_address or "",
                entry.user_agent or "",
            ]
        )
    return buffer.getvalue().encode("utf-8-sig"), len(rows)


def archive_filename(before: datetime) -> str:
    return f"fgnmhs-audit-log-archive-before-{before:%Y%m%d}.csv"


def delete_before(
    session, *, before: datetime, expected_count: int, actor_user_id, reason: str
) -> int:
    """Deletes every audit_logs entry older than `before`, after recording
    the deletion itself. Not committed here — the caller commits, the same
    convention every other write in this app follows.

    Raises ValueError (never a bare assertion — this is a user-facing
    refusal, not a bug) if `before` is inside the protected recent window,
    or if the live count no longer matches `expected_count`: the caller's
    export is assumed to describe exactly the set passed here, and a
    mismatch means that export is stale.
    """
    if before > max_cutoff():
        raise ValueError(f"Can't archive entries newer than {MIN_AGE_DAYS} days old.")
    live_count = count_before(session, before)
    if live_count != expected_count:
        raise ValueError(
            f"{live_count} entries now match this cutoff, not the {expected_count} "
            "that were exported — download again before deleting."
        )
    # Written first so it lands with a created_at newer than `before` and
    # survives the delete below in the same transaction.
    audit_service.record(
        session,
        action=audit_service.AUDIT_LOG_ARCHIVED,
        object_type="audit_logs",
        object_id=session.query(School).one().id,
        user_id=actor_user_id,
        new={"deleted_count": live_count, "before": before.isoformat()},
        reason=reason,
    )
    session.query(AuditLog).filter(AuditLog.created_at < before).delete(synchronize_session=False)
    return live_count
