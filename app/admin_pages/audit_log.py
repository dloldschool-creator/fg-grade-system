"""Audit log viewer (§50).

Read-only apart from one deliberate, heavily guarded exception: the
"Archive old entries" section at the bottom, backed by
`app/audit_archive_service.py`. There is still no edit control anywhere,
and the archive tool can only ever delete entries older than
`audit_archive_service.MIN_AGE_DAYS`, only after the admin has downloaded
the exact rows being deleted, and only after typing a confirmation phrase
naming the row count. It exists because the table above has no pagination
and only ever shows the newest `PAGE_SIZE` rows — at 543 entries, older
ones were already unreachable on screen even though nothing had deleted
them (see the archive module's docstring for the full reasoning).
"""

import os
import tempfile
from datetime import datetime, time, timedelta, timezone

import pandas as pd
import streamlit as st

from app import audit_archive_service, audit_service
from app.admin_pages._helpers import flash, get_session, render_flashes, try_commit
from app.auth import require_role
from app.display_time import LOG_FORMAT, format_time
from app.models.admin import AuditLog
from app.models.rbac import User

PAGE_SIZE = 100
ANY = "— any —"

# Session-state keys for the prepared export (a temp-file path, never the
# bytes themselves — see _store below, same reasoning as backup.py: holding
# an export in st.session_state pins it in memory for the life of the
# session).
_EXPORT_PATH_KEY = "audit_archive_path"
_EXPORT_META_KEY = "audit_archive_meta"
_DOWNLOADED_KEY = "audit_archive_downloaded"

# Grouped the way somebody investigating actually asks the question
# ("what happened to this learner's grades?"), not alphabetically.
ACTION_GROUPS = {
    "Grades": [
        audit_service.GRADE_CREATED,
        audit_service.GRADE_CHANGED,
        audit_service.GRADE_SUBMITTED,
        audit_service.GRADE_FINALIZED,
        audit_service.GRADE_REOPENED,
    ],
    "Attendance": [
        audit_service.ATTENDANCE_CHANGED,
        audit_service.ATTENDANCE_MONTH_FINALIZED,
        audit_service.ATTENDANCE_MONTH_REOPENED,
    ],
    "Learners": [
        audit_service.LEARNER_CREATED,
        audit_service.LEARNER_CHANGED,
        audit_service.LEARNER_DELETED,
        audit_service.LEARNER_ADMISSION_CHANGED,
        audit_service.LEARNER_MOVEMENT_RECORDED,
    ],
    "Configuration": [
        audit_service.SUBJECT_OFFERING_CHANGED,
        audit_service.CALENDAR_DAY_CHANGED,
    ],
    # Its own group: "who can get into this system, and who gave them the
    # password" is a question asked on its own, not while looking through
    # calendar edits.
    "Accounts": [
        audit_service.USER_CREATED,
        audit_service.USER_ROLES_CHANGED,
        audit_service.USER_PASSWORD_RESET,
    ],
    "Awards": [audit_service.AWARD_OVERRIDDEN, audit_service.AWARD_OVERRIDE_CLEARED],
    "Data": [
        audit_service.DATA_IMPORTED,
        audit_service.BACKUP_DOWNLOADED,
        audit_service.AUDIT_LOG_ARCHIVED,
    ],
}
ALL_ACTIONS = [action for actions in ACTION_GROUPS.values() for action in actions]


def _format_value(value) -> str:
    """A JSONB blob is unreadable in a table cell; flatten it to
    `field: value` pairs."""
    if not value:
        return ""
    if not isinstance(value, dict):
        return str(value)
    return ", ".join(f"{k}: {v}" for k, v in value.items())


def _store_export(data: bytes, before: datetime, count: int) -> None:
    """Spills the export to a temp file and keeps only its path in session
    state — same reasoning as backup.py's _store: an export held as bytes in
    st.session_state pins it in memory for the life of the session."""
    previous = st.session_state.get(_EXPORT_PATH_KEY)
    if previous and os.path.exists(previous):
        try:
            os.unlink(previous)
        except OSError:
            pass

    handle = tempfile.NamedTemporaryFile(prefix="fgnmhs-audit-archive-", suffix=".csv", delete=False)
    try:
        handle.write(data)
    finally:
        handle.close()

    st.session_state[_EXPORT_PATH_KEY] = handle.name
    st.session_state[_EXPORT_META_KEY] = {"before": before.isoformat(), "count": count}
    st.session_state[_DOWNLOADED_KEY] = False


def _clear_export() -> None:
    path = st.session_state.pop(_EXPORT_PATH_KEY, None)
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            pass
    st.session_state.pop(_EXPORT_META_KEY, None)
    st.session_state.pop(_DOWNLOADED_KEY, None)


def _render_archive_section(session, current_user, grand_total: int) -> None:
    st.divider()
    st.subheader("Archive old entries")
    st.caption(
        "For a very large log, not for routine cleanup: entries can be exported and "
        "permanently deleted, oldest first. Nothing younger than "
        f"{audit_archive_service.MIN_AGE_DAYS} days can ever be selected, and deleting "
        "always requires downloading the exact rows first."
    )
    if grand_total >= audit_archive_service.SUGGEST_THRESHOLD:
        st.warning(
            f"There are {grand_total:,} entries in the log — consider archiving the "
            "oldest ones below so the viewer above stays usable.",
            icon="⚠️",
        )

    latest_allowed = audit_archive_service.max_cutoff().date()
    default_cutoff = min(latest_allowed, datetime.now(timezone.utc).date() - timedelta(days=365))
    cutoff_date = st.date_input(
        "Delete entries older than",
        value=default_cutoff,
        max_value=latest_allowed,
    )
    before = datetime.combine(cutoff_date, time.min, tzinfo=timezone.utc)
    eligible = audit_archive_service.count_before(session, before)
    st.caption(f"{eligible:,} entry(s) are older than {cutoff_date:%Y-%m-%d}.")

    if st.button("Prepare export", disabled=eligible == 0):
        with st.spinner("Exporting…"):
            data, count = audit_archive_service.export_csv(session, before)
        _store_export(data, before, count)
        st.rerun()

    path = st.session_state.get(_EXPORT_PATH_KEY)
    meta = st.session_state.get(_EXPORT_META_KEY)
    if not path or not meta or not os.path.exists(path):
        return
    if meta["before"] != before.isoformat():
        st.info("The cutoff date changed since this export was prepared — prepare it again.")
        return

    count = meta["count"]
    filename = audit_archive_service.archive_filename(before)
    with open(path, "rb") as handle:
        clicked = st.download_button(
            f"Download {count:,} entry(s) (.csv)",
            data=handle,
            file_name=filename,
            mime="text/csv",
            type="primary",
        )
    if clicked:
        st.session_state[_DOWNLOADED_KEY] = True

    if not st.session_state.get(_DOWNLOADED_KEY):
        st.caption("Download the file above to unlock deletion.")
        return

    st.warning(
        f"This will permanently delete {count:,} entries from the audit log. "
        "This cannot be undone from within the app — the file you downloaded "
        "becomes the only remaining record of them.",
        icon="🗑️",
    )
    with st.form("audit_archive_delete"):
        confirmed_saved = st.checkbox("I have downloaded and safely stored this file.")
        phrase = f"DELETE {count} LOGS"
        typed = st.text_input(f"Type “{phrase}” to confirm")
        reason = st.text_area("Reason (required)")
        submitted = st.form_submit_button("Permanently delete these entries", type="primary")
        if submitted:
            if not confirmed_saved:
                flash("error", "Confirm you've downloaded and saved the file first.")
            elif typed != phrase:
                flash("error", "Confirmation text didn't match — nothing was deleted.")
            elif not reason.strip():
                flash("error", "A reason is required (§50).")
            else:
                try:
                    audit_archive_service.delete_before(
                        session,
                        before=before,
                        expected_count=count,
                        actor_user_id=current_user.id,
                        reason=reason,
                    )
                except ValueError as exc:
                    flash("error", str(exc))
                else:
                    if try_commit(session, f"Deleted {count:,} archived entries."):
                        _clear_export()
                st.rerun()


def render() -> None:
    current_user = require_role("SUPER_ADMIN")
    st.title("Audit Log")
    st.caption(
        "A record of every important change: who, what changed, the old and new "
        "value, when, and the reason where one was required. Entries can never be "
        "edited, and can only be deleted via the archive tool at the bottom of this "
        "page, which forces a download first."
    )
    render_flashes()

    with get_session() as session:
        users = {u.id: u for u in session.query(User).all()}

        col1, col2, col3 = st.columns(3)
        with col1:
            group_choice = st.selectbox("Category", options=[ANY] + list(ACTION_GROUPS))
        with col2:
            action_options = [ANY] + (
                ALL_ACTIONS if group_choice == ANY else ACTION_GROUPS[group_choice]
            )
            action_choice = st.selectbox("Action", options=action_options)
        with col3:
            user_options = [ANY] + sorted(users, key=lambda uid: users[uid].full_name or "")
            user_choice = st.selectbox(
                "Done by",
                options=user_options,
                format_func=lambda v: ANY if v == ANY else users[v].full_name,
            )

        query = session.query(AuditLog)
        if action_choice != ANY:
            query = query.filter(AuditLog.action == action_choice)
        elif group_choice != ANY:
            query = query.filter(AuditLog.action.in_(ACTION_GROUPS[group_choice]))
        if user_choice != ANY:
            query = query.filter(AuditLog.user_id == user_choice)

        total = query.count()
        entries = query.order_by(AuditLog.created_at.desc()).limit(PAGE_SIZE).all()

        if not entries:
            st.info("No entries match.")
        else:
            st.caption(
                f"Showing the most recent {len(entries)} of {total} matching entry(s)."
                if total > len(entries)
                else f"{total} matching entry(s)."
            )
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "When": format_time(entry.created_at, LOG_FORMAT),
                            "Who": (
                                users[entry.user_id].full_name
                                if entry.user_id in users
                                else "(deleted user)"
                                if entry.user_id
                                else "(system)"
                            ),
                            "Action": entry.action,
                            "Object": entry.object_type,
                            "Was": _format_value(entry.previous_value),
                            "Became": _format_value(entry.new_value),
                            "Reason": entry.reason or "",
                            "IP": entry.ip_address or "",
                        }
                        for entry in entries
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

        grand_total = session.query(AuditLog).count()
        _render_archive_section(session, current_user, grand_total)
