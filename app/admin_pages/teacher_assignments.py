from functools import partial

import streamlit as st

from app.admin_pages._helpers import (
    clear_text_fields,
    generation_key,
    get_session,
    keep_panel_open,
    panel_is_open,
    render_flashes,
    section_picker,
    try_commit,
)
from app.auth import require_role
from app.import_pipeline import (
    apply_mapping,
    missing_columns_message,
    missing_required,
    read_table,
    suggest_mapping,
)
from app.models.organization import SchoolYear
from app.teacher_assignment_import import COLUMNS, build_plan, commit_plan
from app.teacher_assignment_import import template_bytes as import_template_bytes
from app.teacher_assignment_service import (
    active_teachers,
    assign_subject,
    load_section_subjects,
    may_assign,
    unassign_subject,
)

UNASSIGNED = "— unassigned —"
_UPLOAD_FORM = "teacher_assignment_upload"


def _bulk_upload(session, current_user, school_year_id) -> None:
    """Super Admin only: assign many subjects from one Excel file."""
    _panel = "teacher_assignment_bulk"
    with st.expander("Bulk-assign from a spreadsheet", expanded=panel_is_open(_panel)):
        st.caption(
            "Columns: **Section**, **Subject**, **Teacher** (name or email). The template "
            "lists every subject in the selected school year that has no teacher yet — "
            "fill in the Teacher column and upload it. Rows with a blank Teacher are "
            "skipped. Each row covers every term the subject runs. Nothing is created: "
            "a name that doesn't match is reported and the file is not applied."
        )
        st.download_button(
            "Download the template (.xlsx)",
            # Deferred: built when clicked, and it opens its own session.
            data=partial(_template_for_year, school_year_id),
            file_name="teacher-assignments-template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="teacher_assignment_template",
        )
        replace = st.checkbox(
            "Also replace subjects that already have a different teacher",
            help="The old assignment stays on record. Without this, those rows are only listed.",
            key="teacher_assignment_replace",
            on_change=keep_panel_open, args=(_panel,),
        )
        uploaded = st.file_uploader(
            "Excel file (.xlsx)",
            type=["xlsx", "csv"],
            key=generation_key(_UPLOAD_FORM, "teacher_assignment_file"),
            on_change=keep_panel_open, args=(_panel,),
        )
        if uploaded is None:
            return

        headers, rows = read_table(uploaded.getvalue(), uploaded.name)
        if not rows:
            st.warning("That file has a header row but no rows in it.")
            return
        mapping = suggest_mapping(headers, COLUMNS)
        missing = missing_required(mapping, COLUMNS)
        if missing:
            st.error(
                "Couldn't find a column for: **" + "**, **".join(missing) + "**.\n\n"
                f"The file's header row reads: `{'`, `'.join(h for h in headers if h)}`"
            )
            return

        plan = build_plan(session, apply_mapping(rows, mapping), school_year_id, replace=replace)
        st.write(
            f"**{len(plan.assignments)}** to assign · {plan.already} already correct · "
            f"{plan.skipped_blank} blank (skipped) · {len(plan.conflicts)} held by someone else"
            + (" (will be replaced)" if replace else " (skipped)")
        )
        if plan.conflicts and not replace:
            st.dataframe(
                [
                    {"Section": sec.name, "Subject": sr.subject.official_name, "File says": t.full_name}
                    for sr, t, sec in plan.conflicts
                ],
                hide_index=True, width="stretch",
            )
        if plan.errors:
            st.error(
                f"{len(plan.errors)} row(s) need fixing. Nothing is applied until the whole file is clean:"
            )
            st.dataframe(plan.errors, hide_index=True, width="stretch")
            return
        if not plan.assignments:
            return
        st.dataframe(
            [
                {"Section": sec.name, "Subject": sr.subject.official_name, "Teacher": t.full_name}
                for sr, t, sec in plan.assignments
            ],
            hide_index=True, width="stretch",
        )
        if st.button(f"Assign {len(plan.assignments)} subject(s)", key="teacher_assignment_commit"):
            written = commit_plan(session, plan, current_user.id)
            if try_commit(session, f"Assigned {len(plan.assignments)} subject(s) ({written} term rows)."):
                clear_text_fields(_UPLOAD_FORM)
            st.rerun()


def _template_for_year(school_year_id) -> bytes:
    with get_session() as session:
        return import_template_bytes(session, school_year_id)


def render() -> None:
    # An adviser may assign within their own section (§3C). They are not
    # assigning *themselves*: the Gradebook grants encoding rights from
    # this table, so self-service would let a teacher grant themselves a
    # roster. Granting stays a decision someone else makes.
    current_user = require_role("SUPER_ADMIN", "ADVISER")
    is_admin = current_user.has_role("SUPER_ADMIN")

    st.title("Teacher Assignments")
    st.caption(
        "Name the teacher for a subject and it applies to every term that subject "
        "runs. Reassigning keeps the old assignment on record, so who taught what "
        "is never lost."
    )
    render_flashes()

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("Create a school year first.")
            return
        sy_by_id = {sy.id: sy for sy in school_years}
        sy_choice = st.selectbox(
            "School year",
            options=[sy.id for sy in school_years],
            format_func=lambda v: sy_by_id[v].name,
        )

        if is_admin:
            _bulk_upload(session, current_user, sy_choice)

        section = section_picker(
            session,
            sy_choice,
            key="teacher_assignments",
            adviser_user_id=None if is_admin else current_user.id,
            empty_message=(
                None
                if is_admin
                else "You're not the adviser of any section in that school year."
            ),
        )
        if section is None:
            return

        # Belt and braces: the picker already scoped the list, but the
        # permission is checked against the section that came back rather
        # than trusted from how it was chosen.
        if not may_assign(section, current_user):
            st.error("You can only assign teachers in a section you advise.")
            return

        teachers = active_teachers(session)
        if not teachers:
            st.info(
                "No users with the SUBJECT_TEACHER role yet — grant that role on the "
                "Users & Roles page first."
            )
            return
        teacher_by_id = {str(t.id): t for t in teachers}
        options = [UNASSIGNED] + [str(t.id) for t in teachers]

        rows = load_section_subjects(session, section.id, sy_choice)
        if not rows:
            st.info(
                "No subject offerings for this section yet — those are set up on the "
                "Section Subject Offerings page."
            )
            return

        unassigned = sum(1 for row in rows if row.is_unassigned)
        st.write(
            f"**{section.name}** — {len(rows)} subject(s), "
            + (f"**{unassigned} without a teacher**." if unassigned else "all assigned.")
        )

        for row in rows:
            terms = "".join(str(n) for n in row.term_numbers)
            with st.form(f"assign_{section.id}_{row.subject.id}"):
                col1, col2, col3 = st.columns([4, 4, 2])
                col1.write(f"**{row.subject.official_name}**")
                col1.caption(
                    f"Term{'s' if len(row.term_numbers) > 1 else ''} {terms}"
                    f"  ·  {len(row.offerings)} offering(s)"
                )

                sole = row.sole_teacher_id
                index = options.index(sole) if sole in options else 0
                choice = col2.selectbox(
                    "Teacher",
                    options=options,
                    index=index,
                    format_func=lambda v: v if v == UNASSIGNED else teacher_by_id[v].full_name,
                    # Keyed by section+subject, not just subject: a subject
                    # like Work Immersion is the same catalog row across
                    # every section that offers it (nine, today), so a bare
                    # f"teacher_{row.subject.id}" key is reused verbatim when
                    # switching sections in one browser session — Streamlit
                    # then keeps whichever teacher was last picked/shown for
                    # that subject anywhere, ignoring this section's own
                    # `index=`. The "Current: ..." caption reads `sole`
                    # straight from the DB so it stayed correct while the
                    # dropdown silently showed a different section's value —
                    # a stale-but-plausible selection is the dangerous case,
                    # since pressing Assign on it reassigns for real.
                    key=f"teacher_{section.id}_{row.subject.id}",
                    label_visibility="collapsed",
                )

                if row.is_split:
                    # Legal, and picking one to display would hide it.
                    names = ", ".join(
                        sorted(
                            teacher_by_id[t].full_name
                            for t in row.holders
                            if t in teacher_by_id
                        )
                    )
                    col3.caption(f"Split across terms: {names}")
                elif sole:
                    col3.caption(f"Current: {teacher_by_id[sole].full_name}")
                else:
                    col3.caption("Current: none")

                col1, col2 = st.columns(2)
                save = col1.form_submit_button("Assign")
                clear = col2.form_submit_button(
                    "Unassign", disabled=row.is_unassigned
                )

                if save:
                    if choice == UNASSIGNED:
                        st.warning("Pick a teacher, or use Unassign.")
                    else:
                        written, replaced = assign_subject(
                            session,
                            row,
                            choice,
                            actor_user_id=current_user.id,
                            section=section,
                        )
                        if not written:
                            st.info("Already assigned to that teacher.")
                        else:
                            message = (
                                f"{teacher_by_id[choice].full_name} now teaches "
                                f"{row.subject.official_name} ({written} term"
                                f"{'s' if written != 1 else ''})."
                            )
                            if replaced:
                                message += " The previous assignment was kept on record."
                            # try_commit, not a bare commit: two people
                            # assigning the same offering at once is an
                            # IntegrityError from the partial unique index,
                            # and it should read as a message rather than
                            # crash the page.
                            if try_commit(session, message):
                                st.rerun()
                if clear:
                    cleared = unassign_subject(
                        session, row, actor_user_id=current_user.id, section=section
                    )
                    if cleared and try_commit(
                        session,
                        f"{row.subject.official_name} has no teacher assigned now.",
                    ):
                        st.rerun()
