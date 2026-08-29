"""Administrative backup page (§55). Super Admin only — the download is
every learner's full record in one file (§54)."""

import os
import tempfile

import streamlit as st

from app import audit_service
from app.admin_pages._helpers import get_session, render_flashes, try_commit
from app.auth import require_role
from app.backup_service import backup_filename, generate_backup
from app.models.organization import School

PATH_KEY = "backup_path"
COUNTS_KEY = "backup_counts"
SIZE_KEY = "backup_size"


def _store(data: bytes, counts: dict) -> None:
    """Spills the archive to a temp file and keeps only its path in
    session state.

    The previous version kept the zip itself in `st.session_state`, which
    pins it for the life of the session — one admin sitting on the page
    held the whole database in memory until they signed out. Harmless at
    13MB; at years of 1,200-learner data it is the largest single object
    the app would ever allocate.
    """
    previous = st.session_state.get(PATH_KEY)
    if previous and os.path.exists(previous):
        try:
            os.unlink(previous)  # never accumulate copies of the whole database
        except OSError:
            pass

    handle = tempfile.NamedTemporaryFile(prefix="fgnmhs-backup-", suffix=".zip", delete=False)
    try:
        handle.write(data)
    finally:
        handle.close()

    st.session_state[PATH_KEY] = handle.name
    st.session_state[COUNTS_KEY] = counts
    st.session_state[SIZE_KEY] = len(data)


def render() -> None:
    current_user = require_role("SUPER_ADMIN")
    st.title("Backup")
    st.caption(
        "A downloadable copy of the whole database — one spreadsheet per table "
        "in a zip file, plus a note explaining how to restore it."
    )
    render_flashes()

    st.warning(
        "This file contains every learner's LRN, birthdate, grades and attendance. "
        "Store it securely and don't leave copies on shared drives.",
        icon="🔒",
    )

    st.markdown(
        """
**This is the operator-held copy, not the only backup.** Supabase takes its
own automated backups of the whole project — including the login accounts,
which live in Supabase Auth and are *not* in this file. Restore from those
after a real failure; keep this one so the school still holds its records
if access to the Supabase project is ever lost, and so the data can be read
without restoring anything.
        """
    )

    st.divider()

    # Built on demand rather than on every rerun: it reads every table, so
    # it shouldn't happen just because someone clicked into the page.
    if st.button("Create backup", type="primary"):
        with st.spinner("Reading every table…"):
            with get_session() as session:
                data, counts = generate_backup(session, taken_by=current_user.full_name)
        _store(data, counts)

    path = st.session_state.get(PATH_KEY)
    if not path or not os.path.exists(path):
        return

    counts = st.session_state.get(COUNTS_KEY, {})
    size = st.session_state.get(SIZE_KEY, 0)
    col1, col2, col3 = st.columns(3)
    col1.metric("Tables", len(counts))
    col2.metric("Rows", f"{sum(counts.values()):,}")
    col3.metric("Size", f"{size / 1024:,.0f} KB")

    filename = backup_filename()
    # Streamlit needs the bytes at render time either way, but reading
    # them from disk keeps the copy transient. Holding them in
    # st.session_state pinned it for the whole session — fine at 13MB,
    # a real problem once years of 1,200-learner data are in it.
    with open(path, "rb") as handle:
        clicked = st.download_button(
            "Download backup (.zip)",
            data=handle,
            file_name=filename,
            mime="application/zip",
            type="primary",
        )
    if clicked:
        # §55 asks for an audit trail on backups, and §54 makes a copy of
        # every learner record leaving the system exactly the kind of event
        # worth being able to account for later.
        with get_session() as session:
            audit_service.record(
                session,
                action=audit_service.BACKUP_DOWNLOADED,
                object_type="database",
                object_id=session.query(School).one().id,
                user_id=current_user.id,
                new={
                    "file": filename,
                    "tables": len(counts),
                    "rows": sum(counts.values()),
                    "bytes": size,
                },
            )
            try_commit(session, "Backup download recorded in the audit log.")

    with st.expander("What's in it"):
        st.dataframe(
            [{"Table": name, "Rows": count} for name, count in counts.items()],
            hide_index=True,
            width="stretch",
        )
