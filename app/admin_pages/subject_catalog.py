import streamlit as st

from app.admin_pages._helpers import (
    clear_text_fields,
    get_session,
    read_uploaded_csv,
    render_flashes,
    stateful_tabs,
    text_field,
    try_commit,
    try_delete,
)
from app.auth import require_role
from app.models.academic_structure import GradeLevel, Track
from app.models.subjects import Subject, SubjectCategory

SUBJECT_CSV_COLUMNS = "code, official_name, short_name, grade_level_code, category_code, track_restriction_code"


def _categories_tab(session):
    categories = session.query(SubjectCategory).order_by(SubjectCategory.name).all()
    for cat in categories:
        with st.form(f"edit_cat_{cat.id}"):
            col1, col2, col3 = st.columns([2, 4, 2])
            code = col1.text_input("Code", value=cat.code, key=f"cat_code_{cat.id}")
            name = col2.text_input("Name", value=cat.name, key=f"cat_name_{cat.id}")
            if col3.form_submit_button("Save"):
                cat.code = code.upper()
                cat.name = name
                try_commit(session, "Saved.")
                st.rerun()

    st.divider()
    with st.form("add_category"):
        st.subheader("Add subject category")
        code = text_field("Code", key="add_category.code")
        name = text_field("Name", key="add_category.name")
        if st.form_submit_button("Add"):
            if not code or not name:
                st.error("Code and name are required.")
            else:
                session.add(SubjectCategory(code=code.upper(), name=name))
                if try_commit(session, f"Added {name}."):
                    clear_text_fields("add_category")
                st.rerun()


def _validate_subject_row(row, gl_by_code, cat_by_code, track_by_code, taken_codes) -> tuple[dict | None, str | None]:
    code = row.get("code", "")
    official_name = row.get("official_name", "")
    short_name = row.get("short_name", "")
    gl_code = row.get("grade_level_code", "").upper()
    cat_code = row.get("category_code", "").upper()
    track_code = row.get("track_restriction_code", "").upper() or None

    if not code or not official_name or not short_name:
        return None, "code, official_name, and short_name are required"
    if code in taken_codes:
        return None, f"code '{code}' is already used (existing subject or earlier row in this file)"
    if gl_code not in gl_by_code:
        return None, f"grade_level_code '{gl_code}' doesn't match any Grade Level"
    if cat_code not in cat_by_code:
        return None, f"category_code '{cat_code}' doesn't match any Subject Category"
    if track_code is not None and track_code not in track_by_code:
        return None, f"track_restriction_code '{track_code}' doesn't match any Track"

    return (
        {
            "code": code,
            "official_name": official_name,
            "short_name": short_name,
            "grade_level_id": gl_by_code[gl_code].id,
            "subject_category_id": cat_by_code[cat_code].id,
            "track_restriction_id": track_by_code[track_code].id if track_code else None,
        },
        None,
    )


def _bulk_upload_subjects(session, grade_levels, categories, tracks) -> None:
    gl_by_code = {gl.code: gl for gl in grade_levels}
    cat_by_code = {c.code: c for c in categories}
    track_by_code = {t.code: t for t in tracks}

    with st.expander("Bulk-add from CSV"):
        st.caption(
            f"Your file needs a header row with these columns: `{SUBJECT_CSV_COLUMNS}`. "
            "Leave the **track_restriction_code** column empty if the subject is "
            "offered under every track. "
            f"Grade level codes: {', '.join(gl_by_code) or 'none yet'}. "
            f"Category codes: {', '.join(cat_by_code) or 'none yet'}. "
            f"Track codes: {', '.join(track_by_code) or 'none yet'}."
        )
        uploaded = st.file_uploader("CSV file", type="csv", key="subject_csv")
        if uploaded is None:
            return

        rows = read_uploaded_csv(uploaded)
        taken_codes = {code for (code,) in session.query(Subject.code).all()}

        valid_rows, errors = [], []
        for i, row in enumerate(rows, start=2):
            parsed, error = _validate_subject_row(row, gl_by_code, cat_by_code, track_by_code, taken_codes)
            if error:
                errors.append(f"Row {i}: {error}")
            else:
                valid_rows.append(parsed)
                taken_codes.add(parsed["code"])

        st.write(f"{len(valid_rows)} of {len(rows)} row(s) valid.")
        if errors:
            st.error("\n".join(errors))
        if valid_rows:
            st.dataframe(
                [{"code": r["code"], "official_name": r["official_name"], "short_name": r["short_name"]} for r in valid_rows],
                hide_index=True,
            )
            if st.button(f"Import {len(valid_rows)} valid subject(s)"):
                for parsed in valid_rows:
                    session.add(Subject(**parsed))
                try_commit(session, f"Imported {len(valid_rows)} subject(s).")
                st.rerun()


def _subjects_tab(session):
    grade_levels = session.query(GradeLevel).order_by(GradeLevel.display_order).all()
    gl_by_id = {gl.id: gl for gl in grade_levels}
    tracks = session.query(Track).order_by(Track.display_order).all()
    track_by_id = {t.id: t for t in tracks}
    categories = session.query(SubjectCategory).order_by(SubjectCategory.name).all()
    cat_by_id = {c.id: c for c in categories}

    gl_filter = st.selectbox(
        "Filter by grade level",
        options=["All"] + [gl.id for gl in grade_levels],
        format_func=lambda v: "All" if v == "All" else gl_by_id[v].name,
    )

    query = session.query(Subject).order_by(Subject.grade_level_id, Subject.sort_order)
    if gl_filter != "All":
        query = query.filter(Subject.grade_level_id == gl_filter)
    subjects = query.all()

    for subject in subjects:
        with st.expander(f"{subject.official_name} ({gl_by_id[subject.grade_level_id].code})"):
            with st.form(f"edit_subject_{subject.id}"):
                code = st.text_input("Code", value=subject.code, key=f"subj_code_{subject.id}")
                official_name = st.text_input(
                    "Official name", value=subject.official_name, key=f"subj_on_{subject.id}"
                )
                short_name = st.text_input(
                    "Short name", value=subject.short_name, key=f"subj_sn_{subject.id}"
                )
                gl_choice = st.selectbox(
                    "Grade level",
                    options=[gl.id for gl in grade_levels],
                    index=[gl.id for gl in grade_levels].index(subject.grade_level_id),
                    format_func=lambda v: gl_by_id[v].name,
                    key=f"subj_gl_{subject.id}",
                )
                cat_choice = st.selectbox(
                    "Category",
                    options=[c.id for c in categories],
                    index=[c.id for c in categories].index(subject.subject_category_id),
                    format_func=lambda v: cat_by_id[v].name,
                    key=f"subj_cat_{subject.id}",
                )
                track_options = [None] + [t.id for t in tracks]
                track_choice = st.selectbox(
                    "Track restriction (None = offered under any track)",
                    options=track_options,
                    index=track_options.index(subject.track_restriction_id),
                    format_func=lambda v: "None" if v is None else track_by_id[v].name,
                    key=f"subj_track_{subject.id}",
                )
                is_active = st.checkbox(
                    "Active", value=subject.is_active, key=f"subj_active_{subject.id}"
                )

                col1, col2 = st.columns(2)
                if col1.form_submit_button("Save"):
                    subject.code = code
                    subject.official_name = official_name
                    subject.short_name = short_name
                    subject.grade_level_id = gl_choice
                    subject.subject_category_id = cat_choice
                    subject.track_restriction_id = track_choice
                    subject.is_active = is_active
                    try_commit(session, "Saved.")
                    st.rerun()
                if col2.form_submit_button("Delete", type="secondary"):
                    try_delete(session, subject, subject.official_name)
                    st.rerun()

    st.divider()
    with st.form("add_subject"):
        st.subheader("Add subject")
        code = text_field("Code", key="add_subject.code")
        official_name = text_field("Official name", key="add_subject.official_name")
        short_name = text_field("Short name", key="add_subject.short_name")
        gl_choice = st.selectbox(
            "Grade level",
            options=[gl.id for gl in grade_levels],
            format_func=lambda v: gl_by_id[v].name,
            key="new_subj_gl",
        )
        cat_choice = st.selectbox(
            "Category",
            options=[c.id for c in categories],
            format_func=lambda v: cat_by_id[v].name,
            key="new_subj_cat",
        )
        track_options = [None] + [t.id for t in tracks]
        track_choice = st.selectbox(
            "Track restriction (None = offered under any track)",
            options=track_options,
            format_func=lambda v: "None" if v is None else track_by_id[v].name,
            key="new_subj_track",
        )
        if st.form_submit_button("Add"):
            if not code or not official_name or not short_name:
                st.error("Code, official name, and short name are required.")
            else:
                session.add(
                    Subject(
                        code=code,
                        official_name=official_name,
                        short_name=short_name,
                        grade_level_id=gl_choice,
                        subject_category_id=cat_choice,
                        track_restriction_id=track_choice,
                    )
                )
                if try_commit(session, f"Added {official_name}."):
                    clear_text_fields("add_subject")
                st.rerun()

    _bulk_upload_subjects(session, grade_levels, categories, tracks)


def render() -> None:
    require_role("SUPER_ADMIN")
    st.title("Subject Catalog")
    st.caption(
        "Grade 12 electives named \"Elective 2\" or \"Elective 3\" are placeholders, "
        "not real subject names — rename them here before using them in a section."
    )
    render_flashes()

    with get_session() as session:
        choice = stateful_tabs("subject_catalog_tab", ["Subject Categories", "Subjects"])
        if choice == "Subject Categories":
            _categories_tab(session)
        elif choice == "Subjects":
            _subjects_tab(session)
