"""Shared helpers for admin pages."""

import csv
import io
from contextlib import contextmanager

import streamlit as st
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal

_FLASH_KEY = "_flash_messages"


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def flash(kind: str, message: str) -> None:
    """Queues a message to survive an immediately-following st.rerun() —
    st.success/st.error called right before st.rerun() never reach the
    browser, since the rerun restarts script execution before the message
    is rendered. Call render_flashes() near the top of a page to show and
    clear whatever's queued."""
    st.session_state.setdefault(_FLASH_KEY, []).append((kind, message))


def render_flashes() -> None:
    for kind, message in st.session_state.pop(_FLASH_KEY, []):
        getattr(st, kind)(message)


def stateful_tabs(key: str, labels: list[str]) -> str:
    """st.tabs() always resets to the first tab after st.rerun() (a known
    Streamlit limitation, not fixed upstream) — every save/delete action
    on these pages reruns to refresh the list, which was kicking the admin
    back to the first tab after every action. This persists the active
    tab in session_state instead, styled to read like tabs."""
    if key not in st.session_state:
        st.session_state[key] = labels[0]
    return st.radio(
        "Section", labels, key=key, horizontal=True, label_visibility="collapsed"
    )


_CONSTRAINT_MESSAGES = {
    "lrn_format": "LRN must be exactly 12 digits.",
    "uq_learners_lrn": "That LRN is already used by another learner.",
    "uq_enrollments_learner_id": "This learner already has an enrollment for that school year.",
    "uq_subject_profile_subjects_subject_profile_id": "That subject is already on this profile.",
    "uq_section_subject_offerings_section_id": "That subject is already offered in this section/term.",
    "uq_sections_adviser_per_school_year": "That user is already the adviser of another section this school year.",
    "uq_tracks_code": "That track code is already used.",
    "uq_strands_track_id": "That strand code is already used for this track.",
    "uq_schools_deped_school_id": "That DepEd School ID is already used.",
    "uq_school_years_name": "That school year name is already used.",
    "uq_subject_categories_code": "That category code is already used.",
    "uq_subjects_code": "That subject code is already used.",
    "uq_grading_policy_versions_grading_policy_id": "That version number already exists for this policy.",
}


def _friendly_integrity_error(exc: IntegrityError) -> str:
    cause = str(getattr(exc, "orig", exc))
    for constraint_name, message in _CONSTRAINT_MESSAGES.items():
        if constraint_name in cause:
            return message
    # Unrecognized constraint — surface Postgres's own detail line rather
    # than a fully opaque message, so an unmapped constraint is still
    # self-diagnosable instead of a dead end.
    detail_line = next((line for line in cause.splitlines() if line.strip()), cause).strip()
    return f"that would violate a data rule — {detail_line}"


def flush_or_rollback(session) -> bool:
    """Flushes pending inserts so a generated PK is available to a
    dependent row added right after (e.g. a new Learner's id, needed by a
    new Enrollment referencing it) — with the same friendly-error handling
    as try_commit. Needed because this codebase declares no ORM
    relationship() between related tables (explicit queries everywhere
    instead), so SQLAlchemy's unit-of-work can't infer cross-table insert
    order from a bare FK column alone; without an explicit flush here, it
    can attempt the dependent INSERT first and violate the FK. Call this
    between adding the parent row and the row that references it, then
    still call try_commit for the actual commit."""
    try:
        session.flush()
        return True
    except IntegrityError as exc:
        session.rollback()
        flash("error", f"Couldn't save — {_friendly_integrity_error(exc)}")
        return False


def try_commit(session, success_message: str) -> bool:
    """Commits and flashes `success_message`, or on constraint violation
    rolls back and flashes a friendly explanation instead of a stack
    trace. Returns True on success. Always flashes rather than calling
    st.success/st.error directly, since call sites follow this with
    st.rerun()."""
    try:
        session.commit()
        flash("success", success_message)
        return True
    except IntegrityError as exc:
        session.rollback()
        flash("error", f"Couldn't save — {_friendly_integrity_error(exc)}")
        return False


def read_uploaded_csv(uploaded_file) -> list[dict]:
    """Parses a Streamlit-uploaded CSV into a list of {column: value}
    dicts, keyed by the header row, with surrounding whitespace stripped
    from every value. Lightweight bulk-import, not the full formal
    upload/map/validate/confirm/audit pipeline in master-spec.md §51 —
    that stays Phase 13; this is fixed-column CSV with inline validation
    (see each page's own row-validation loop)."""
    text = uploaded_file.getvalue().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def try_delete(session, instance, label: str) -> bool:
    """Deletes `instance` and commits. On FK violation (row still
    referenced elsewhere — every FK in this schema is ON DELETE RESTRICT),
    flashes a friendly message instead of a stack trace. Returns True on
    success. Always flashes rather than calling st.success/st.error
    directly, since every call site follows this with st.rerun()."""
    try:
        session.delete(instance)
        session.commit()
        flash("success", f"Deleted {label}.")
        return True
    except IntegrityError:
        session.rollback()
        flash("error", f"Can't delete {label} — it's still referenced elsewhere.")
        return False
