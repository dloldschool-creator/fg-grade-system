import streamlit as st

from app.admin_pages._helpers import (
    clear_text_fields,
    get_session,
    render_flashes,
    stateful_tabs,
    text_field,
    try_commit,
    try_delete,
)
from app.auth import require_role
from app.models.academic_structure import GradeLevel, Strand, Track


def _grade_levels_tab(session):
    st.caption("Grade levels are fixed and not edited here.")
    rows = session.query(GradeLevel).order_by(GradeLevel.display_order).all()
    st.table([{"Code": gl.code, "Name": gl.name} for gl in rows])


def _tracks_tab(session):
    tracks = session.query(Track).order_by(Track.display_order).all()
    for track in tracks:
        col1, col2, col3, col4 = st.columns([3, 3, 2, 2])
        col1.write(track.code)
        col2.write(track.name)
        active = col3.checkbox("Active", value=track.is_active, key=f"track_active_{track.id}")
        if active != track.is_active:
            track.is_active = active
            session.commit()
            st.rerun()
        if col4.button("Delete", key=f"track_delete_{track.id}"):
            try_delete(session, track, track.name)
            st.rerun()

    st.divider()
    with st.form("add_track"):
        st.subheader("Add track")
        code = text_field("Code (e.g. ACADEMIC)", key="add_track.code")
        name = text_field("Name", key="add_track.name")
        display_order = st.number_input("Display order", min_value=0, value=0, step=1)
        if st.form_submit_button("Add"):
            if not code or not name:
                st.error("Code and name are required.")
            else:
                session.add(Track(code=code.upper(), name=name, display_order=display_order))
                if try_commit(session, f"Added {name}."):
                    clear_text_fields("add_track")
                st.rerun()


def _strands_tab(session):
    tracks = session.query(Track).order_by(Track.display_order).all()
    track_by_id = {t.id: t for t in tracks}
    strands = session.query(Strand).order_by(Strand.track_id, Strand.display_order).all()

    for strand in strands:
        col1, col2, col3, col4, col5 = st.columns([2, 2, 3, 2, 2])
        col1.write(track_by_id[strand.track_id].code)
        col2.write(strand.code)
        col3.write(strand.name)
        active = col4.checkbox("Active", value=strand.is_active, key=f"strand_active_{strand.id}")
        if active != strand.is_active:
            strand.is_active = active
            session.commit()
            st.rerun()
        if col5.button("Delete", key=f"strand_delete_{strand.id}"):
            try_delete(session, strand, strand.name)
            st.rerun()

    st.divider()
    with st.form("add_strand"):
        st.subheader("Add strand")
        track_choice = st.selectbox(
            "Track", options=[t.id for t in tracks], format_func=lambda tid: track_by_id[tid].name
        )
        code = text_field("Code (e.g. STEM)", key="add_strand.code")
        name = text_field("Name", key="add_strand.name")
        display_order = st.number_input(
            "Display order", min_value=0, value=0, step=1, key="strand_display_order"
        )
        if st.form_submit_button("Add"):
            if not code or not name:
                st.error("Code and name are required.")
            else:
                session.add(
                    Strand(
                        track_id=track_choice,
                        code=code.upper(),
                        name=name,
                        display_order=display_order,
                    )
                )
                if try_commit(session, f"Added {name}."):
                    clear_text_fields("add_strand")
                st.rerun()


def render() -> None:
    require_role("SUPER_ADMIN")
    st.title("Academic Structure")
    render_flashes()

    with get_session() as session:
        choice = stateful_tabs("academic_structure_tab", ["Grade Levels", "Tracks", "Strands"])
        if choice == "Grade Levels":
            _grade_levels_tab(session)
        elif choice == "Tracks":
            _tracks_tab(session)
        elif choice == "Strands":
            _strands_tab(session)
