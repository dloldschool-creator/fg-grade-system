import streamlit as st

from app import audit_service
from app.admin_pages._helpers import (
    flush_or_rollback,
    get_session,
    render_flashes,
    try_commit,
    try_delete,
)
from app.auth import require_role
from app.models.academic_structure import Section
from app.models.enums import OfferingStatus
from app.models.organization import SchoolYear, Term
from app.models.subjects import (
    SectionSubjectOffering,
    Subject,
    SubjectCategory,
    SubjectProfile,
    SubjectProfileSubject,
)


def _seed_from_profile(session, section: Section, terms: list[Term]):
    matching_profiles = (
        session.query(SubjectProfile)
        .filter_by(
            grade_level_id=section.grade_level_id,
            track_id=section.track_id,
            strand_id=section.strand_id,
        )
        .all()
    )
    if not matching_profiles:
        st.info(
            "No subject profile matches this section's grade level/track/strand yet — "
            "create one on the Subject Profiles page, or just add offerings manually below."
        )
        return
    profile_by_id = {p.id: p for p in matching_profiles}
    term_by_number = {t.term_number: t for t in terms}

    with st.form("seed_from_profile"):
        profile_choice = st.selectbox(
            "Subject profile", options=[p.id for p in matching_profiles], format_func=lambda v: profile_by_id[v].name
        )
        st.caption(
            "Creates a CONFIRMED offering for each subject/term the profile marks active. "
            "Already-offered subject/terms are skipped, not duplicated."
        )
        if st.form_submit_button("Seed offerings from profile"):
            entries = (
                session.query(SubjectProfileSubject)
                .filter_by(subject_profile_id=profile_choice)
                .all()
            )
            existing = {
                (o.subject_id, o.term_id)
                for o in session.query(SectionSubjectOffering).filter_by(section_id=section.id).all()
            }
            created = 0
            for entry in entries:
                subject = session.get(Subject, entry.subject_id)
                for number, active in [
                    (1, entry.term1_active),
                    (2, entry.term2_active),
                    (3, entry.term3_active),
                ]:
                    if not active or number not in term_by_number:
                        continue
                    term = term_by_number[number]
                    if (subject.id, term.id) in existing:
                        continue
                    session.add(
                        SectionSubjectOffering(
                            school_year_id=section.school_year_id,
                            section_id=section.id,
                            subject_id=subject.id,
                            term_id=term.id,
                            subject_category_id=subject.subject_category_id,
                            is_required=not entry.is_elective,
                            status=OfferingStatus.CONFIRMED,
                        )
                    )
                    created += 1
            try_commit(session, f"Seeded {created} offering(s) from {profile_by_id[profile_choice].name}.")
            st.rerun()


def render() -> None:
    current_user = require_role("SUPER_ADMIN")
    st.title("Section Subject Offerings")
    st.caption(
        "**Single source of truth** for what a learner is actually graded on (§48) — "
        "controls the gradebook, Term Average, Final Grade, General Average, SF9, SF10, "
        "and temp cards. A subject profile only seeds defaults; nothing else may "
        "substitute for what's confirmed here."
    )
    render_flashes()

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("Create a school year first.")
            return
        sy_by_id = {sy.id: sy for sy in school_years}
        sy_choice = st.selectbox(
            "School year", options=[sy.id for sy in school_years], format_func=lambda v: sy_by_id[v].name
        )

        sections = session.query(Section).filter_by(school_year_id=sy_choice).order_by(Section.name).all()
        if not sections:
            st.warning("No sections for this school year yet — create one on the Sections page.")
            return
        section_by_id = {s.id: s for s in sections}
        section_choice = st.selectbox(
            "Section", options=[s.id for s in sections], format_func=lambda v: section_by_id[v].name
        )
        section = section_by_id[section_choice]

        terms = session.query(Term).filter_by(school_year_id=sy_choice).order_by(Term.term_number).all()
        term_by_id = {t.id: t for t in terms}

        st.subheader("Seed from a subject profile")
        _seed_from_profile(session, section, terms)

        st.divider()
        st.subheader("Current offerings")
        offerings = (
            session.query(SectionSubjectOffering)
            .filter_by(section_id=section.id)
            .order_by(SectionSubjectOffering.term_id, SectionSubjectOffering.display_order)
            .all()
        )
        categories = session.query(SubjectCategory).order_by(SubjectCategory.name).all()
        cat_by_id = {c.id: c for c in categories}
        all_subjects = {s.id: s for s in session.query(Subject).all()}

        if not offerings:
            st.caption("No offerings yet for this section.")
        for term in terms:
            term_offerings = [o for o in offerings if o.term_id == term.id]
            if not term_offerings:
                continue
            st.markdown(f"**{term.name}**")
            for offering in term_offerings:
                subject = all_subjects[offering.subject_id]
                with st.form(f"edit_offering_{offering.id}"):
                    col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
                    col1.write(subject.official_name)
                    category_choice = col2.selectbox(
                        "Category",
                        options=[c.id for c in categories],
                        index=[c.id for c in categories].index(offering.subject_category_id),
                        format_func=lambda v: cat_by_id[v].name,
                        key=f"off_cat_{offering.id}",
                        label_visibility="collapsed",
                    )
                    status_choice = col3.selectbox(
                        "Status",
                        options=[s.value for s in OfferingStatus],
                        index=[s.value for s in OfferingStatus].index(offering.status.value),
                        key=f"off_status_{offering.id}",
                        label_visibility="collapsed",
                    )
                    required = col4.checkbox(
                        "Required", value=offering.is_required, key=f"off_req_{offering.id}"
                    )
                    col1, col2 = st.columns(2)
                    save = col1.form_submit_button("Save")
                    delete = col2.form_submit_button("Delete")

                    if save:
                        previous, new = audit_service.changes(
                            {
                                "subject_category": cat_by_id[offering.subject_category_id].name,
                                "status": offering.status,
                                "is_required": offering.is_required,
                            },
                            {
                                "subject_category": cat_by_id[category_choice].name,
                                "status": OfferingStatus(status_choice),
                                "is_required": required,
                            },
                        )
                        offering.subject_category_id = category_choice
                        offering.status = OfferingStatus(status_choice)
                        offering.is_required = required
                        offering.version += 1
                        # Only when something actually differs — §48 makes
                        # this table the source of truth for what a learner
                        # is graded on, so the log should read as a list of
                        # real changes, not one row per Save click.
                        if new:
                            audit_service.record(
                                session,
                                action=audit_service.SUBJECT_OFFERING_CHANGED,
                                object_type="section_subject_offerings",
                                object_id=offering.id,
                                user_id=current_user.id,
                                previous={**previous, "subject": subject.official_name},
                                new=new,
                            )
                        try_commit(session, "Saved.")
                        st.rerun()
                    if delete:
                        audit_service.record(
                            session,
                            action=audit_service.SUBJECT_OFFERING_CHANGED,
                            object_type="section_subject_offerings",
                            object_id=offering.id,
                            user_id=current_user.id,
                            previous={
                                "subject": subject.official_name,
                                "status": offering.status,
                                "is_required": offering.is_required,
                            },
                            new={"deleted": True},
                        )
                        try_delete(session, offering, subject.official_name)
                        st.rerun()

        st.divider()
        st.subheader("Add offering manually")
        eligible_subjects = [
            s
            for s in all_subjects.values()
            if s.grade_level_id == section.grade_level_id
            and (s.track_restriction_id is None or s.track_restriction_id == section.track_id)
        ]
        if not eligible_subjects:
            st.info("No subjects match this section's grade level/track yet.")
            return

        with st.form("add_offering"):
            subject_choice = st.selectbox(
                "Subject", options=[s.id for s in eligible_subjects], format_func=lambda v: all_subjects[v].official_name
            )
            term_choice = st.selectbox(
                "Term", options=[t.id for t in terms], format_func=lambda v: term_by_id[v].name
            )
            default_cat = all_subjects[subject_choice].subject_category_id if subject_choice else None
            category_choice = st.selectbox(
                "Category",
                options=[c.id for c in categories],
                index=[c.id for c in categories].index(default_cat) if default_cat in cat_by_id else 0,
                format_func=lambda v: cat_by_id[v].name,
            )
            is_required = st.checkbox("Required", value=True)
            status_choice = st.selectbox("Status", options=[s.value for s in OfferingStatus])

            if st.form_submit_button("Add"):
                added = SectionSubjectOffering(
                    school_year_id=sy_choice,
                    section_id=section.id,
                    subject_id=subject_choice,
                    term_id=term_choice,
                    subject_category_id=category_choice,
                    is_required=is_required,
                    status=OfferingStatus(status_choice),
                )
                session.add(added)
                if flush_or_rollback(session):
                    audit_service.record(
                        session,
                        action=audit_service.SUBJECT_OFFERING_CHANGED,
                        object_type="section_subject_offerings",
                        object_id=added.id,
                        user_id=current_user.id,
                        new={
                            "subject": all_subjects[subject_choice].official_name,
                            "term": term_by_id[term_choice].name,
                            "is_required": is_required,
                            "status": OfferingStatus(status_choice),
                            "created": True,
                        },
                    )
                    try_commit(session, f"Added {all_subjects[subject_choice].official_name}.")
                st.rerun()
