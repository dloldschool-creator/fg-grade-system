import streamlit as st

from app.admin_pages._helpers import get_session, render_flashes, try_commit, try_delete
from app.auth import require_role
from app.models.academic_structure import GradeLevel, Strand, Track
from app.models.subjects import Subject, SubjectProfile, SubjectProfileSubject


def _profile_subjects_section(session, profile: SubjectProfile, subjects_by_id: dict, entries=None):
    st.subheader("Subjects on this profile")
    # `entries` is preloaded by the caller for the whole page — fetching
    # them here was one round trip per profile, paid even for the panels
    # the user never opened.
    entries = entries or []
    for entry in entries:
        subject = subjects_by_id.get(entry.subject_id)
        subject_label = subject.official_name if subject else "(unknown subject)"
        with st.form(f"edit_pse_{entry.id}"):
            st.markdown(f"**{subject_label}**")
            col1, col2, col3, col4, col5, col6 = st.columns([1, 1, 1, 1, 1, 1])
            t1 = col1.checkbox("Term 1", value=entry.term1_active, key=f"pse_t1_{entry.id}")
            t2 = col2.checkbox("Term 2", value=entry.term2_active, key=f"pse_t2_{entry.id}")
            t3 = col3.checkbox("Term 3", value=entry.term3_active, key=f"pse_t3_{entry.id}")
            elective = col4.checkbox("Elective", value=entry.is_elective, key=f"pse_el_{entry.id}")
            order = col5.number_input(
                "Order", min_value=0, value=entry.display_order, step=1, key=f"pse_ord_{entry.id}"
            )
            col6.write("")
            save = col6.form_submit_button("Save")
            remove = st.form_submit_button("Remove from profile")

            if save:
                entry.term1_active = t1
                entry.term2_active = t2
                entry.term3_active = t3
                entry.is_elective = elective
                entry.display_order = order
                try_commit(session, "Saved.")
                st.rerun()
            if remove:
                try_delete(session, entry, subject_label)
                st.rerun()

    st.divider()
    eligible_subjects = [
        s
        for s in subjects_by_id.values()
        if s.grade_level_id == profile.grade_level_id
        and (s.track_restriction_id is None or s.track_restriction_id == profile.track_id)
    ]
    already_added = {e.subject_id for e in entries}
    addable = [s for s in eligible_subjects if s.id not in already_added]
    if not addable:
        st.caption("No more eligible subjects to add (matching grade level/track and not already on this profile).")
        return

    with st.form(f"add_pse_{profile.id}"):
        st.caption("Add subject to profile")
        subject_choice = st.selectbox(
            "Subject", options=[s.id for s in addable], format_func=lambda v: subjects_by_id[v].official_name
        )
        col1, col2, col3, col4, col5 = st.columns(5)
        t1 = col1.checkbox("Term 1", key=f"new_pse_t1_{profile.id}")
        t2 = col2.checkbox("Term 2", key=f"new_pse_t2_{profile.id}")
        t3 = col3.checkbox("Term 3", key=f"new_pse_t3_{profile.id}")
        elective = col4.checkbox("Elective", key=f"new_pse_el_{profile.id}")
        order = col5.number_input("Order", min_value=0, value=0, step=1, key=f"new_pse_ord_{profile.id}")

        if st.form_submit_button("Add"):
            if not (t1 or t2 or t3):
                st.error("Mark at least one term active.")
            else:
                session.add(
                    SubjectProfileSubject(
                        subject_profile_id=profile.id,
                        subject_id=subject_choice,
                        term1_active=t1,
                        term2_active=t2,
                        term3_active=t3,
                        is_elective=elective,
                        display_order=order,
                    )
                )
                try_commit(session, f"Added {subjects_by_id[subject_choice].official_name}.")
                st.rerun()


def render() -> None:
    require_role("SUPER_ADMIN")
    st.title("Subject Profiles")
    st.caption(
        "The default set of subjects for each grade level and track/strand. "
        "Filling these in first means a new section's subjects can be added with "
        "one click on Section Subject Offerings, instead of one at a time."
    )
    render_flashes()

    with get_session() as session:
        grade_levels = session.query(GradeLevel).order_by(GradeLevel.display_order).all()
        gl_by_id = {gl.id: gl for gl in grade_levels}
        tracks = session.query(Track).order_by(Track.display_order).all()
        track_by_id = {t.id: t for t in tracks}
        strands = session.query(Strand).order_by(Strand.track_id, Strand.display_order).all()
        strand_by_id = {s.id: s for s in strands}
        subjects_by_id = {s.id: s for s in session.query(Subject).order_by(Subject.official_name).all()}

        profiles = session.query(SubjectProfile).order_by(SubjectProfile.name).all()

        entries_by_profile: dict = {}
        for entry in (
            session.query(SubjectProfileSubject)
            .order_by(SubjectProfileSubject.display_order)
            .all()
        ):
            entries_by_profile.setdefault(entry.subject_profile_id, []).append(entry)

        for profile in profiles:
            with st.expander(
                f"{profile.name} — {gl_by_id[profile.grade_level_id].code} / "
                f"{strand_by_id[profile.strand_id].code}"
                + ("" if profile.is_active else "  (inactive)")
            ):
                track_key = f"profile_track_{profile.id}"
                if track_key not in st.session_state:
                    st.session_state[track_key] = profile.track_id
                track_choice = st.selectbox(
                    "Track", options=[t.id for t in tracks], format_func=lambda v: track_by_id[v].name,
                    key=track_key,
                )
                strand_options = [s.id for s in strands if s.track_id == track_choice]

                with st.form(f"edit_profile_{profile.id}"):
                    name = st.text_input("Name", value=profile.name, key=f"prof_name_{profile.id}")
                    gl_choice = st.selectbox(
                        "Grade level",
                        options=[gl.id for gl in grade_levels],
                        index=[gl.id for gl in grade_levels].index(profile.grade_level_id),
                        format_func=lambda v: gl_by_id[v].name,
                        key=f"prof_gl_{profile.id}",
                    )
                    strand_choice = st.selectbox(
                        "Strand",
                        options=strand_options,
                        index=strand_options.index(profile.strand_id) if profile.strand_id in strand_options else 0,
                        format_func=lambda v: strand_by_id[v].name,
                        key=f"prof_strand_{profile.id}",
                    )
                    is_active = st.checkbox("Active", value=profile.is_active, key=f"prof_active_{profile.id}")

                    col1, col2 = st.columns(2)
                    if col1.form_submit_button("Save profile"):
                        profile.name = name
                        profile.grade_level_id = gl_choice
                        profile.track_id = track_choice
                        profile.strand_id = strand_choice
                        profile.is_active = is_active
                        try_commit(session, "Saved.")
                        st.rerun()
                    if col2.form_submit_button("Delete profile"):
                        try_delete(session, profile, profile.name)
                        st.rerun()

                st.divider()
                _profile_subjects_section(
                    session, profile, subjects_by_id, entries_by_profile.get(profile.id, [])
                )

        st.divider()
        st.subheader("Add subject profile")
        new_track_key = "new_profile_track"
        if new_track_key not in st.session_state:
            st.session_state[new_track_key] = tracks[0].id
        new_track_choice = st.selectbox(
            "Track", options=[t.id for t in tracks], format_func=lambda v: track_by_id[v].name, key=new_track_key
        )
        new_strand_options = [s.id for s in strands if s.track_id == new_track_choice]

        with st.form("add_profile"):
            name = st.text_input("Name (e.g. G11-STEM)")
            gl_choice = st.selectbox(
                "Grade level", options=[gl.id for gl in grade_levels], format_func=lambda v: gl_by_id[v].name
            )
            strand_choice = st.selectbox(
                "Strand", options=new_strand_options, format_func=lambda v: strand_by_id[v].name
            )
            if st.form_submit_button("Add"):
                if not name:
                    st.error("Name is required.")
                else:
                    session.add(
                        SubjectProfile(
                            name=name,
                            grade_level_id=gl_choice,
                            track_id=new_track_choice,
                            strand_id=strand_choice,
                        )
                    )
                    try_commit(session, f"Added {name}.")
                    st.rerun()
