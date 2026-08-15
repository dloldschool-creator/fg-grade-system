import streamlit as st

from app.admin_pages._helpers import (
    clear_text_fields,
    get_session,
    render_flashes,
    section_filters,
    text_field,
    try_commit,
    try_delete,
)
from app.auth import require_role
from app.models.academic_structure import GradeLevel, Section, Strand, Track
from app.models.organization import SchoolYear
from app.models.rbac import Role, User, UserRole


def render() -> None:
    require_role("SUPER_ADMIN")
    st.title("Sections")
    render_flashes()

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("Create a school year first (School Years & Terms page).")
            return
        sy_by_id = {sy.id: sy for sy in school_years}

        grade_levels = session.query(GradeLevel).order_by(GradeLevel.display_order).all()
        gl_by_id = {gl.id: gl for gl in grade_levels}
        tracks = session.query(Track).order_by(Track.display_order).all()
        track_by_id = {t.id: t for t in tracks}
        strands = session.query(Strand).order_by(Strand.track_id, Strand.display_order).all()
        strand_by_id = {s.id: s for s in strands}

        advisers = (
            session.query(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .filter(Role.code == "ADVISER", User.is_active.is_(True))
            .order_by(User.full_name)
            .all()
        )
        adviser_by_id = {a.id: a for a in advisers}

        sy_filter = st.selectbox(
            "School year", options=[sy.id for sy in school_years], format_func=lambda v: sy_by_id[v].name
        )

        sections = (
            session.query(Section)
            .filter_by(school_year_id=sy_filter)
            .order_by(Section.grade_level_id, Section.display_order)
            .all()
        )

        # The same filters every other section page has. This one lists all
        # 30 sections as expanders rather than picking one, so the filters
        # narrow the list instead of a dropdown.
        visible_sections, _ = section_filters(
            sections, gl_by_id, strand_by_id, key="sections_list"
        )
        if sections and not visible_sections:
            st.info("No sections match those filters.")

        for section in visible_sections:
            adviser_label = (
                adviser_by_id[section.adviser_user_id].full_name
                if section.adviser_user_id in adviser_by_id
                else "— none —"
            )
            with st.expander(
                f"{gl_by_id[section.grade_level_id].code} — {section.name} "
                f"({strand_by_id[section.strand_id].code}) — Adviser: {adviser_label}"
            ):
                # Track lives outside the form: st.form only reruns the
                # script on submit, so a strand dropdown filtered by track
                # would show stale options if both lived inside the form.
                track_key = f"sec_track_{section.id}"
                if track_key not in st.session_state:
                    st.session_state[track_key] = section.track_id
                track_choice = st.selectbox(
                    "Track",
                    options=[t.id for t in tracks],
                    format_func=lambda v: track_by_id[v].name,
                    key=track_key,
                )
                strand_options = [s.id for s in strands if s.track_id == track_choice]

                with st.form(f"edit_section_{section.id}"):
                    name = st.text_input("Name", value=section.name, key=f"sec_name_{section.id}")
                    gl_choice = st.selectbox(
                        "Grade level",
                        options=[gl.id for gl in grade_levels],
                        index=[gl.id for gl in grade_levels].index(section.grade_level_id),
                        format_func=lambda v: gl_by_id[v].name,
                        key=f"sec_gl_{section.id}",
                    )
                    strand_choice = st.selectbox(
                        "Strand",
                        options=strand_options,
                        index=strand_options.index(section.strand_id)
                        if section.strand_id in strand_options
                        else 0,
                        format_func=lambda v: strand_by_id[v].name,
                        key=f"sec_strand_{section.id}",
                    )
                    adviser_options = [None] + [a.id for a in advisers]
                    adviser_choice = st.selectbox(
                        "Adviser",
                        options=adviser_options,
                        index=adviser_options.index(section.adviser_user_id)
                        if section.adviser_user_id in adviser_options
                        else 0,
                        format_func=lambda v: "— none —" if v is None else adviser_by_id[v].full_name,
                        key=f"sec_adviser_{section.id}",
                    )
                    room = st.text_input("Room", value=section.room or "", key=f"sec_room_{section.id}")
                    capacity = st.number_input(
                        "Capacity",
                        min_value=0,
                        value=section.capacity or 0,
                        step=1,
                        key=f"sec_capacity_{section.id}",
                    )
                    is_active = st.checkbox(
                        "Active", value=section.is_active, key=f"sec_active_{section.id}"
                    )

                    col1, col2 = st.columns(2)
                    if col1.form_submit_button("Save"):
                        section.name = name
                        section.grade_level_id = gl_choice
                        section.track_id = track_choice
                        section.strand_id = strand_choice
                        section.adviser_user_id = adviser_choice
                        section.room = room or None
                        section.capacity = capacity or None
                        section.is_active = is_active
                        section.version += 1
                        try_commit(session, "Saved.")
                        st.rerun()
                    if col2.form_submit_button("Delete"):
                        try_delete(session, section, section.name)
                        st.rerun()

        st.divider()
        st.subheader("Add section")
        if not advisers:
            st.info(
                "No users with the ADVISER role yet — you can still create a section and "
                "assign an adviser later (Users & Roles page)."
            )

        new_track_key = "new_sec_track"
        if new_track_key not in st.session_state:
            st.session_state[new_track_key] = tracks[0].id
        new_track_choice = st.selectbox(
            "Track", options=[t.id for t in tracks], format_func=lambda v: track_by_id[v].name, key=new_track_key
        )
        new_strand_options = [s.id for s in strands if s.track_id == new_track_choice]

        with st.form("add_section"):
            name = text_field("Name (e.g. STEM - A)", key="add_section.name")
            gl_choice = st.selectbox(
                "Grade level", options=[gl.id for gl in grade_levels], format_func=lambda v: gl_by_id[v].name
            )
            strand_choice = st.selectbox(
                "Strand", options=new_strand_options, format_func=lambda v: strand_by_id[v].name
            )
            adviser_options = [None] + [a.id for a in advisers]
            adviser_choice = st.selectbox(
                "Adviser",
                options=adviser_options,
                format_func=lambda v: "— none —" if v is None else adviser_by_id[v].full_name,
            )
            room = text_field("Room", key="add_section.room")
            capacity = st.number_input("Capacity", min_value=0, value=0, step=1, key="new_sec_capacity")

            if st.form_submit_button("Add"):
                if not name:
                    st.error("Name is required.")
                else:
                    session.add(
                        Section(
                            school_year_id=sy_filter,
                            grade_level_id=gl_choice,
                            track_id=new_track_choice,
                            strand_id=strand_choice,
                            name=name,
                            adviser_user_id=adviser_choice,
                            room=room or None,
                            capacity=capacity or None,
                        )
                    )
                    if try_commit(session, f"Added {name}."):
                        clear_text_fields("add_section")
                    st.rerun()
