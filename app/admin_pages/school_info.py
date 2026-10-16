import streamlit as st

from app.admin_pages._helpers import get_session, render_flashes, try_commit
from app.auth import require_role
from app.naming import normalize_name
from app.models.organization import School
from app.text_rules import ADDRESS_MAX, SHORT_MAX


def render() -> None:
    require_role("SUPER_ADMIN")
    st.title("School Info")
    render_flashes()

    with get_session() as session:
        school = session.query(School).one_or_none()

        if school is None:
            st.info("No school record yet — create one below.")
            with st.form("create_school"):
                school_name = st.text_input("School Name", max_chars=ADDRESS_MAX)
                deped_school_id = st.text_input("DepEd School ID", max_chars=20)
                region = st.text_input("Region", max_chars=SHORT_MAX)
                schools_division = st.text_input("Schools Division", max_chars=SHORT_MAX)
                district = st.text_input("District", max_chars=SHORT_MAX)
                address = st.text_input("Address", max_chars=ADDRESS_MAX)
                school_head_name = st.text_input("School Head Name", max_chars=SHORT_MAX)
                school_head_position = st.text_input("School Head Position/Designation", max_chars=SHORT_MAX)
                if st.form_submit_button("Create"):
                    session.add(
                        School(
                            school_name=school_name,
                            deped_school_id=deped_school_id,
                            region=region,
                            schools_division=schools_division,
                            district=district,
                            address=address,
                            school_head_name=normalize_name(school_head_name),
                            school_head_position=school_head_position,
                        )
                    )
                    try_commit(session, "School created.")
                    st.rerun()
            return

        st.caption("These details appear on every printed form and report card.")
        with st.form("edit_school"):
            school_name = st.text_input("School Name", value=school.school_name, max_chars=ADDRESS_MAX)
            deped_school_id = st.text_input("DepEd School ID", value=school.deped_school_id, max_chars=20)
            region = st.text_input("Region", value=school.region, max_chars=SHORT_MAX)
            schools_division = st.text_input("Schools Division", value=school.schools_division, max_chars=SHORT_MAX)
            district = st.text_input("District", value=school.district, max_chars=SHORT_MAX)
            address = st.text_input("Address", value=school.address, max_chars=ADDRESS_MAX)
            school_head_name = st.text_input("School Head Name", value=school.school_head_name, max_chars=SHORT_MAX)
            school_head_position = st.text_input(
                "School Head Position/Designation", value=school.school_head_position,
                max_chars=SHORT_MAX,
            )
            if st.form_submit_button("Save"):
                school.school_name = school_name
                school.deped_school_id = deped_school_id
                school.region = region
                school.schools_division = schools_division
                school.district = district
                school.address = address
                school.school_head_name = normalize_name(school_head_name)
                school.school_head_position = school_head_position
                try_commit(session, "Saved.")
                st.rerun()
