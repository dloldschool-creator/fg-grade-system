"""Parent Notices — spec §78: who gets the term card by email, whose
parents are asked to come in, and who isn't ready yet, per section and term.

Release learners' term cards are emailed (§78.4); Concern learners'
parents are emailed, texted from the adviser's own phone, or sent a printed
letter (§78.5). Both kinds of email go through
`parent_notice_service.send_emails`, which owns the double-send guard and
the per-message commits; this page only decides whether to offer the
button and reports the result.

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
    _forget_stale,
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
from app.notice_messages import concern_email, sms_link, term_card_email
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


def _other_channels(row) -> str:
    """Texts marked and letters printed for this learner, newest first."""
    parts = []
    for channel, word in (("SMS", "texted"), ("LETTER", "letter printed")):
        latest = next(
            (n for n in row.notifications if n.kind == notices.CONCERN and n.channel == channel),
            None,
        )
        if latest is not None:
            when = notices._naive_utc(latest.sent_at)
            parts.append(f"{word} {when:%b %d}" if when else word)
    return ", ".join(parts)


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
            **({"Still missing": "; ".join(row.incomplete)} if concern else {}),
            "Override": (
                f"{GROUP_LABELS[row.override_decision]} — {row.override_reason}"
                if row.override_decision
                else ""
            ),
            "Parent contact": _contact_text(row),
        }
        if concern:
            entry["Meeting"] = _meeting_text(row.meeting)
            entry["Email"] = notices.concern_email_status(row).label
            entry["Text / letter"] = _other_channels(row)
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
    # The list moves under the stored pick (a grade lands, a learner goes
    # Not ready); a stored id no longer offered would raise.
    _forget_stale(f"{form}_learner", by_id)
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


def _learner_meeting_form(session, section, term, concern, current_user) -> None:
    """One learner's own meeting time; `concern` is the non-empty Concern
    group, which the caller has already checked."""
    with st.expander("Give one learner a different meeting time"):
        by_id = {row.enrollment_id: row for row in concern}
        form = f"notice_learner_meeting_{section.id}_{term.id}"
        # An override can move the stored learner out of Concern.
        _forget_stale(f"{form}_learner", by_id)
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


_EMAIL_COPY = {
    notices.TERM_CARD: {
        "group": NoticeGroup.RELEASE,
        "title": "Email the term cards",
        "caption": (
            "Each parent gets one email with their child's term card attached, "
            "protected by the child's birthdate (YYYYMMDD) as the password. Replies "
            "go to the adviser. A card is never emailed twice."
        ),
        "thing": "term card",
        "confirm": "I've checked the Release list above",
        "resend": "Also re-send {n} card(s) whose grades or attendance changed after they were emailed",
    },
    notices.CONCERN: {
        "group": NoticeGroup.CONCERN,
        "title": "Email the concern notices",
        "caption": (
            "A short message in English and Filipino asking the parent to come in, "
            "with the meeting date and time if one is set. It never mentions grades "
            "or attendance. Replies go to the adviser."
        ),
        "thing": "concern notice",
        "confirm": "I've checked the Concern list above",
        "resend": "Also re-send {n} notice(s) whose meeting changed after they were emailed",
    },
}


def _adviser_for(session, section):
    from app.models.rbac import User

    return session.get(User, section.adviser_user_id) if section.adviser_user_id else None


def _email_panel(session, section, term, data, current_user, *, may_write: bool,
                 kind: str = notices.TERM_CARD) -> None:
    """Emailing one group's parents: term cards to Release (step 3) or
    concern notices to Concern (step 4). One panel, one sender, one
    double-send guard for both."""
    from app.notice_mailer import MailAuthError, MailNotConfigured, MailSettings

    copy = _EMAIL_COPY[kind]
    rows = data.in_group(copy["group"])
    if not rows:
        return
    statuses = {row.enrollment_id: notices.email_status(row, kind) for row in rows}
    ready = [row for row in rows if statuses[row.enrollment_id].sendable]
    fresh = [row for row in ready if not statuses[row.enrollment_id].is_resend]
    outdated = [row for row in ready if statuses[row.enrollment_id].is_resend]
    stuck = [row for row in rows if statuses[row.enrollment_id].label.startswith("interrupted")]
    tag = f"{kind}_{section.id}_{term.id}"

    st.subheader(copy["title"])
    st.caption(copy["caption"])
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
    if kind == notices.TERM_CARD and data.encoding_open:
        st.warning(
            f"Grades for {term.name} can still be changed, so who belongs in which "
            "group isn't final yet. Emails open once encoding for the term has closed."
        )
        return
    adviser = _adviser_for(session, section)
    if adviser is None:
        st.warning("This section has no adviser on record, so there's nobody for replies to reach.")
        return

    if stuck:
        st.warning(
            f"{len(stuck)} email(s) were interrupted before they finished and may "
            "already have reached the parent. Check with the parent before sending again."
        )
        if st.button("Allow these to be sent again", key=f"release_stuck_{tag}"):
            notices.release_stuck_sends(
                session, stuck, term_id=term.id, user_id=current_user.id, kind=kind
            )
            try_commit(session, "Interrupted emails can be sent again.")
            st.rerun()

    if not ready:
        st.caption("Nothing to send right now.")
        return

    if fresh:
        first = fresh[0]
        with st.expander("Preview the email"):
            ctx = notices.notice_context(session, section, term, first, adviser)
            if kind == notices.TERM_CARD:
                subject, body = term_card_email(ctx)
            else:
                meeting = (
                    (first.meeting.meeting_date, first.meeting.meeting_time)
                    if first.meeting else None
                )
                subject, body = concern_email(ctx, meeting)
            st.text(f"To: {first.learner.guardian_email}\nSubject: {subject}\n\n{body}")
        if st.button(
            "Send a test to my own email first",
            key=f"test_{tag}",
            help=f"Sends {_name(first)}'s {copy['thing']} to {current_user.email}, marked as a test.",
        ):
            try:
                notices.send_test_email(
                    session, section, term, first, kind=kind, adviser=adviser,
                    to=current_user.email, settings=settings,
                )
                flash("success", f"Test email sent to {current_user.email}.")
            except (MailNotConfigured, MailAuthError, OSError) as exc:
                flash("error", f"The test email couldn't be sent: {exc}")
            st.rerun()

    targets = fresh
    if outdated:
        resend = st.checkbox(copy["resend"].format(n=len(outdated)), key=f"resend_{tag}")
        if resend:
            targets = fresh + outdated
    if not targets:
        return
    confirmed = st.checkbox(copy["confirm"], key=f"confirm_send_{tag}")
    if st.button(
        f"Email {len(targets)} {copy['thing']}(s)", type="primary",
        disabled=not confirmed, key=f"send_{tag}",
    ):
        bar = st.progress(0.0, text="Sending…")
        try:
            result = notices.send_emails(
                session, section, term, targets, kind=kind, user_id=current_user.id,
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
        message = f"Emailed {result.sent} {copy['thing']}(s)."
        if result.failed:
            message += f" {result.failed} failed; they're listed below and can be retried."
        if result.skipped:
            message += f" {result.skipped} skipped (already sent or being sent)."
        flash("warning" if result.failed else "success", message)
        st.rerun()


def _sms_panel(session, section, term, data, current_user) -> None:
    """Texting a Concern parent from the adviser's own phone (§78.5). One
    learner at a time through a picker, so the page draws one set of
    widgets rather than one per learner."""
    concern = data.in_group(NoticeGroup.CONCERN)
    if not concern:
        return
    textable = [row for row in concern if notices.sms_status(row) is None]
    st.subheader("Text a parent")
    st.caption(
        "Texts go from your own phone, on your own load. Pick a learner, open the "
        "message on your phone (or copy it), send it, then mark it as texted here. "
        "The app can't see your phone, so it records that you marked it, not that "
        "it arrived."
    )
    if not textable:
        st.caption(
            "No parent in Concern can be texted yet: each needs a mobile number and "
            "consent on file, and only attendance concerns go out before encoding closes."
        )
        return
    adviser = _adviser_for(session, section)
    if adviser is None:
        st.warning("This section has no adviser on record.")
        return

    by_id = {row.enrollment_id: row for row in textable}
    tag = f"sms_{section.id}_{term.id}"

    def label(enrollment_id):
        row = by_id[enrollment_id]
        texted = notices.last_texted(row)
        done = notices._naive_utc(texted.sent_at) if texted else None
        return f"{_name(row)}" + (f" — texted {done:%b %d}" if done else "")

    # An override, or a consent change, can take the stored learner out
    # of this list; a stored id no longer offered would raise.
    _forget_stale(f"{tag}_learner", by_id)
    choice = st.selectbox("Learner", options=list(by_id), format_func=label, key=f"{tag}_learner")
    language = st.radio(
        "Language", options=["FIL", "EN"],
        format_func=lambda v: "Filipino" if v == "FIL" else "English",
        horizontal=True, key=f"{tag}_language",
    )
    row = by_id[choice]
    text = notices.sms_message(session, section, term, row, adviser=adviser, language=language)
    st.code(text, language=None, wrap_lines=True)
    texts = -(-len(text) // 153) if len(text) > 160 else 1
    st.caption(
        f"To {row.mobile_display} · {len(text)} characters, about {texts} text(s)."
        + ("" if row.meeting else " No meeting is set, so it asks the parent to reply or visit.")
    )
    st.markdown(
        f'<a href="{sms_link(row.learner.guardian_mobile, text)}" target="_self">'
        "📱 Open in my phone's messaging app</a>",
        unsafe_allow_html=True,
    )
    if st.button("Mark as texted", key=f"{tag}_mark"):
        try:
            notices.record_sms(session, row, term_id=term.id, user_id=current_user.id)
        except ValueError as exc:
            flash("error", str(exc))
        else:
            try_commit(session, f"Recorded a text to {_name(row)}'s parent.")
        st.rerun()


def _letters_panel(session, section, term, data, current_user) -> None:
    """Printing concern letters (§78.5): the whole Concern group, or one
    learner, as one PDF — built only when asked. Paper needs no consent,
    but every letter names a meeting, so learners without one are left out."""
    concern = data.in_group(NoticeGroup.CONCERN)
    if not concern:
        return
    st.subheader("Print letters")
    concern = notices.contactable_concern(concern)
    if not concern:
        st.caption("No learner in Concern can be contacted yet.")
        return
    with_meeting = [row for row in concern if row.meeting]
    st.caption(
        "One page per learner, in English and Filipino, signed by the adviser, with a "
        "slip for the parent to sign and return. Each letter names the meeting date "
        "and time."
    )
    if not with_meeting:
        st.warning("Set a parent meeting date and time above first; the letter names it.")
        return
    if len(with_meeting) < len(concern):
        st.caption(f"{len(concern) - len(with_meeting)} learner(s) have no meeting and are left out.")
    adviser = _adviser_for(session, section)
    if adviser is None:
        st.warning("This section has no adviser on record to sign the letters.")
        return

    tag = f"letters_{section.id}_{term.id}"
    by_id = {row.enrollment_id: row for row in with_meeting}
    _forget_stale(f"{tag}_choice", {"ALL", *by_id})
    choice = st.selectbox(
        "Which letters",
        options=["ALL", *by_id],
        format_func=lambda v: (
            f"Everyone in Concern with a meeting ({len(with_meeting)})" if v == "ALL"
            else _name(by_id[v])
        ),
        key=f"{tag}_choice",
    )
    if not st.button("Build letters", key=f"{tag}_build"):
        return
    rows = with_meeting if choice == "ALL" else [by_id[choice]]
    pdf, printed = notices.build_concern_letters(
        session, section, term, rows, adviser=adviser, user_id=current_user.id,
        today=datetime.now(SCHOOL_TZ).date(),
    )
    if not pdf:
        st.warning("Nothing to print.")
        return
    committed = try_commit(session, f"Recorded {len(printed)} printed letter(s).")
    # No rerun follows (the download button has to render in this run), so
    # show the message here rather than one click later at the top.
    render_flashes()
    if not committed:
        return
    stem = f"ConcernLetters_{section.name.replace(' ', '')}_{term.name.replace(' ', '')}"
    st.success(f"{len(printed)} letter(s) ready.")
    st.download_button(
        "Download letters (PDF)", data=pdf, file_name=f"{stem}.pdf",
        mime="application/pdf", type="primary", key=f"{tag}_download",
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
        "Term cards are emailed from here, and parents in Concern are emailed, "
        "texted or sent a printed letter."
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
        if may_write:
            st.divider()
            st.header("Contact the parents in Concern")
            # The meeting comes first: the letter can't be printed without
            # one, and the email and text include it when it's set. The
            # section form shows even with nobody in Concern, so a meeting
            # can be set ahead of time and a stale one can still be removed.
            _section_meeting_form(session, section, term, data, current_user)
        if may_write and concern:
            _learner_meeting_form(session, section, term, concern, current_user)
            # One rule for all three channels (§78.5): attendance concerns can
            # go out at once; a failing grade waits for encoding to close,
            # since it can still change and a text or letter can't be taken
            # back. `concern_held` is that rule; each panel reads it.
            held = [row for row in concern if row.concern_held]
            if held:
                st.info(
                    f"Grades for {term.name} can still be changed, so {len(held)} "
                    "learner(s) in Concern for a grade or by override, not for "
                    "attendance, wait until encoding closes. Attendance concerns "
                    "can be contacted now."
                )
            _email_panel(
                session, section, term, data, current_user,
                may_write=may_write, kind=notices.CONCERN,
            )
            _sms_panel(session, section, term, data, current_user)
            _letters_panel(session, section, term, data, current_user)
        elif may_write:
            st.caption("Nobody is in Concern right now.")
        st.divider()
        _sent_record(data)
        if not may_write:
            return
        st.divider()
        _override_form(session, section, term, data, current_user)
