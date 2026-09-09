"""Audit log viewer (§50).

Read-only apart from one deliberate, heavily guarded exception: the
"Archive old entries" section at the bottom, backed by
`app/audit_archive_service.py`. There is still no edit control anywhere,
and the archive tool can only ever delete entries older than
`audit_archive_service.MIN_AGE_DAYS`, only after the admin has downloaded
the exact rows being deleted, and only after typing a confirmation phrase
naming the row count. The table above is paginated `PAGE_SIZE` rows at a
time, so every entry is reachable on screen — archiving is not what makes
old rows visible any more (it originally was; see the archive module's
docstring). It still exists so a very large log doesn't turn one query on
this page into hundreds of pages to click through.
"""

import os
import tempfile
import uuid
from datetime import datetime, time, timezone

import pandas as pd
import streamlit as st

from app import audit_archive_service, audit_service
from app.admin_pages._helpers import flash, get_session, render_flashes, try_commit
from app.auth import require_role
from app.display_time import LOG_FORMAT, format_time
from app.models.admin import AuditLog
from app.models.learners import Learner
from app.models.rbac import User

PAGE_SIZE = 100
ANY = "— any —"

# Session-state keys for the viewer's pagination. The page number is kept
# separately from the filter selectboxes (which Streamlit already persists
# on its own) so that changing a filter can deliberately reset it — landing
# on page 1 of a narrowed result set rather than a now out-of-range page.
_PAGE_KEY = "audit_log_page"
_PAGE_FILTERS_KEY = "audit_log_page_filters"

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
        audit_service.SUBJECT_UNITS_CHANGED,
        audit_service.CALENDAR_DAY_CHANGED,
    ],
    # Its own group: "who can get into this system, and who gave them the
    # password" is a question asked on its own, not while looking through
    # calendar edits.
    "Accounts": [
        audit_service.USER_CREATED,
        audit_service.USER_ROLES_CHANGED,
        audit_service.USER_PASSWORD_RESET,
        audit_service.USER_RENAMED,
    ],
    # Who may encode grades for a section is an access grant (see
    # teacher_assignment_service.py's module docstring) — its own group
    # for the same reason Accounts is: asked on its own, not while
    # looking through subject-catalog edits.
    "Teaching Assignments": [
        audit_service.TEACHER_ASSIGNED,
        audit_service.TEACHER_UNASSIGNED,
    ],
    "Awards": [
        audit_service.AWARD_OVERRIDDEN,
        audit_service.AWARD_OVERRIDE_CLEARED,
        audit_service.AWARD_POLICY_CREATED,
        audit_service.AWARD_POLICY_CHANGED,
        audit_service.AWARD_POLICY_DELETED,
        audit_service.AWARD_POLICY_VERSION_CREATED,
        audit_service.AWARD_POLICY_VERSION_CHANGED,
        audit_service.AWARD_POLICY_VERSION_DELETED,
        audit_service.AWARD_POLICY_VERSION_STATUS_CHANGED,
    ],
    "Data": [
        audit_service.DATA_IMPORTED,
        audit_service.BACKUP_DOWNLOADED,
        audit_service.AUDIT_LOG_ARCHIVED,
    ],
}
ALL_ACTIONS = [action for actions in ACTION_GROUPS.values() for action in actions]


def _resolve_user(raw, users: dict) -> str:
    """A user id stored inside a previous/new payload (e.g.
    `teacher_user_id`) reads as a bare UUID in the table; show the name
    the rest of the page already shows for the `Who` column instead."""
    try:
        user = users.get(uuid.UUID(str(raw)))
    except (ValueError, TypeError):
        return str(raw)
    return user.full_name if user else "(deleted user)"


def _format_value(value, users: dict | None = None) -> str:
    """A JSONB blob is unreadable in a table cell; flatten it to
    `field: value` pairs. Any field ending in `_user_id`/`_user_ids` — the
    shape `teacher_assignment_service.py` writes — is resolved to the
    user's name rather than left as a bare UUID."""
    if not value:
        return ""
    if not isinstance(value, dict):
        return str(value)
    users = users or {}

    def render(k, v):
        if k.endswith("_user_ids") and isinstance(v, (list, tuple)):
            v = [_resolve_user(item, users) for item in v]
        elif k.endswith("_user_id") and v is not None:
            v = _resolve_user(v, users)
        return f"{k}: {v}"

    return ", ".join(render(k, v) for k, v in value.items())


def _learner_name(last_name, first_name, middle_name=None, extension_name=None) -> str:
    middle = f" {middle_name}" if middle_name else ""
    extension = f" {extension_name}" if extension_name else ""
    return f"{last_name}, {first_name}{middle}{extension}".strip()


def _learner_name_from_payload(payload: dict | None) -> str | None:
    """A LEARNER_DELETED entry's `previous_value` is the only surviving
    record of the identity fields once the row itself is gone; use it when
    a live lookup misses. Diffed payloads (LEARNER_CHANGED) may carry only
    the fields that actually changed, so this returns None rather than a
    partial name when last_name/first_name aren't both present."""
    if not payload or "last_name" not in payload or "first_name" not in payload:
        return None
    return _learner_name(
        payload["last_name"],
        payload["first_name"],
        payload.get("middle_name"),
        payload.get("extension_name"),
    )


def _resolve_object(entry: AuditLog, learners: dict) -> str:
    """`object_type` alone ("learners") tells you nothing about which
    learner — LEARNER_CREATED/CHANGED/DELETED/ADMISSION_CHANGED all store
    the learner's id as `object_id`, so resolve it to a name the same way
    `_resolve_user` does for a `_user_id` field."""
    if entry.object_type != "learners":
        return entry.object_type
    learner = learners.get(entry.object_id)
    if learner:
        name = _learner_name(
            learner.last_name, learner.first_name, learner.middle_name, learner.extension_name
        )
        return f"{entry.object_type} — {name}"
    name = _learner_name_from_payload(entry.previous_value) or _learner_name_from_payload(entry.new_value)
    return f"{entry.object_type} — {name} (deleted)" if name else f"{entry.object_type} (deleted learner)"


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
        "permanently deleted, oldest first. Deleting always requires downloading the "
        "exact rows first."
    )
    latest_allowed = audit_archive_service.max_cutoff().date()
    st.warning(
        f"You're allowed to archive data no newer than {latest_allowed:%Y-%m-%d} — "
        f"nothing within the last {audit_archive_service.MIN_AGE_DAYS} days can ever "
        "be selected below.",
        icon="⚠️",
    )
    # grand_total alone doesn't say anything is actually archivable: every
    # entry could be inside the 90-day floor above (true for a young log,
    # where the archivable count is 0 for weeks after crossing
    # SUGGEST_THRESHOLD). Telling the admin to "archive the oldest ones"
    # when there's nothing eligible yet is advice they can't act on.
    archivable = audit_archive_service.count_before(session, audit_archive_service.max_cutoff())
    if grand_total >= audit_archive_service.SUGGEST_THRESHOLD:
        if archivable > 0:
            st.warning(
                f"There are {grand_total:,} entries in the log, {archivable:,} of them "
                "archivable — consider archiving the oldest ones below so the viewer "
                "above stays usable.",
                icon="⚠️",
            )
        else:
            st.info(
                f"There are {grand_total:,} entries in the log, but none are archivable "
                f"yet — the oldest won't cross the {audit_archive_service.MIN_AGE_DAYS}-day "
                "mark until later.",
                icon="ℹ️",
            )

    cutoff_date = st.date_input(
        "Delete entries older than",
        value=latest_allowed,
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
        learners = {l.id: l for l in session.query(Learner).all()}

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
        total_pages = max(1, -(-total // PAGE_SIZE))  # ceil division

        # A filter change makes the previously selected page meaningless
        # (and possibly out of range), so reset to page 1 whenever the
        # filter combination differs from the one the stored page was
        # chosen under.
        filter_signature = (group_choice, action_choice, user_choice)
        if st.session_state.get(_PAGE_FILTERS_KEY) != filter_signature:
            st.session_state[_PAGE_FILTERS_KEY] = filter_signature
            st.session_state[_PAGE_KEY] = 1
        page = min(max(st.session_state.get(_PAGE_KEY, 1), 1), total_pages)
        st.session_state[_PAGE_KEY] = page

        entries = (
            query.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
            .all()
        )

        if not entries:
            st.info("No entries match.")
        else:
            first = (page - 1) * PAGE_SIZE + 1
            st.caption(
                f"Showing {first}–{first + len(entries) - 1} of {total} matching "
                f"entry(s) — page {page} of {total_pages}."
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
                            "Object": _resolve_object(entry, learners),
                            "Was": _format_value(entry.previous_value, users),
                            "Became": _format_value(entry.new_value, users),
                            "Reason": entry.reason or "",
                        }
                        for entry in entries
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

            if total_pages > 1:
                col_prev, col_label, col_next, col_jump = st.columns([1, 2, 1, 1.4])
                with col_prev:
                    if st.button("← Previous", disabled=page <= 1, width="stretch"):
                        st.session_state[_PAGE_KEY] = page - 1
                        st.rerun()
                with col_label:
                    st.markdown(
                        f"<div style='text-align:center; padding-top: 0.4rem'>"
                        f"Page {page} of {total_pages}</div>",
                        unsafe_allow_html=True,
                    )
                with col_next:
                    if st.button("Next →", disabled=page >= total_pages, width="stretch"):
                        st.session_state[_PAGE_KEY] = page + 1
                        st.rerun()
                with col_jump:
                    with st.form("audit_log_jump", border=False):
                        jump_col, go_col = st.columns([2, 1])
                        target = jump_col.number_input(
                            "Go to page",
                            min_value=1,
                            max_value=total_pages,
                            value=page,
                            step=1,
                            label_visibility="collapsed",
                        )
                        if go_col.form_submit_button("Go") and target != page:
                            st.session_state[_PAGE_KEY] = int(target)
                            st.rerun()

        grand_total = session.query(AuditLog).count()
        _render_archive_section(session, current_user, grand_total)
