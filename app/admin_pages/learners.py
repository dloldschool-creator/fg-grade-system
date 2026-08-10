from datetime import date

import streamlit as st

from app.admin_pages._helpers import (
    flash,
    flush_or_rollback,
    get_session,
    read_uploaded_csv,
    render_flashes,
    try_commit,
    try_delete,
)
from app.auth import require_role
from app.models.academic_structure import Section
from app.models.enums import EnrollmentStatus, Sex
from app.models.learners import Enrollment, Learner, LearnerAdmissionRecord
from app.models.organization import SchoolYear

RESULT_LIMIT = 50
LEARNER_CSV_COLUMNS = "last_name, first_name, middle_name, extension_name, sex, birthdate, lrn"


def _identity_form(session, learner: Learner) -> None:
    with st.form(f"edit_learner_{learner.id}"):
        col1, col2, col3 = st.columns(3)
        last_name = col1.text_input("Last name", value=learner.last_name, key=f"ln_{learner.id}")
        first_name = col2.text_input("First name", value=learner.first_name, key=f"fn_{learner.id}")
        middle_name = col3.text_input(
            "Middle name", value=learner.middle_name or "", key=f"mn_{learner.id}"
        )
        col1, col2, col3 = st.columns(3)
        extension_name = col1.text_input(
            "Extension (Jr., III, ...)", value=learner.extension_name or "", key=f"ext_{learner.id}"
        )
        sex = col2.selectbox(
            "Sex",
            options=[s.value for s in Sex],
            index=[s.value for s in Sex].index(learner.sex.value),
            key=f"sex_{learner.id}",
        )
        birthdate = col3.date_input("Birthdate", value=learner.birthdate, key=f"bd_{learner.id}")
        lrn = st.text_input(
            "LRN (12 digits, blank if not yet assigned)", value=learner.lrn or "", key=f"lrn_{learner.id}"
        )

        col1, col2 = st.columns(2)
        if col1.form_submit_button("Save"):
            learner.last_name = last_name
            learner.first_name = first_name
            learner.middle_name = middle_name or None
            learner.extension_name = extension_name or None
            learner.sex = Sex(sex)
            learner.birthdate = birthdate
            learner.lrn = lrn.strip() or None
            try_commit(session, "Saved.")
            st.rerun()
        if col2.form_submit_button("Delete"):
            try_delete(session, learner, f"{learner.last_name}, {learner.first_name}")
            st.rerun()


def _admission_record_form(session, learner: Learner) -> None:
    record = (
        session.query(LearnerAdmissionRecord).filter_by(learner_id=learner.id).one_or_none()
    )
    st.caption("SHS-entry eligibility fields (§25) — typically filled in once, at Grade 11 admission.")
    with st.form(f"admission_{learner.id}"):
        date_of_shs_admission = st.date_input(
            "Date of SHS admission",
            value=record.date_of_shs_admission if record else None,
            key=f"shs_date_{learner.id}",
        )
        col1, col2 = st.columns(2)
        jhs_completer = col1.checkbox(
            "JHS completer",
            value=bool(record.junior_high_school_completer) if record else False,
            key=f"jhs_comp_{learner.id}",
        )
        jhs_ga = col2.number_input(
            "JHS general average",
            min_value=0.0,
            max_value=100.0,
            value=float(record.junior_high_school_general_average or 0) if record else 0.0,
            key=f"jhs_ga_{learner.id}",
        )
        col1, col2 = st.columns(2)
        hs_completer = col1.checkbox(
            "HS completer (ALS/other)",
            value=bool(record.high_school_completer) if record else False,
            key=f"hs_comp_{learner.id}",
        )
        hs_ga = col2.number_input(
            "HS general average",
            min_value=0.0,
            max_value=100.0,
            value=float(record.high_school_general_average or 0) if record else 0.0,
            key=f"hs_ga_{learner.id}",
        )
        previous_school_name = st.text_input(
            "Previous school name", value=record.previous_school_name or "" if record else "",
            key=f"prev_name_{learner.id}",
        )
        previous_school_address = st.text_input(
            "Previous school address",
            value=record.previous_school_address or "" if record else "",
            key=f"prev_addr_{learner.id}",
        )
        col1, col2, col3 = st.columns(3)
        pept_passer = col1.checkbox(
            "PEPT passer", value=bool(record.pept_passer) if record else False, key=f"pept_p_{learner.id}"
        )
        pept_rating = col2.number_input(
            "PEPT rating", min_value=0.0, max_value=100.0,
            value=float(record.pept_rating or 0) if record else 0.0, key=f"pept_r_{learner.id}",
        )
        pept_date = col3.date_input(
            "PEPT exam date", value=record.pept_examination_date if record else None,
            key=f"pept_d_{learner.id}",
        )
        col1, col2, col3 = st.columns(3)
        als_passer = col1.checkbox(
            "ALS A&E passer", value=bool(record.als_ae_passer) if record else False,
            key=f"als_p_{learner.id}",
        )
        als_rating = col2.number_input(
            "ALS A&E rating", min_value=0.0, max_value=100.0,
            value=float(record.als_ae_rating or 0) if record else 0.0, key=f"als_r_{learner.id}",
        )
        als_date = col3.date_input(
            "ALS A&E exam date", value=record.als_ae_examination_date if record else None,
            key=f"als_d_{learner.id}",
        )
        clc_name = st.text_input(
            "CLC name", value=record.clc_name or "" if record else "", key=f"clc_n_{learner.id}"
        )
        clc_address = st.text_input(
            "CLC address", value=record.clc_address or "" if record else "", key=f"clc_a_{learner.id}"
        )
        other_notes = st.text_area(
            "Other eligibility notes",
            value=record.other_eligibility_notes or "" if record else "",
            key=f"other_{learner.id}",
        )

        if st.form_submit_button("Save admission record"):
            if record is None:
                record = LearnerAdmissionRecord(learner_id=learner.id)
                session.add(record)
            record.date_of_shs_admission = date_of_shs_admission
            record.junior_high_school_completer = jhs_completer
            record.junior_high_school_general_average = jhs_ga or None
            record.high_school_completer = hs_completer
            record.high_school_general_average = hs_ga or None
            record.previous_school_name = previous_school_name or None
            record.previous_school_address = previous_school_address or None
            record.pept_passer = pept_passer
            record.pept_rating = pept_rating or None
            record.pept_examination_date = pept_date
            record.als_ae_passer = als_passer
            record.als_ae_rating = als_rating or None
            record.als_ae_examination_date = als_date
            record.clc_name = clc_name or None
            record.clc_address = clc_address or None
            record.other_eligibility_notes = other_notes or None
            try_commit(session, "Admission record saved.")
            st.rerun()


def _validate_learner_row(row: dict) -> tuple[dict | None, str | None]:
    last_name = row.get("last_name", "")
    first_name = row.get("first_name", "")
    sex_raw = row.get("sex", "").upper()
    birthdate_raw = row.get("birthdate", "")
    lrn = row.get("lrn", "") or None

    if not last_name or not first_name:
        return None, "last_name and first_name are required"
    if sex_raw not in (Sex.MALE.value, Sex.FEMALE.value):
        return None, "sex must be MALE or FEMALE"
    try:
        birthdate = date.fromisoformat(birthdate_raw)
    except ValueError:
        return None, "birthdate must be YYYY-MM-DD"
    if lrn and (len(lrn) != 12 or not lrn.isdigit()):
        return None, "lrn must be exactly 12 digits"

    return (
        {
            "last_name": last_name,
            "first_name": first_name,
            "middle_name": row.get("middle_name") or None,
            "extension_name": row.get("extension_name") or None,
            "sex": Sex(sex_raw),
            "birthdate": birthdate,
            "lrn": lrn,
        },
        None,
    )


def _bulk_upload_section(session) -> None:
    with st.expander("Bulk-add from CSV"):
        st.caption(
            f"Columns: `{LEARNER_CSV_COLUMNS}` (header row required). "
            "last_name, first_name, sex, birthdate are required; the rest may be blank. "
            "sex must be MALE or FEMALE; birthdate as YYYY-MM-DD. Section assignment isn't "
            "part of this upload — enroll imported learners afterward on the Enrollment page."
        )
        uploaded = st.file_uploader("CSV file", type="csv", key="learner_csv")
        if uploaded is None:
            return

        rows = read_uploaded_csv(uploaded)
        valid_rows, errors = [], []
        for i, row in enumerate(rows, start=2):  # row 1 is the header
            parsed, error = _validate_learner_row(row)
            if error:
                errors.append(f"Row {i}: {error}")
            else:
                valid_rows.append(parsed)

        st.write(f"{len(valid_rows)} of {len(rows)} row(s) valid.")
        if errors:
            st.error("\n".join(errors))
        if valid_rows:
            st.dataframe(valid_rows, hide_index=True)
            if st.button(f"Import {len(valid_rows)} valid learner(s)"):
                for parsed in valid_rows:
                    session.add(Learner(**parsed))
                try_commit(session, f"Imported {len(valid_rows)} learner(s).")
                st.rerun()


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER")
    st.title("Learner Masterlist")
    render_flashes()

    # Registrar/Super Admin can enroll a new learner into any section; an
    # Adviser-only account is scoped to sections they actually advise
    # (§3C) — same rule already applied on the Enrollment page, and this
    # page's "Add learner" form can also enroll, so it needs the same
    # scoping or an adviser could enroll a learner into someone else's
    # section by mistake.
    adviser_user_id = None if current_user.has_role("SUPER_ADMIN", "REGISTRAR") else current_user.id

    with get_session() as session:
        search = st.text_input("Search by name or LRN")

        query = session.query(Learner).order_by(Learner.last_name, Learner.first_name)
        if search:
            like = f"%{search}%"
            query = query.filter(
                (Learner.last_name.ilike(like))
                | (Learner.first_name.ilike(like))
                | (Learner.lrn.ilike(like))
            )
        learners = query.limit(RESULT_LIMIT).all()

        if not search:
            st.caption(f"Showing the first {RESULT_LIMIT} learners — search to narrow down.")

        for learner in learners:
            label = f"{learner.last_name}, {learner.first_name}"
            if learner.middle_name:
                label += f" {learner.middle_name[0]}."
            label += f"  —  LRN: {learner.lrn or 'not yet assigned'}"
            with st.expander(label):
                _identity_form(session, learner)
                st.divider()
                _admission_record_form(session, learner)

        st.divider()
        st.subheader("Add learner")

        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        sy_options = [None] + [sy.id for sy in school_years]
        sy_by_id = {sy.id: sy for sy in school_years}
        sy_choice = st.selectbox(
            "Enroll into school year (optional — leave blank to assign a section later)",
            options=sy_options,
            format_func=lambda v: "— assign later —" if v is None else sy_by_id[v].name,
            key="new_learner_sy",
        )
        section_choice = None
        if sy_choice is not None:
            section_query = session.query(Section).filter_by(school_year_id=sy_choice)
            if adviser_user_id is not None:
                section_query = section_query.filter_by(adviser_user_id=adviser_user_id)
            sections = section_query.order_by(Section.name).all()
            section_by_id = {s.id: s for s in sections}
            if sections:
                section_choice = st.selectbox(
                    "Section",
                    options=[s.id for s in sections],
                    format_func=lambda v: section_by_id[v].name,
                    key="new_learner_section",
                )
            elif adviser_user_id is not None:
                st.caption("You're not the adviser of any section for that school year — learner will be added without enrollment.")
            else:
                st.caption("No sections exist for that school year yet — learner will be added without enrollment.")

        with st.form("add_learner"):
            col1, col2, col3 = st.columns(3)
            last_name = col1.text_input("Last name", key="new_ln")
            first_name = col2.text_input("First name", key="new_fn")
            middle_name = col3.text_input("Middle name", key="new_mn")
            col1, col2, col3 = st.columns(3)
            extension_name = col1.text_input("Extension (Jr., III, ...)", key="new_ext")
            sex = col2.selectbox("Sex", options=[s.value for s in Sex], key="new_sex")
            birthdate = col3.date_input("Birthdate", value=date(2009, 1, 1), key="new_bd")
            lrn = st.text_input("LRN (12 digits, blank if not yet assigned)", key="new_lrn")

            if st.form_submit_button("Add"):
                if not last_name or not first_name:
                    st.error("Last name and first name are required.")
                else:
                    learner = Learner(
                        last_name=last_name,
                        first_name=first_name,
                        middle_name=middle_name or None,
                        extension_name=extension_name or None,
                        sex=Sex(sex),
                        birthdate=birthdate,
                        lrn=lrn.strip() or None,
                    )
                    session.add(learner)
                    # Flush now (inside flush_or_rollback's error handling)
                    # so learner.id is populated before the Enrollment
                    # below references it — no ORM relationship() links
                    # these two mappers in this codebase, so SQLAlchemy
                    # can't infer that Learner must insert before
                    # Enrollment from the bare FK column alone; without
                    # this it can (and did) attempt them in the wrong
                    # order and fail a foreign key check.
                    if flush_or_rollback(session):
                        message = f"Added {last_name}, {first_name}."
                        if sy_choice is not None and section_choice is not None:
                            section = session.get(Section, section_choice)
                            session.add(
                                Enrollment(
                                    learner_id=learner.id,
                                    school_year_id=sy_choice,
                                    grade_level_id=section.grade_level_id,
                                    section_id=section_choice,
                                    enrollment_status=EnrollmentStatus.ENROLLED,
                                )
                            )
                            message += f" Enrolled in {section.name}."
                        try_commit(session, message)
                    st.rerun()

        _bulk_upload_section(session)
