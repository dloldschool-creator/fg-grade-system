"""Parent Notices — spec §78: who gets the term card by email, whose
parents are asked to come in, and who isn't ready yet, per section and term.

Step 3 sends the Release group's term cards (§78.4) through
`parent_notice_service.send_term_cards`, which owns the double-send guard
and the per-message commits; this page only decides whether to offer the
button and reports the result. Concern notices are step 4.

The page also writes the adviser's own input: an override with its reason
(§78.3) and the parent-meeting schedule (§78.5). The grouping itself is
`app.notice_rules.classify`, fed by `load_section_notices` in a fixed
number of queries; the three groups are tables, so there is no
per-learner expander to pay for on every rerun.

**The School Head reaches it read-only** (§78.6: the sent record). Every
write — sending, overrides, schedules — sits behind `may_write`, which is
False for `is_read_only()` accounts.
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
from app.notice_messages import NoticeContext, term_card_email
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
        status = None if concern else notices.card_email_status(row).label
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
        else:
            entry["Term card email"] = status
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


def _email_panel(session, section, term, data, current_user, *, may_write: bool) -> None:
    """Emailing the Release group's term cards (step 3)."""
    from app.models.rbac import User
    from app.notice_mailer import MailAuthError, MailNotConfigured, MailSettings

    release = data.in_group(NoticeGroup.RELEASE)
    if not release:
        return
    statuses = {row.enrollment_id: notices.card_email_status(row) for row in release}
    ready = [row for row in release if statuses[row.enrollment_id].sendable]
    fresh = [row for row in ready if not statuses[row.enrollment_id].is_resend]
    outdated = [row for row in ready if statuses[row.enrollment_id].is_resend]
    stuck = [
        row for row in release
        if statuses[row.enrollment_id].label.startswith("interrupted")
    ]

    st.subheader("Email the term cards")
    st.caption(
        "Each parent gets one email with their child's term card attached, "
        "protected by the child's birthdate (YYYYMMDD) as the password. Replies "
        "go to the adviser. A card is never emailed twice."
    )
    if not may_write:
        st.caption(f"{len(fresh)} ready to email.")
        return

    settings = MailSettings.from_env()
    if settings is None:
        st.info(
            "Emailing isn't set up yet: the school's sending account has to be "
            "added to the app's settings first. Ask the ICT Coordinator.",
            icon="✉️",
        )
        return
    if notices.term_encoding_open(term, datetime.now(SCHOOL_TZ).date()):
        st.warning(
            f"Grades for {term.name} can still be changed, so term cards can't be "
            "emailed yet. Sending opens once encoding for the term has closed."
        )
        return
    adviser = session.get(User, section.adviser_user_id) if section.adviser_user_id else None
    if adviser is None:
        st.warning("This section has no adviser on record, so there's nobody for replies to reach.")
        return

    if stuck:
        st.warning(
            f"{len(stuck)} email(s) were interrupted before they finished and may "
            "already have reached the parent. Check with the parent before sending again."
        )
        if st.button("Allow these to be sent again", key=f"release_stuck_{section.id}_{term.id}"):
            notices.release_stuck_sends(session, stuck, term_id=term.id, user_id=current_user.id)
            try_commit(session, "Interrupted emails can be sent again.")
            st.rerun()

    if not ready:
        st.caption("Nothing to send right now.")
        return

    if fresh:
        first = fresh[0]
        with st.expander("Preview the email"):
            ctx = _preview_context(first, section, term, adviser, session)
            subject, body = term_card_email(ctx)
            st.text(f"To: {first.learner.guardian_email}\nSubject: {subject}\n\n{body}")
        if st.button(
            "Send a test to my own email first",
            key=f"test_card_{section.id}_{term.id}",
            help=f"Sends {_name(first)}'s card to {current_user.email}, marked as a test.",
        ):
            try:
                notices.send_test_card(
                    session, section, term, first, adviser=adviser,
                    to=current_user.email, settings=settings,
                )
                flash("success", f"Test email sent to {current_user.email}.")
            except (MailNotConfigured, MailAuthError, OSError) as exc:
                flash("error", f"The test email couldn't be sent: {exc}")
            st.rerun()

    targets = fresh
    label = f"Email {len(fresh)} term card(s)"
    if outdated:
        resend = st.checkbox(
            f"Also re-send {len(outdated)} card(s) that changed after they were emailed",
            key=f"resend_{section.id}_{term.id}",
        )
        if resend:
            targets = fresh + outdated
            label = f"Email {len(targets)} term card(s)"
    if not targets:
        return
    confirmed = st.checkbox(
        "I've checked the Release list above", key=f"confirm_send_{section.id}_{term.id}"
    )
    if st.button(label, type="primary", disabled=not confirmed,
                 key=f"send_cards_{section.id}_{term.id}"):
        bar = st.progress(0.0, text="Sending…")
        try:
            result = notices.send_term_cards(
                session, section, term, targets, user_id=current_user.id,
                adviser=adviser, settings=settings,
                progress=lambda done, total: bar.progress(
                    done / total, text=f"Sending… {done} of {total}"
                ),
            )
        except (MailNotConfigured, MailAuthError, OSError) as exc:
            bar.empty()
            flash("error", f"Nothing was sent: {exc}")
            st.rerun()
        bar.empty()
        if result.stopped:
            flash("error", f"Sending stopped: {result.stopped}")
        message = f"Emailed {result.sent} term card(s)."
        if result.failed:
            message += f" {result.failed} failed; they're listed below and can be retried."
        if result.skipped:
            message += f" {result.skipped} skipped (already sent or being sent)."
        flash("warning" if result.failed else "success", message)
        st.rerun()


def _preview_context(row, section, term, adviser, session):
    from app.models.academic_structure import GradeLevel

    grade_level = session.get(GradeLevel, section.grade_level_id)
    school_year = session.get(SchoolYear, term.school_year_id)
    return NoticeContext(
        learner_first=row.learner.first_name,
        learner_last=row.learner.last_name,
        section=section.name,
        grade_level=grade_level.name if grade_level else "",
        term_name=term.name,
        school_year=school_year.name if school_year else "",
        adviser_name=adviser.full_name,
    )


def _sent_record(data) -> None:
    """Every notice sent or attempted for the term (§78.6)."""
    entries = []
    for row in data.rows:
        for n in row.notifications:
            when = notices._naive_utc(n.sent_at or n.created_at)
            entries.append(
                {
                    "Learner": _name(row),
                    "What": "Term card" if n.kind == notices.TERM_CARD else "Concern notice",
                    "How": n.channel.title(),
                    "Status": n.status.title(),
                    "To": n.recipient or "",
                    "When (UTC)": f"{when:%b %d, %Y %H:%M}" if when else "",
                    "Problem": n.error or "",
                }
            )
    st.subheader("Sent record")
    if entries:
        st.dataframe(entries, hide_index=True, width="stretch")
    else:
        st.caption("Nothing has been sent for this term yet.")


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")
    st.title("Parent Notices")
    st.caption(
        "Who gets their term card by email, whose parents are asked to come in, "
        "and who isn't ready yet — per section and term."
    )
    st.caption(
        "Term cards are emailed from here. Concern notices (email, text and "
        "printed letter) are coming next."
    )
    render_flashes()

    # A School Head sees every section, read-only (the sent record, §78.6).
    adviser_scoped = not current_user.has_role("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD")

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
        # the writes, which every form below relies on. A School Head who
        # also advises edits only their own section.
        if adviser_scoped and not is_advised_by(section, str(current_user.id)):
            st.error("You can only manage notices for a section you advise.")
            return
        may_write = not current_user.is_read_only() and (
            current_user.has_role("SUPER_ADMIN", "REGISTRAR")
            or is_advised_by(section, str(current_user.id))
        )

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
        _email_panel(session, section, term, data, current_user, may_write=may_write)
        st.divider()
        _sent_record(data)
        if not may_write:
            return
        st.divider()
        _section_meeting_form(session, section, term, data, current_user)
        _learner_meeting_form(session, section, term, data, current_user)
        st.divider()
        _override_form(session, section, term, data, current_user)
