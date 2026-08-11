"""Administrative backup page (§55). Super Admin only — the download is
every learner's full record in one file (§54)."""

import streamlit as st

from app import audit_service
from app.admin_pages._helpers import get_session, render_flashes, try_commit
from app.auth import require_role
from app.backup_service import backup_filename, generate_backup
from app.models.organization import School

BACKUP_KEY = "backup_bytes"
COUNTS_KEY = "backup_counts"


def render() -> None:
    current_user = require_role("SUPER_ADMIN")
    st.title("Backup")
    st.caption(
        "Downloadable snapshot of the whole database (§55) — one CSV per table "
        "in a zip, plus a manifest with restore instructions."
    )
    render_flashes()

    st.warning(
        "This file contains every learner's LRN, birthdate, grades and attendance. "
        "Store it encrypted and don't leave copies on shared drives (§54).",
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
        st.session_state[BACKUP_KEY] = data
        st.session_state[COUNTS_KEY] = counts

    data = st.session_state.get(BACKUP_KEY)
    if data is None:
        return

    counts = st.session_state.get(COUNTS_KEY, {})
    col1, col2, col3 = st.columns(3)
    col1.metric("Tables", len(counts))
    col2.metric("Rows", f"{sum(counts.values()):,}")
    col3.metric("Size", f"{len(data) / 1024:,.0f} KB")

    filename = backup_filename()
    if st.download_button(
        "Download backup (.zip)",
        data=data,
        file_name=filename,
        mime="application/zip",
        type="primary",
    ):
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
                    "bytes": len(data),
                },
            )
            try_commit(session, "Backup download recorded in the audit log.")

    with st.expander("What's in it"):
        st.dataframe(
            [{"Table": name, "Rows": count} for name, count in counts.items()],
            hide_index=True,
            use_container_width=True,
        )
