"""The Learner Masterlist.

**Who may edit what (§3C, §54).** A Registrar or Super Admin works
school-wide. An adviser edits the learners in the sections they advise,
plus any they created that are not enrolled anywhere yet, and can *look
up* anyone else read-only — name, LRN, and which section they are in, so
a transferee can be found before a duplicate is typed. `lrn` is uniquely
indexed and an LRN retyped from a form is how duplicates get made, so
taking the lookup away would trade a privacy gain for a data-integrity
loss. The rule itself lives in `app.learner_access`; this page draws it.

Every write here is audit-logged. A learner's name, sex, birthdate and
LRN are the identity every report the school issues is printed under, and
until 2026-08-21 all four could be overwritten with no record at all.
"""

from datetime import date

import streamlit as st

from app import audit_service
from app.admin_pages._helpers import (
    clear_text_fields,
    flush_or_rollback,
    generation_key,
    get_session,
    keep_panel_open,
    panel_is_open,
    render_flashes,
    text_field,
    try_commit,
    try_delete,
)
from app.auth import require_role
from app.import_pipeline import apply_mapping, missing_required, read_table, suggest_mapping
from app.import_specs import LEARNER_IMPORT
from app.learner_access import editable_learner_ids, may_edit
from app.models.academic_structure import Section
from app.models.enums import EnrollmentStatus, Sex
from app.models.learners import Enrollment, Learner, LearnerAdmissionRecord
from app.models.organization import SchoolYear
from app.models.rbac import User
from app.naming import normalize_name
from app.roster_order import learner_order_by, learner_sort_key

RESULT_LIMIT = 50


def _identity_values(learner: Learner) -> dict:
    """What the identity form writes, and therefore what an audit entry
    compares. One function so a field added to the form but not here
    shows up as a column the log never mentions."""
    return {
        "last_name": learner.last_name,
        "first_name": learner.first_name,
        "middle_name": learner.middle_name,
        "extension_name": learner.extension_name,
        "sex": learner.sex,
        "birthdate": learner.birthdate,
        "lrn": learner.lrn,
    }


def _identity_form(session, learner: Learner, current_user, *, may_delete: bool) -> None:
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

        # Delete is drawn only where it can be used: a disabled button
        # beside Save invites a question the page can't answer well.
        columns = st.columns(2) if may_delete else [st]
        if columns[0].form_submit_button("Save"):
            previous = _identity_values(learner)
            learner.last_name = normalize_name(last_name)
            learner.first_name = normalize_name(first_name)
            learner.middle_name = normalize_name(middle_name)
            learner.extension_name = normalize_name(extension_name)
            learner.sex = Sex(sex)
            learner.birthdate = birthdate
            learner.lrn = lrn.strip() or None
            was, now = audit_service.changes(previous, _identity_values(learner))
            if was:
                audit_service.record(
                    session,
                    action=audit_service.LEARNER_CHANGED,
                    object_type="learners",
                    object_id=learner.id,
                    user_id=current_user.id,
                    previous=was,
                    new=now,
                )
            try_commit(session, "Saved.")
            st.rerun()
        if may_delete and columns[1].form_submit_button("Delete"):
            label = f"{learner.last_name}, {learner.first_name}"
            # Recorded before the row goes, and rolled back with it if the
            # delete is refused — the entry belongs to the same
            # transaction as the change it describes.
            audit_service.record(
                session,
                action=audit_service.LEARNER_DELETED,
                object_type="learners",
                object_id=learner.id,
                user_id=current_user.id,
                previous=_identity_values(learner),
            )
            try_delete(session, learner, label)
            st.rerun()


def _admission_record_form(session, learner: Learner, current_user, record=None) -> None:
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
            created = record is None
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
            # Logged against the learner, not the admission row: that is
            # the object anyone investigating already has an id for, and a
            # record created in this same submit has no id worth citing.
            audit_service.record(
                session,
                action=audit_service.LEARNER_ADMISSION_CHANGED,
                object_type="learners",
                object_id=learner.id,
                user_id=current_user.id,
                new={
                    "created": created,
                    "date_of_shs_admission": date_of_shs_admission,
                    "previous_school_name": previous_school_name or None,
                    "pept_passer": pept_passer,
                    "als_ae_passer": als_passer,
                },
            )
            try_commit(session, "Admission record saved.")
            st.rerun()


def _placements(session, learner_ids) -> dict:
    """Where each of `learner_ids` currently is, as one line of text.

    Only ever called for learners the viewer may *not* edit, and only for
    the ones actually on screen. It is what turns "you can't change this
    one" into something usable, by naming who can.

    Four queries, all batched, none inside the render loop.
    """
    if not learner_ids:
        return {}

    enrollments = (
        session.query(Enrollment).filter(Enrollment.learner_id.in_(list(learner_ids))).all()
    )
    if not enrollments:
        return {}

    years = {
        row.id: row
        for row in session.query(SchoolYear)
        .filter(SchoolYear.id.in_({e.school_year_id for e in enrollments}))
        .all()
    }
    sections = {
        row.id: row
        for row in session.query(Section)
        .filter(Section.id.in_({e.section_id for e in enrollments}))
        .all()
    }
    adviser_ids = {s.adviser_user_id for s in sections.values() if s.adviser_user_id}
    advisers = (
        {row.id: row for row in session.query(User).filter(User.id.in_(adviser_ids)).all()}
        if adviser_ids
        else {}
    )

    # A learner has at most one enrollment per school year, and school
    # year names sort chronologically, so the newest is the one to name.
    newest: dict = {}
    for enrollment in enrollments:
        year = years.get(enrollment.school_year_id)
        key = year.name if year else ""
        if enrollment.learner_id not in newest or key > newest[enrollment.learner_id][0]:
            newest[enrollment.learner_id] = (key, enrollment)

    lines = {}
    for learner_id, (year_name, enrollment) in newest.items():
        section = sections.get(enrollment.section_id)
        adviser = advisers.get(section.adviser_user_id) if section else None
        where = section.name if section else "an unnamed section"
        who = adviser.full_name if adviser else "no adviser on record"
        lines[learner_id] = f"{where} ({year_name}) — adviser: {who}"
    return lines


def _read_only_card(learner: Learner, placement: str | None) -> None:
    """What an adviser sees for someone else's learner.

    Enough to recognise the person and know who to ask, and nothing that
    can be typed into. Searching before adding has to stay possible: an
    LRN is unique, and a learner who cannot be found is a learner who
    gets entered a second time.
    """
    st.write(
        f"**{learner.last_name}, {learner.first_name}** — "
        f"LRN: {learner.lrn or 'not yet assigned'}"
    )
    if placement:
        st.caption(f"Enrolled in {placement}.")
    else:
        st.caption("Not enrolled in any section yet — the registrar can make changes.")


def _listed_learners(session, search: str, adviser_user_id, editable: set) -> list:
    """The learners to draw, and in what order.

    Search is school-wide for everyone; that is what makes the read-only
    lookup a lookup. With the box empty an adviser gets their own people
    rather than the first fifty names in the school, which is both the
    more useful default and the one that matches what they can act on.
    """
    if search:
        like = f"%{search}%"
        return (
            session.query(Learner)
            .filter(
                (Learner.last_name.ilike(like))
                | (Learner.first_name.ilike(like))
                | (Learner.lrn.ilike(like))
            )
            .order_by(Learner.last_name, Learner.first_name)
            .limit(RESULT_LIMIT)
            .all()
        )

    if adviser_user_id is not None:
        if not editable:
            return []
        # No limit: this set is bounded by the sections one person
        # advises, and truncating it would quietly hide learners from the
        # only page their adviser can fix them on. Male first, then
        # female, alphabetical within each — the order every roster and
        # every DepEd form uses.
        return (
            session.query(Learner)
            .filter(Learner.id.in_(list(editable)))
            .order_by(*learner_order_by(Learner))
            .all()
        )

    return (
        session.query(Learner)
        .order_by(Learner.last_name, Learner.first_name)
        .limit(RESULT_LIMIT)
        .all()
    )


def _bulk_upload_section(session, current_user, adviser_user_id) -> None:
    """Bulk-add, sharing the Import from Excel machinery rather than its own.

    This used to carry a second, stricter copy of the same rules, and every
    way the two differed was a way to be rejected here for a file the other
    page would have accepted: headers had to match letter for letter ("Sex"
    failed where "sex" passed), the birthdate had to be ISO even though
    Excel rewrites it to the PC's regional format on save, and an LRN had to
    be manually formatted as text first or Excel turned it into 1.07E+11.

    One implementation means a fix lands in both places at once.

    `adviser_user_id` is None for a Registrar or Super Admin and the
    adviser's own id otherwise; it is what lets an adviser enrol their
    class straight from the file while keeping them out of everyone
    else's sections (§3C). Import from Excel is Registrar-only, so this
    panel is the only way an adviser can enrol in bulk.

    The rows it writes are stamped with `current_user.id` as their creator
    (see `commit_learners`). That matters most on the path below where the
    Section column is *refused* and the learners are created anyway:
    without the stamp they would belong to no section and no creator, and
    the person who had just typed them could not correct a single one.
    """
    spec = LEARNER_IMPORT

    # The whole flow below — preview, errors, confirm — renders inside
    # this panel, so a collapse on upload hid all of it and read as
    # nothing having happened.
    _panel = "learner_bulk_add"
    # The uploader's key hangs off this so a successful import can drop the
    # file; see generation_key.
    _UPLOAD_FORM = "learner_bulk_upload"
    with st.expander("Bulk-add from a spreadsheet", expanded=panel_is_open(_panel)):
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
            + (
                "\n\nAs an adviser you can enrol into the section(s) you advise; "
                "a row naming any other section is refused and the rest of the "
                "file still goes in."
                if adviser_user_id is not None
                else ""
            )
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
            # A generation-carrying key so a successful import can drop the
            # file. Without that the uploader keeps it across the rerun, the
            # panel re-validates it against the rows it just wrote, and every
            # LRN comes back as "already exists in the system" — the import
            # reporting its own success as 26 failures.
            key=generation_key(_UPLOAD_FORM, "learner_csv"),
            on_change=keep_panel_open, args=(_panel,),
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
            years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
            by_id = {sy.id: sy for sy in years}
            school_year_id = st.selectbox(
                "Enroll into which school year?",
                options=[sy.id for sy in years],
                format_func=lambda v: by_id[v].name,
                key="bulk_learner_sy",
                on_change=keep_panel_open, args=(_panel,),
            )
            if adviser_user_id is not None:
                # An adviser may hold more than one section (see the
                # Section model), so this is a list, and naming which ones
                # is what stops a refused row reading as a typo.
                mine = (
                    session.query(Section)
                    .filter_by(school_year_id=school_year_id, adviser_user_id=adviser_user_id)
                    .order_by(Section.name)
                    .all()
                )
                if mine:
                    st.caption(
                        "You can enroll into: **"
                        + "**, **".join(s.name for s in mine)
                        + "**."
                    )
                else:
                    # Refusing every row here would block the learners
                    # from being created at all, which is worse than not
                    # enrolling them — this is what the panel did for
                    # every adviser before they could enrol.
                    st.warning(
                        "You're not the adviser of any section in that school year, "
                        "so the Section column will be ignored. The learners are "
                        "still created, and can be enrolled later on the Enrollment "
                        "page.",
                        icon="⚠️",
                    )
                    mapping.pop("section", None)
                    school_year_id = None

        mapped = apply_mapping(rows, mapping)
        result = spec.validate(
            session,
            mapped,
            mapping,
            school_year_id=school_year_id,
            adviser_user_id=adviser_user_id,
        )

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
            # Drop the file on success, so the rerun shows an empty uploader
            # and the "Added N" flash rather than re-validating the same rows
            # against the database they were just written to. On failure the
            # file stays, because the fix is usually to re-read the errors.
            if try_commit(session, f"Added {written} learner(s)."):
                clear_text_fields(_UPLOAD_FORM)
            st.rerun()


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER")
    st.title("Learner Masterlist")
    render_flashes()

    # Registrar/Super Admin work school-wide; an Adviser-only account is
    # scoped to the learners in the sections they actually advise (§3C,
    # §54), plus any they created who are not enrolled anywhere yet. The
    # same rule was already applied on the Enrollment page to *sections*;
    # this page never applied it to learners, so every adviser could edit
    # every learner in the school.
    adviser_user_id = None if current_user.has_role("SUPER_ADMIN", "REGISTRAR") else current_user.id
    # Deleting a learner is a registrar action. The database already
    # refuses to delete an enrolled one (every foreign key here is ON
    # DELETE RESTRICT), so the button only ever bit on learners with no
    # enrollment — which is exactly the fragile set: just imported, not
    # yet enrolled, quite possibly somebody else's afternoon of typing.
    may_delete = current_user.has_role("SUPER_ADMIN", "REGISTRAR")

    with get_session() as session:
        editable = editable_learner_ids(session, adviser_user_id)

        search = st.text_input("Search by name or LRN")
        learners = _listed_learners(session, search, adviser_user_id, editable)

        if adviser_user_id is not None:
            st.caption(
                "You can edit the learners in the section(s) you advise, and any "
                "you added who aren't enrolled yet. Searching finds anyone in the "
                "school, so you can check whether someone is already here before "
                "adding them — those results are read-only."
            )
        elif not search:
            st.caption(f"Showing the first {RESULT_LIMIT} learners — search to narrow down.")

        mine = [learner for learner in learners if may_edit(learner.id, editable, adviser_user_id)]
        mine_ids = {learner.id for learner in mine}
        others = [learner for learner in learners if learner.id not in mine_ids]

        if not search and adviser_user_id is not None and not mine:
            st.info(
                "No learners yet in the section(s) you advise. Add them below, or "
                "ask the registrar to enroll them."
            )

        # Both lookups batched above the render loop: Streamlit runs an
        # expander's body whether or not it is open, so anything left in
        # there is a round trip per learner on every single interaction.
        admission_records = {
            row.learner_id: row
            for row in session.query(LearnerAdmissionRecord)
            .filter(LearnerAdmissionRecord.learner_id.in_(list(mine_ids)))
            .all()
        } if mine else {}
        placements = _placements(session, [learner.id for learner in others])

        for learner in mine:
            label = f"{learner.last_name}, {learner.first_name}"
            if learner.middle_name:
                label += f" {learner.middle_name[0]}."
            label += f"  —  LRN: {learner.lrn or 'not yet assigned'}"
            with st.expander(label):
                _identity_form(session, learner, current_user, may_delete=may_delete)
                st.divider()
                _admission_record_form(
                    session, learner, current_user, admission_records.get(learner.id)
                )

        if others:
            # No heading, and the caption carries the whole label. The
            # matches above are search results too and have no heading of
            # their own, so naming only this group would read as though
            # the editable half were something else — and any wording
            # about where these learners *are* has nothing to contrast
            # with in the case this exists for: an adviser checking
            # whether a transferee is already in the school matches none
            # of their own, so this is the only group on the page.
            #
            # The rule needs something above it for the same reason.
            if mine:
                st.divider()
            st.caption(
                "Found by your search but not in a section you advise, so these "
                "are read-only. Ask the section's adviser or the registrar to "
                "make a change."
            )
            for learner in sorted(others, key=learner_sort_key):
                _read_only_card(learner, placements.get(learner.id))

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
                        # What keeps this learner editable by the person
                        # adding them when no section is chosen — nobody
                        # advises a learner who isn't enrolled anywhere.
                        created_by_user_id=current_user.id,
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
                        # One entry per learner here, unlike the bulk
                        # panel: a single add is a deliberate act on a
                        # named person, and the LRN typed in it is what
                        # every later record hangs off. See
                        # `commit_learners` for why a bulk import is
                        # attributed by column instead.
                        audit_service.record(
                            session,
                            action=audit_service.LEARNER_CREATED,
                            object_type="learners",
                            object_id=learner.id,
                            user_id=current_user.id,
                            new=_identity_values(learner),
                        )
                        # Sex and Birthdate keep their setting: a roster is
                        # typed in one sitting and the next learner is
                        # usually the same year group.
                        if try_commit(session, message):
                            clear_text_fields("add_learner")
                    st.rerun()

        _bulk_upload_section(session, current_user, adviser_user_id)
