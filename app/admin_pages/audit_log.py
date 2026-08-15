"""Audit log viewer (§50).

Read-only on purpose. There is no delete control, no edit control and no
"clear old entries" button anywhere on this page — §50 requires the
history to survive the people it records, and the surest way to honour
that is for the application to have no code that removes a row.
"""

import pandas as pd
import streamlit as st

from app import audit_service
from app.admin_pages._helpers import get_session, render_flashes
from app.auth import require_role
from app.display_time import LOG_FORMAT, format_time
from app.models.admin import AuditLog
from app.models.rbac import User

PAGE_SIZE = 100
ANY = "— any —"

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
    "Learners": [audit_service.LEARNER_MOVEMENT_RECORDED],
    "Configuration": [
        audit_service.SUBJECT_OFFERING_CHANGED,
        audit_service.CALENDAR_DAY_CHANGED,
        audit_service.USER_ROLES_CHANGED,
    ],
    "Awards": [audit_service.AWARD_OVERRIDDEN, audit_service.AWARD_OVERRIDE_CLEARED],
    "Data": [audit_service.DATA_IMPORTED, audit_service.BACKUP_DOWNLOADED],
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


def render() -> None:
    require_role("SUPER_ADMIN")
    st.title("Audit Log")
    st.caption(
        "A record of every important change: who, what changed, the old and new "
        "value, when, and the reason where one was required. Entries cannot be "
        "edited or deleted from anywhere in this app."
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
            st.info(
                "No entries match. If the log is empty entirely, nothing sensitive has "
                "been changed since audit logging was switched on."
            )
            return

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
            use_container_width=True,
        )
