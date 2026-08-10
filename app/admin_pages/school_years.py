import streamlit as st

from app.admin_pages._helpers import flash, flush_or_rollback, get_session, render_flashes, try_commit
from app.auth import require_role
from app.models.enums import GradeEncodingStatus, SchoolYearStatus
from app.models.organization import School, SchoolYear, Term


def render() -> None:
    require_role("SUPER_ADMIN")
    st.title("School Years & Terms")
    st.caption(
        "Term dates are ordinary editable rows, never hardcoded — DepEd calendar "
        "revisions don't require a code change (CLAUDE.md)."
    )
    render_flashes()

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()

        for sy in school_years:
            with st.expander(f"{sy.name}  ({sy.status.value})", expanded=False):
                with st.form(f"edit_sy_{sy.id}"):
                    col1, col2 = st.columns(2)
                    start_date = col1.date_input("Start date", value=sy.start_date)
                    end_date = col2.date_input("End date", value=sy.end_date)
                    status = st.selectbox(
                        "Status",
                        options=[s.value for s in SchoolYearStatus],
                        index=[s.value for s in SchoolYearStatus].index(sy.status.value),
                    )
                    recognition_date = st.date_input(
                        "Recognition date (for award certificates)",
                        value=sy.recognition_date,
                    )
                    recognition_venue = st.text_input(
                        "Recognition venue", value=sy.recognition_venue or ""
                    )
                    if st.form_submit_button("Save school year"):
                        sy.start_date = start_date
                        sy.end_date = end_date
                        sy.status = SchoolYearStatus(status)
                        sy.recognition_date = recognition_date
                        sy.recognition_venue = recognition_venue or None
                        try_commit(session, "Saved.")
                        st.rerun()

                st.subheader("Terms")
                terms = (
                    session.query(Term)
                    .filter_by(school_year_id=sy.id)
                    .order_by(Term.term_number)
                    .all()
                )
                for term in terms:
                    with st.form(f"edit_term_{term.id}"):
                        st.markdown(f"**{term.name}**  —  encoding: {term.grade_encoding_status.value}")
                        col1, col2 = st.columns(2)
                        t_start = col1.date_input(
                            "Start", value=term.start_date, key=f"term_start_{term.id}"
                        )
                        t_end = col2.date_input(
                            "End", value=term.end_date, key=f"term_end_{term.id}"
                        )
                        encoding_status = st.selectbox(
                            "Grade encoding",
                            options=[s.value for s in GradeEncodingStatus],
                            index=[s.value for s in GradeEncodingStatus].index(
                                term.grade_encoding_status.value
                            ),
                            key=f"term_encoding_{term.id}",
                            help="OPEN lets subject teachers encode grades for this term "
                            "on the Gradebook page. Close it once grading is done.",
                        )
                        if st.form_submit_button(f"Save {term.name}"):
                            term.start_date = t_start
                            term.end_date = t_end
                            term.grade_encoding_status = GradeEncodingStatus(encoding_status)
                            session.commit()
                            flash("success", f"{term.name} saved.")
                            st.rerun()

        st.divider()
        st.subheader("Add school year")
        school = session.query(School).one_or_none()
        if school is None:
            st.warning("Create a school record on the School Info page first.")
            return

        with st.form("create_school_year"):
            name = st.text_input("Name (e.g. 2027-2028)")
            col1, col2 = st.columns(2)
            sy_start = col1.date_input("School year start date")
            sy_end = col2.date_input("School year end date")
            st.markdown("**Term 1**")
            c1, c2 = st.columns(2)
            t1_start = c1.date_input("Term 1 start", key="new_t1_start")
            t1_end = c2.date_input("Term 1 end", key="new_t1_end")
            st.markdown("**Term 2**")
            c1, c2 = st.columns(2)
            t2_start = c1.date_input("Term 2 start", key="new_t2_start")
            t2_end = c2.date_input("Term 2 end", key="new_t2_end")
            st.markdown("**Term 3**")
            c1, c2 = st.columns(2)
            t3_start = c1.date_input("Term 3 start", key="new_t3_start")
            t3_end = c2.date_input("Term 3 end", key="new_t3_end")

            if st.form_submit_button("Create school year"):
                if not name:
                    st.error("Name is required.")
                else:
                    new_sy = SchoolYear(
                        school_id=school.id,
                        name=name,
                        start_date=sy_start,
                        end_date=sy_end,
                        status=SchoolYearStatus.DRAFT,
                    )
                    session.add(new_sy)
                    # Flush now (inside flush_or_rollback's error handling)
                    # so new_sy.id is populated before the Terms below
                    # reference it — no ORM relationship() links these two
                    # mappers in this codebase, so SQLAlchemy can't infer
                    # that SchoolYear must insert before Term from the
                    # bare FK column alone; without this it can attempt
                    # them in the wrong order and fail a foreign key check
                    # (this exact bug hit Learner/Enrollment — same fix).
                    if flush_or_rollback(session):
                        for number, term_name, t_start, t_end in [
                            (1, "Term 1", t1_start, t1_end),
                            (2, "Term 2", t2_start, t2_end),
                            (3, "Term 3", t3_start, t3_end),
                        ]:
                            session.add(
                                Term(
                                    school_year_id=new_sy.id,
                                    term_number=number,
                                    name=term_name,
                                    start_date=t_start,
                                    end_date=t_end,
                                )
                            )
                        try_commit(session, f"Created {name} (status DRAFT).")
                    st.rerun()
