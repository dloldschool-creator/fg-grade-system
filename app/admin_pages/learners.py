from datetime import date

import streamlit as st

from app.admin_pages._helpers import (
    clear_text_fields,
    flash,
    flush_or_rollback,
    get_session,
    render_flashes,
    text_field,
    try_commit,
    try_delete,
)
from app.auth import require_role
from app.import_pipeline import apply_mapping, missing_required, read_table, suggest_mapping
from app.import_specs import LEARNER_IMPORT
from app.models.academic_structure import Section
from app.models.enums import EnrollmentStatus, Sex
from app.models.learners import Enrollment, Learner, LearnerAdmissionRecord
from app.naming import normalize_name
from app.models.organization import SchoolYear

RESULT_LIMIT = 50


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
            learner.last_name = normalize_name(last_name)
            learner.first_name = normalize_name(first_name)
            learner.middle_name = normalize_name(middle_name)
            learner.extension_name = normalize_name(extension_name)
            learner.sex = Sex(sex)
            learner.birthdate = birthdate
            learner.lrn = lrn.strip() or None
            try_commit(session, "Saved.")
            st.rerun()
        if col2.form_submit_button("Delete"):
            try_delete(session, learner, f"{learner.last_name}, {learner.first_name}")
            st.rerun()


def _admission_record_form(session, learner: Learner, record=None) -> None:
    # `record` comes preloaded from the caller's batched lookup. Fetching
    # it here was one round trip per learner on the list, paid whether or
    # not the panel was open — Streamlit runs an expander's body either way.
    st.caption("Usually filled in once, when the learner is admitted to Grade 11.")
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


def _bulk_upload_section(session, current_user) -> None:
    """Bulk-add, sharing the Import from Excel machinery rather than its own.

    This used to carry a second, stricter copy of the same rules, and every
    way the two differed was a way to be rejected here for a file the other
    page would have accepted: headers had to match letter for letter ("Sex"
    failed where "sex" passed), the birthdate had to be ISO even though
    Excel rewrites it to the PC's regional format on save, and an LRN had to
    be manually formatted as text first or Excel turned it into 1.07E+11.

    One implementation means a fix lands in both places at once.
    """
    spec = LEARNER_IMPORT
    may_enrol = current_user.has_role("SUPER_ADMIN", "REGISTRAR")

    with st.expander("Bulk-add from a spreadsheet"):
        st.info(
            "**Adding a whole year group? Use Import from Excel instead.** "
            "It walks through the same checks with a bigger preview and keeps "
            "a record of the import. This is the quick option for a small batch.",
            icon="💡",
        )
        st.caption(
            "One row per learner. Put these in the first row — **capitals, "
            "spaces and underscores don't matter**, and extra columns are "
            "ignored:\n\n"
            f"`{'`, `'.join(c.label for c in spec.columns)}`\n\n"
            "**Last Name**, **First Name**, **Sex** and **Birthdate** are "
            "required; the rest can be left empty. Sex can be M/F or "
            "MALE/FEMALE. **Section** is optional — fill it in and the learner "
            "is enrolled into that section in the same step."
        )
        st.caption(
            "**Save the file as Excel (.xlsx).** Nothing else needs "
            "reformatting — LRNs come through whole and birthdates are read in "
            "whatever order your Excel writes them. Never save it as CSV: that "
            "shortens a 12-digit LRN to `1.07E+11` and the last digits are gone "
            "for good, so those rows are refused rather than guessed at."
        )

        uploaded = st.file_uploader(
            "Excel file (.xlsx)",
            # CSV is still accepted so an older file already saved that way
            # still uploads, but it is never offered — see the caption above.
            type=["csv", "xlsx"],
            key="learner_csv",
        )
        if uploaded is None:
            return

        headers, rows = read_table(uploaded.getvalue(), uploaded.name)
        if not rows:
            st.warning("That file has a header row but no learners in it.")
            return

        mapping = suggest_mapping(headers, spec)
        missing = missing_required(mapping, spec)
        if missing:
            st.error(
                "Couldn't find a column for: **"
                + "**, **".join(missing)
                + f"**.\n\nThe file's header row reads: `{'`, `'.join(h for h in headers if h)}`"
            )
            return

        # Only offered when a Section column is actually present, so the
        # common "just create the learners" case asks nothing extra.
        school_year_id = None
        if mapping.get("section"):
            if may_enrol:
                years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
                by_id = {sy.id: sy for sy in years}
                school_year_id = st.selectbox(
                    "Enroll into which school year?",
                    options=[sy.id for sy in years],
                    format_func=lambda v: by_id[v].name,
                    key="bulk_learner_sy",
                )
            else:
                st.warning(
                    "The Section column will be ignored — only a Registrar or "
                    "Super Admin can enroll from here. The learners will still "
                    "be created, and you can enroll your own section on the "
                    "Enrollment page.",
                    icon="⚠️",
                )
                mapping.pop("section", None)

        mapped = apply_mapping(rows, mapping)
        result = spec.validate(session, mapped, mapping, school_year_id=school_year_id)

        st.write(f"**{len(result.parsed)} of {len(rows)} row(s) ready to add.**")
        if result.errors:
            st.error(f"{len(result.errors)} row(s) need fixing before they can be added:")
            st.dataframe(result.error_dicts(), hide_index=True, use_container_width=True)

        if not result.parsed:
            return

        preview = [
            {k: v for k, v in row.items() if not k.startswith("__") and not k.endswith("_id")}
            for row in result.parsed
        ]
        st.dataframe(preview, hide_index=True, use_container_width=True)

        if st.button(f"Add {len(result.parsed)} learner(s)", key="bulk_learner_commit"):
            written = spec.commit(session, result.parsed, current_user.id)
            try_commit(session, f"Added {written} learner(s).")
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

        admission_records = {
            row.learner_id: row
            for row in session.query(LearnerAdmissionRecord)
            .filter(LearnerAdmissionRecord.learner_id.in_([l.id for l in learners]))
            .all()
        } if learners else {}

        for learner in learners:
            label = f"{learner.last_name}, {learner.first_name}"
            if learner.middle_name:
                label += f" {learner.middle_name[0]}."
            label += f"  —  LRN: {learner.lrn or 'not yet assigned'}"
            with st.expander(label):
                _identity_form(session, learner)
                st.divider()
                _admission_record_form(
                    session, learner, admission_records.get(learner.id)
                )

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
            last_name = text_field("Last name", key="add_learner.last_name", container=col1)
            first_name = text_field("First name", key="add_learner.first_name", container=col2)
            middle_name = text_field("Middle name", key="add_learner.middle_name", container=col3)
            col1, col2, col3 = st.columns(3)
            extension_name = text_field(
                "Extension (Jr., III, ...)", key="add_learner.extension_name", container=col1
            )
            sex = col2.selectbox("Sex", options=[s.value for s in Sex], key="new_sex")
            birthdate = col3.date_input("Birthdate", value=date(2009, 1, 1), key="new_bd")
            lrn = text_field("LRN (12 digits, blank if not yet assigned)", key="add_learner.lrn")

            if st.form_submit_button("Add"):
                last_name = normalize_name(last_name)
                first_name = normalize_name(first_name)
                if not last_name or not first_name:
                    st.error("Last name and first name are required.")
                else:
                    learner = Learner(
                        last_name=last_name,
                        first_name=first_name,
                        middle_name=normalize_name(middle_name),
                        extension_name=normalize_name(extension_name),
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
                        # Sex and Birthdate keep their setting: a roster is
                        # typed in one sitting and the next learner is
                        # usually the same year group.
                        if try_commit(session, message):
                            clear_text_fields("add_learner")
                    st.rerun()

        _bulk_upload_section(session, current_user)
