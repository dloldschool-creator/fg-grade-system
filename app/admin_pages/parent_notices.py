"""Parent Notices — step 2 of spec §78: who gets the term card, who gets a
concern notice, and who isn't ready yet, per section and term.

**Preview only.** Nothing is emailed, texted or printed from here yet
(steps 3 and 4). What this page writes is the adviser's own input: an
override with its reason (§78.3) and the parent-meeting schedule (§78.5).

The grouping itself is `app.notice_rules.classify`, fed by
`parent_notice_service.load_section_notices` in a fixed number of queries.
No per-learner panel exists, so there's no expander to pay for on every
rerun; the three groups are tables.

Not granted to the School Head yet. §78.6 gives that role the sent record,
which does not exist until step 3, and this page writes.
"""

from datetime import datetime, time

import streamlit as st

from app import parent_notice_service as notices
from app.admin_pages._helpers import (
    clear_text_fields,
    flash,
    get_session,
    render_flashes,
    section_picker,
    text_field,
    try_commit,
)
from app.auth import require_role
from app.display_time import SCHOOL_TZ
from app.models.organization import SchoolYear, Term
from app.notice_rules import GROUP_LABELS, NoticeGroup, may_override
from app.section_access import is_advised_by

DEFAULT_MEETING_TIME = time(9, 0)


def _meeting_text(meeting) -> str:
    if meeting is None:
        return "—"
    text = f"{meeting.meeting_date:%a, %b %d, %Y} {meeting.meeting_time:%I:%M %p}"
    return text + (" (own)" if meeting.is_learner_specific else "")


def _name(row) -> str:
    learner = row.learner
    return f"{learner.last_name}, {learner.first_name}"


def _contact_text(row) -> str:
    parts = []
    if row.has_email:
        parts.append("email")
    if row.has_mobile:
        parts.append("mobile")
    if not parts:
        return "none on file"
    return " + ".join(parts) + ("" if row.learner.notices_consent else " (no consent)")


def _group_table(rows, *, concern: bool) -> list[dict]:
    table = []
    for row in rows:
        entry = {
            "Learner": _name(row),
            "Absent": row.figures.absences,
            "Late": row.figures.lates,
            "Cutting": row.figures.cuttings,
            "Why": "; ".join(row.reasons) or "—",
            "Override": (
                f"{GROUP_LABELS[row.override_decision]} — {row.override_reason}"
                if row.override_decision
                else ""
            ),
            "Parent contact": _contact_text(row),
        }
        if concern:
            entry["Meeting"] = _meeting_text(row.meeting)
        table.append(entry)
    return table


def _not_ready_table(rows) -> list[dict]:
    return [
        {
            "Learner": _name(row),
            "What's missing": "; ".join(row.reasons),
            "Override (waiting)": (
                f"{GROUP_LABELS[row.override_decision]} — {row.override_reason}"
                if row.override_decision
                else ""
            ),
        }
        for row in rows
    ]


def _section_meeting_form(session, section, term, data, current_user) -> None:
    st.subheader("Parent meeting")
    st.caption(
        "The date and time printed on concern letters and included in concern "
        "emails and texts. It applies to every learner in Concern; you can give "
        "one learner a different time below."
    )
    current = data.section_meeting
    form = f"notice_meeting_{section.id}_{term.id}"
    with st.form(form):
        col1, col2 = st.columns(2)
        meeting_date = col1.date_input(
            "Date",
            value=current.meeting_date if current else datetime.now(SCHOOL_TZ).date(),
            key=f"{form}_date",
        )
        meeting_time = col2.time_input(
            "Time",
            value=current.meeting_time if current else DEFAULT_MEETING_TIME,
            step=900,
            key=f"{form}_time",
        )
        columns = st.columns(2) if current else [st]
        if columns[0].form_submit_button("Save meeting schedule"):
            notices.set_meeting(
                session, section_id=section.id, term_id=term.id,
                meeting_date=meeting_date, meeting_time=meeting_time,
                user_id=current_user.id,
            )
            try_commit(session, "Meeting schedule saved.")
            st.rerun()
        if current and columns[1].form_submit_button("Remove schedule"):
            notices.clear_meeting(
                session, section_id=section.id, term_id=term.id, user_id=current_user.id
            )
            try_commit(session, "Meeting schedule removed.")
            st.rerun()
    if current is None:
        st.caption("No meeting set yet — concern letters can't be printed without one.")


def _override_form(session, section, term, data, current_user) -> None:
    candidates = [row for row in data.rows if may_override(row.computed)]
    if not candidates:
        return
    st.subheader("Override a learner")
    st.caption(
        "Move a learner between Release and Concern when the numbers don't tell "
        "the whole story. A reason is required and is kept on record."
    )
    by_id = {row.enrollment_id: row for row in candidates}
    # Section and term both in the key, so switching either builds fresh
    # widgets rather than one whose stored learner isn't in the new list.
    form = f"notice_override_{section.id}_{term.id}"
    with st.form(form):
        choice = st.selectbox(
            "Learner",
            options=list(by_id),
            format_func=lambda v: (
                f"{_name(by_id[v])} — now {GROUP_LABELS[by_id[v].group]}"
                + (" (overridden)" if by_id[v].override_decision else "")
            ),
            key=f"{form}_learner",
        )
        decision = st.radio(
            "Put them in",
            options=[NoticeGroup.RELEASE, NoticeGroup.CONCERN],
            format_func=lambda g: GROUP_LABELS[g],
            horizontal=True,
            key=f"{form}_decision",
        )
        reason = text_field("Reason", key=f"{form}.reason")
        col1, col2 = st.columns(2)
        save = col1.form_submit_button("Save override")
        clear = col2.form_submit_button("Clear override")

    if save:
        row = by_id[choice]
        if decision is row.computed:
            st.error(
                f"{_name(row)} is already {GROUP_LABELS[decision]} on the numbers — "
                "clear the override instead if there is one."
            )
            return
        if not reason.strip():
            st.error("Please give a reason.")
            return
        notices.set_override(
            session, enrollment_id=row.enrollment_id, term_id=term.id,
            decision=decision, reason=reason, user_id=current_user.id,
        )
        if try_commit(session, f"{_name(row)} moved to {GROUP_LABELS[decision]}."):
            clear_text_fields(form)
        st.rerun()
    if clear:
        row = by_id[choice]
        if notices.clear_override(
            session, enrollment_id=row.enrollment_id, term_id=term.id, user_id=current_user.id
        ):
            try_commit(session, f"Override cleared for {_name(row)}.")
        else:
            flash("info", f"{_name(row)} has no override to clear.")
        st.rerun()


def _learner_meeting_form(session, section, term, data, current_user) -> None:
    concern = data.in_group(NoticeGroup.CONCERN)
    if not concern:
        return
    with st.expander("Give one learner a different meeting time"):
        by_id = {row.enrollment_id: row for row in concern}
        form = f"notice_learner_meeting_{section.id}_{term.id}"
        with st.form(form):
            choice = st.selectbox(
                "Learner",
                options=list(by_id),
                format_func=lambda v: f"{_name(by_id[v])} — {_meeting_text(by_id[v].meeting)}",
                key=f"{form}_learner",
            )
            col1, col2 = st.columns(2)
            meeting_date = col1.date_input(
                "Date", value=datetime.now(SCHOOL_TZ).date(), key=f"{form}_date"
            )
            meeting_time = col2.time_input(
                "Time", value=DEFAULT_MEETING_TIME, step=900, key=f"{form}_time"
            )
            col1, col2 = st.columns(2)
            save = col1.form_submit_button("Save for this learner")
            reset = col2.form_submit_button("Use the section's schedule")
        if save:
            notices.set_meeting(
                session, section_id=section.id, term_id=term.id,
                enrollment_id=choice, meeting_date=meeting_date,
                meeting_time=meeting_time, user_id=current_user.id,
            )
            try_commit(session, f"Meeting saved for {_name(by_id[choice])}.")
            st.rerun()
        if reset:
            notices.clear_meeting(
                session, section_id=section.id, term_id=term.id,
                enrollment_id=choice, user_id=current_user.id,
            )
            try_commit(session, f"{_name(by_id[choice])} now uses the section's schedule.")
            st.rerun()


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER")
    st.title("Parent Notices")
    st.caption(
        "Who gets their term card by email, whose parents are asked to come in, "
        "and who isn't ready yet — per section and term."
    )
    st.info(
        "Preview only: nothing is emailed, texted or printed from this page yet.",
        icon="👀",
    )
    render_flashes()

    adviser_scoped = not current_user.has_role("SUPER_ADMIN", "REGISTRAR")

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("No school years yet.")
            return
        sy_by_id = {sy.id: sy for sy in school_years}
        sy_choice = st.selectbox(
            "School year", options=list(sy_by_id), format_func=lambda v: sy_by_id[v].name
        )
        section = section_picker(
            session, sy_choice, key="parent_notices",
            adviser_user_id=current_user.id if adviser_scoped else None,
        )
        if section is None:
            return
        # The picker already scopes an adviser; this is the guard beside
        # the writes, which every form below relies on.
        if adviser_scoped and not is_advised_by(section, str(current_user.id)):
            st.error("You can only manage notices for a section you advise.")
            return

        terms = session.query(Term).filter_by(school_year_id=sy_choice).order_by(Term.term_number).all()
        if not terms:
            st.warning("This school year has no terms yet.")
            return
        term_by_id = {t.id: t for t in terms}
        term_id = st.selectbox(
            "Term", options=list(term_by_id), format_func=lambda v: term_by_id[v].name
        )
        term = term_by_id[term_id]

        data = notices.load_section_notices(session, section, term)
        if data.thresholds is None:
            st.warning(
                "There's no parent-notice policy for this school year yet, so nobody "
                "can be grouped. Ask an admin to add one."
            )
        else:
            st.caption(f"Release means {data.thresholds.describe()} this term.")
        if not data.rows:
            st.info("No learners on this section's roll for the term.")
            return

        release = data.in_group(NoticeGroup.RELEASE)
        concern = data.in_group(NoticeGroup.CONCERN)
        not_ready = data.in_group(NoticeGroup.NOT_READY)
        col1, col2, col3 = st.columns(3)
        col1.metric("Release", len(release))
        col2.metric("Concern", len(concern))
        col3.metric("Not ready", len(not_ready))
        if data.left_before_term_end:
            st.caption(
                f"{data.left_before_term_end} learner(s) left the section before the "
                "term ended and aren't included."
            )
        missing_contact = [
            row for row in release + concern
            if not (row.has_email or row.has_mobile) or not row.learner.notices_consent
        ]
        if missing_contact:
            st.caption(
                f"{len(missing_contact)} learner(s) in Release or Concern have no parent "
                "contact or consent on file — fill it in on the Learner Masterlist. "
                "Their parents can still get a printed letter."
            )

        st.divider()
        st.subheader(f"Release — term card by email ({len(release)})")
        if release:
            st.dataframe(_group_table(release, concern=False), hide_index=True, width="stretch")
        else:
            st.caption("Nobody yet.")

        st.subheader(f"Concern — parents asked to come in ({len(concern)})")
        if concern:
            st.dataframe(_group_table(concern, concern=True), hide_index=True, width="stretch")
        else:
            st.caption("Nobody.")

        st.subheader(f"Not ready ({len(not_ready)})")
        if not_ready:
            st.caption("Finish these learners' grades or attendance and they'll be grouped.")
            st.dataframe(_not_ready_table(not_ready), hide_index=True, width="stretch")
        else:
            st.caption("Everyone's record is complete.")

        st.divider()
        _section_meeting_form(session, section, term, data, current_user)
        _learner_meeting_form(session, section, term, data, current_user)
        st.divider()
        _override_form(session, section, term, data, current_user)
