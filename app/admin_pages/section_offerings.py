import streamlit as st

from app import audit_service
from app.admin_pages._helpers import (
    flush_or_rollback,
    get_session,
    render_flashes,
    section_picker,
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


# Shown when no profile can be matched to the section by name. A sentinel
# rather than a silent default: see profile_for_section.
PICK_ONE = "- choose a profile -"


def order_changes(offerings, order_by_subject: dict) -> tuple[list, int]:
    """Which offerings a re-apply would actually move, and how many it leaves alone.

    Pure — no session, no Streamlit — because the two rules worth getting
    right are decisions, not database work:

    * a subject the profile does not list is **skipped**, not reset to 0
      (otherwise a manually added offering jumps to the top of the form);
    * a row already holding the desired order is **not rewritten**, so a
      second click does not bump `version` or write audit rows for nothing.
    """
    changes = []
    untouched = 0
    for offering in offerings:
        desired = order_by_subject.get(offering.subject_id)
        if desired is None:
            untouched += 1
            continue
        if offering.display_order != desired:
            changes.append((offering, desired))
    return changes, untouched


def _reapply_profile_order(session, section: Section, profile: SubjectProfile, current_user) -> None:
    """Push the profile's Order onto this section's existing offerings.

    Seeding skips subject/terms it has already created (it never updates
    them), so without this the print order is fixed at the moment a section
    is first seeded and cannot be changed afterwards.

    Deliberately narrow: it writes `display_order` and nothing else, and
    only for subjects the chosen profile actually lists — a manually added
    offering the profile has never heard of keeps the order it was given
    instead of being reset to 0 and jumping to the top.
    """
    entries = (
        session.query(SubjectProfileSubject).filter_by(subject_profile_id=profile.id).all()
    )
    order_by_subject = {e.subject_id: e.display_order for e in entries}

    offerings = (
        session.query(SectionSubjectOffering).filter_by(section_id=section.id).all()
    )
    if not offerings:
        st.warning("This section has no offerings yet — seed them first.")
        return

    # Names for the audit trail, batched: one query rather than one per row.
    names = {
        s.id: s.official_name
        for s in session.query(Subject).filter(
            Subject.id.in_({o.subject_id for o in offerings})
        )
    }

    changes, untouched = order_changes(offerings, order_by_subject)
    for offering, desired in changes:
        previous_order = offering.display_order
        offering.display_order = desired
        # VersionMixin — rule 9. A bulk write that skipped this would let a
        # concurrent editor's stale-version check pass against a row that
        # had in fact moved underneath them.
        offering.version += 1
        audit_service.record(
            session,
            action=audit_service.SUBJECT_OFFERING_CHANGED,
            object_type="section_subject_offerings",
            object_id=offering.id,
            user_id=current_user.id,
            previous={
                "subject": names.get(offering.subject_id, "(unknown subject)"),
                "display_order": previous_order,
            },
            new={"display_order": desired},
        )

    note = f"Re-applied {profile.name} order to {len(changes)} offering(s)."
    if untouched:
        note += f" {untouched} not on this profile left unchanged."
    try_commit(session, note)


def profile_for_section(section: Section, profiles: list[SubjectProfile]):
    """The profile that belongs to this section, or None if it cannot be told.

    `subject_profiles` is keyed by (grade level, track, strand) and carries
    **no section column**, so every section in a strand matches the same set
    -- the four Kitchen Operations sections each list all four profiles. The
    only thing telling them apart is the naming convention
    `G12-TECHPRO-KO-<SECTION>`, which is a string, not a foreign key.

    Matching on it is what lets the picker default to the right one without a
    migration mid-year. It is deliberately strict -- an exact suffix, exactly
    one hit -- because a *wrong* default is worse than none. Profiles in one
    strand differ by subject and by term: the two CSS profiles run the same
    three subjects in swapped terms, and the Kitchen Operations ones differ
    in the subject itself. Seeding from the wrong profile is silent, and
    rule 4 then builds a General Average out of the wrong term pattern.
    """
    suffix = "-" + section.name.strip().upper()
    hits = [p for p in profiles if p.name.strip().upper().endswith(suffix)]
    return hits[0] if len(hits) == 1 else None


def _seed_from_profile(session, section: Section, terms: list[Term], current_user):
    matching_profiles = (
        session.query(SubjectProfile)
        .filter_by(
            grade_level_id=section.grade_level_id,
            track_id=section.track_id,
            strand_id=section.strand_id,
        )
        # Ordered so the list, and the default that lands on its first item,
        # cannot shift with whatever order Postgres returns rows in.
        .order_by(SubjectProfile.name)
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

    mine = profile_for_section(section, matching_profiles)
    options = [p.id for p in matching_profiles]
    if mine is not None:
        index = options.index(mine.id)
    else:
        # No preselection, rather than a stranger's profile: seeding the
        # wrong one is silent, and the offerings list further down the page
        # looks correct either way, so nothing on screen contradicts it.
        options = [None] + options
        index = 0

    with st.form("seed_from_profile"):
        profile_choice = st.selectbox(
            "Subject profile",
            options=options,
            index=index,
            format_func=lambda v: PICK_ONE if v is None else profile_by_id[v].name,
        )
        if mine is None and len(matching_profiles) > 1:
            st.warning(
                f"{len(matching_profiles)} profiles match this section's grade "
                f"level, track and strand, and none is named for "
                f"**{section.name}**, so there is nothing to default to. "
                "Profiles in the same strand can differ by subject and by "
                "term, so check which one you want before seeding."
            )
        st.caption(
            f"Both buttons act on **{section.name}**, using the profile "
            "picked above.\n\n"
            "**Seed offerings from profile** adds every subject and term listed in "
            "the profile above. Anything already added is left alone, so it is safe "
            "to press twice.\n\n"
            "**Re-apply order** changes only the order the subjects appear in, to "
            "match the profile. It adds nothing and removes nothing. Use it when the "
            "section is already set up and you have since changed the profile's "
            "Order. Subjects the profile doesn't list keep the order they have."
        )
        seed_col, order_col = st.columns(2)
        seed = seed_col.form_submit_button("Seed offerings from profile")
        reapply = order_col.form_submit_button("Re-apply profile order")

        if (seed or reapply) and profile_choice is None:
            st.error("Pick a subject profile first.")
            return

        if reapply:
            _reapply_profile_order(
                session, section, profile_by_id[profile_choice], current_user
            )
            st.rerun()

        if seed:
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
                            # Carried from the profile, not left at the
                            # column default. The offering's display_order
                            # is what actually orders the printed forms
                            # (report_card takes the lowest across a
                            # subject's terms); without this the Order set
                            # on the profile stopped at the profile editor
                            # and every seeded offering tied at 0, so SF9
                            # and the term cards fell back to alphabetical.
                            display_order=entry.display_order,
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
        "**This page decides what each learner is graded on.** What you set here is "
        "what appears in the Gradebook, in the averages, and on the report cards and "
        "term cards. Subject Profiles only give a starting point — so if a subject is "
        "missing from a gradebook, this is the page to fix it on."
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

        section = section_picker(
            session, sy_choice, key="section_offerings",
            empty_message="No sections for this school year yet — create one on the Sections page.",
        )
        if section is None:
            return
        section_choice = section.id

        terms = session.query(Term).filter_by(school_year_id=sy_choice).order_by(Term.term_number).all()
        term_by_id = {t.id: t for t in terms}

        st.subheader("Seed from a subject profile")
        _seed_from_profile(session, section, terms, current_user)

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
                    col1, col2, col3, col4, col5 = st.columns([3, 2, 2, 2, 1])
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
                    # Print order on SF9, the term cards, Grade Summary and
                    # Export. A subject running several terms has one row
                    # per term and report_card takes the lowest of them, so
                    # set all its terms the same unless you mean otherwise —
                    # "Re-apply profile order" above does that for you.
                    order = col5.number_input(
                        "Order",
                        min_value=0,
                        value=offering.display_order,
                        step=1,
                        key=f"off_ord_{offering.id}",
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
                                "display_order": offering.display_order,
                            },
                            {
                                "subject_category": cat_by_id[category_choice].name,
                                "status": OfferingStatus(status_choice),
                                "is_required": required,
                                "display_order": order,
                            },
                        )
                        offering.subject_category_id = category_choice
                        offering.status = OfferingStatus(status_choice)
                        offering.is_required = required
                        offering.display_order = order
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
            # Left at 0 this subject sorts before every ordered one. Match
            # the number its neighbours use on the profile if you want it in
            # a particular place — "Re-apply profile order" will not set it,
            # because a manually added subject is not on the profile.
            display_order = st.number_input("Order", min_value=0, value=0, step=1)

            if st.form_submit_button("Add"):
                added = SectionSubjectOffering(
                    school_year_id=sy_choice,
                    section_id=section.id,
                    subject_id=subject_choice,
                    term_id=term_choice,
                    subject_category_id=category_choice,
                    is_required=is_required,
                    display_order=display_order,
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
                            "display_order": display_order,
                            "status": OfferingStatus(status_choice),
                            "created": True,
                        },
                    )
                    try_commit(session, f"Added {all_subjects[subject_choice].official_name}.")
                st.rerun()
