"""Parent notices for one section and term (spec §78): who is in which
group, the adviser's overrides, and the meeting schedule.

**Query shape.** `load_section_notices` makes a fixed number of round
trips whatever the roster size — about a dozen — and nothing per learner.
The attendance part is the batched shape `analytics_service.attendance_risk`
already uses: movements and records each in one query, windows built by
the engine's own `compute_active_window`, counts by its own
`summarize_attendance`. `active_window_for` is deliberately not used; it
costs two round trips per learner.

**Nothing official is recomputed here.** The failing count and grade
completeness are read from `term_grade_summaries` exactly as
`grading_service` stored them, against the passing mark in force when the
grades were saved. The decision itself is `app.notice_rules.classify`.

**When Concern parents can be contacted** (§78.5, amended 2026-10-04).
A learner in Concern on attendance can be contacted at once, encoding
open or not: an absence is not graded and can't be undone by later
encoding. Anyone else in Concern — a failing grade, or an adviser's
override — waits until the term's encoding closes, since the grade can
still change. `NoticeRow.concern_held` is that one rule, and the email,
SMS and letter paths all read it, so the page can't offer what the
sender would refuse.

Writes (`set_override`, `clear_override`, `set_meeting`, `clear_meeting`)
add to the caller's session with their audit entries and leave the commit
to the caller, so a refused commit rolls both back together.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from app import audit_service
from app.attendance_engine import Movement, compute_active_window, summarize_attendance
from app.guardian_contact import display_mobile
from app.models.attendance import AcademicCalendarDate, AttendanceRecord
from app.models.enums import CompletionStatus
from app.models.grades import TermGradeSummary
from app.models.learners import Enrollment, Learner, LearnerMovement
from app.models.organization import SchoolYear, Term
from app.models.parent_notices import (
    ParentMeetingSchedule,
    ParentNoticeOverride,
    ParentNoticePolicy,
    ParentNotification,
)
from app.models.rbac import User
from app.notice_rules import (
    GROUP_LABELS,
    LearnerTermFigures,
    NoticeGroup,
    NoticeThresholds,
    classify,
    effective_group,
)
from app.roster_order import learner_sort_key


TERM_CARD = "TERM_CARD"
CONCERN = "CONCERN"
# A PENDING row older than this belongs to a send that never finished.
STUCK_AFTER = timedelta(minutes=10)


def _naive_utc(value: datetime | None) -> datetime | None:
    """Timestamps here are written both tz-aware (from Python) and naive
    (from Postgres `now()` into a column without a time zone), all UTC.
    Normalised so they compare — the same trap Insights' `_is_stale` met."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class Meeting:
    meeting_date: date
    meeting_time: time
    is_learner_specific: bool = False


@dataclass
class NoticeRow:
    enrollment_id: object
    learner: Learner
    figures: LearnerTermFigures
    computed: NoticeGroup
    reasons: tuple[str, ...]
    group: NoticeGroup
    # Still missing (grades, attendance days). Non-empty for Not ready, and
    # for Concern reached on attendance before the record was complete.
    incomplete: tuple[str, ...] = ()
    attendance_concern: bool = False
    # The term's grade encoding was open when this was loaded.
    encoding_open: bool = False
    override_decision: NoticeGroup | None = None
    override_reason: str | None = None
    override_by: str | None = None
    meeting: Meeting | None = None
    # When the grades or attendance this term's card rests on last changed.
    basis_at: datetime | None = None
    # This learner's sent record for the term, newest first.
    notifications: list = field(default_factory=list)

    def live_email(self, kind: str):
        """The PENDING or SENT email of `kind`, if any — at most one, by the
        table's unique index."""
        return next(
            (
                n for n in self.notifications
                if n.kind == kind and n.channel == "EMAIL" and n.status in ("PENDING", "SENT")
            ),
            None,
        )

    @property
    def concern_held(self) -> str | None:
        """Why this Concern learner's parent can't be contacted yet, or None.
        Only an attendance concern goes out while encoding is open (§78.5)."""
        if self.encoding_open and not self.attendance_concern:
            return "waits until grade encoding closes"
        return None

    @property
    def meeting_at(self) -> datetime | None:
        """The meeting as one naive datetime, or None. Stored as `basis_at`
        on concern notices, so a later change of schedule is noticed."""
        if self.meeting is None:
            return None
        return datetime.combine(self.meeting.meeting_date, self.meeting.meeting_time)

    def concern_is_outdated(self) -> bool:
        """A concern email went out announcing a different meeting (or none)
        than the one now set (§78.5). Re-sending is then offered."""
        sent = self.live_email(CONCERN)
        return bool(sent and sent.status == "SENT" and sent.basis_at != self.meeting_at)

    def card_is_outdated(self) -> bool:
        """Sent, and the grades or attendance under it changed since (§78.4).
        Unknowable without both timestamps, so that reads as not outdated."""
        sent = self.live_email(TERM_CARD)
        return bool(
            sent and sent.status == "SENT" and sent.basis_at and self.basis_at
            and self.basis_at > sent.basis_at
        )

    @property
    def has_email(self) -> bool:
        return bool(self.learner.guardian_email)

    @property
    def has_mobile(self) -> bool:
        return bool(self.learner.guardian_mobile)

    @property
    def mobile_display(self) -> str:
        return display_mobile(self.learner.guardian_mobile)


@dataclass
class SectionNotices:
    term: Term
    thresholds: NoticeThresholds | None
    class_day_count: int
    rows: list[NoticeRow] = field(default_factory=list)
    # Enrolled in the section but no longer on the roll by the term's last
    # class day (transferred out, dropped). Counted, not classified.
    left_before_term_end: int = 0
    section_meeting: Meeting | None = None
    # The term's grade encoding was open when this was loaded. Read by the
    # page for the term-card gate and copied onto each row for the
    # row-based senders, so both gates read one value.
    encoding_open: bool = False

    def in_group(self, group: NoticeGroup) -> list[NoticeRow]:
        return [row for row in self.rows if row.group is group]


def resolve_thresholds(session, school_year_id) -> NoticeThresholds | None:
    """The highest-versioned policy for the school year, or None when the
    year has none — the page says so rather than inventing a default."""
    policy = (
        session.query(ParentNoticePolicy)
        .filter_by(school_year_id=school_year_id)
        .order_by(ParentNoticePolicy.version.desc())
        .first()
    )
    if policy is None:
        return None
    return NoticeThresholds(
        max_absences=policy.max_absences,
        max_lates=policy.max_lates,
        max_cuttings=policy.max_cuttings,
        version=policy.version,
    )


def load_section_notices(session, section, term: Term, today: date | None = None) -> SectionNotices:
    if today is None:
        from app.display_time import SCHOOL_TZ

        today = datetime.now(SCHOOL_TZ).date()
    encoding_open = term_encoding_open(term, today)
    thresholds = resolve_thresholds(session, term.school_year_id)

    class_days = (
        session.query(AcademicCalendarDate.id, AcademicCalendarDate.calendar_date)
        .filter(
            AcademicCalendarDate.term_id == term.id,
            AcademicCalendarDate.is_default_class_day.is_(True),
        )
        .order_by(AcademicCalendarDate.calendar_date)
        .all()
    )
    day_by_id = {day_id: day for day_id, day in class_days}
    all_days = [day for _, day in class_days]
    result = SectionNotices(
        term=term, thresholds=thresholds, class_day_count=len(all_days),
        encoding_open=encoding_open,
    )

    enrollments = (
        session.query(Enrollment)
        .filter_by(section_id=section.id, school_year_id=term.school_year_id)
        .all()
    )
    if not enrollments:
        return result
    enrollment_ids = [e.id for e in enrollments]

    learners = {
        learner.id: learner
        for learner in session.query(Learner)
        .filter(Learner.id.in_({e.learner_id for e in enrollments}))
        .all()
    }
    school_year = session.get(SchoolYear, term.school_year_id)

    movements: dict = {}
    for movement in (
        session.query(LearnerMovement).filter(LearnerMovement.enrollment_id.in_(enrollment_ids)).all()
    ):
        movements.setdefault(movement.enrollment_id, []).append(movement)

    # Three columns, not ORM objects — a term is ~60 class days times the
    # roster, and hydrating that many instances is the expensive part.
    statuses: dict = {}
    attendance_changed: dict = {}
    if day_by_id:
        for enrollment_id, calendar_date_id, status, updated_at in (
            session.query(
                AttendanceRecord.enrollment_id,
                AttendanceRecord.calendar_date_id,
                AttendanceRecord.status,
                AttendanceRecord.updated_at,
            )
            .filter(
                AttendanceRecord.enrollment_id.in_(enrollment_ids),
                AttendanceRecord.calendar_date_id.in_(list(day_by_id)),
            )
            .all()
        ):
            day = day_by_id.get(calendar_date_id)
            if day is not None:
                statuses.setdefault(enrollment_id, {})[day] = status
            updated_at = _naive_utc(updated_at)
            if updated_at and updated_at > attendance_changed.get(enrollment_id, datetime.min):
                attendance_changed[enrollment_id] = updated_at

    summaries = {
        s.enrollment_id: s
        for s in session.query(TermGradeSummary)
        .filter(
            TermGradeSummary.enrollment_id.in_(enrollment_ids),
            TermGradeSummary.term_id == term.id,
        )
        .all()
    }
    overrides = {
        o.enrollment_id: o
        for o in session.query(ParentNoticeOverride)
        .filter(
            ParentNoticeOverride.enrollment_id.in_(enrollment_ids),
            ParentNoticeOverride.term_id == term.id,
        )
        .all()
    }
    notifications: dict = {}
    for notification in (
        session.query(ParentNotification)
        .filter(
            ParentNotification.enrollment_id.in_(enrollment_ids),
            ParentNotification.term_id == term.id,
        )
        .order_by(ParentNotification.created_at.desc())
        .all()
    ):
        notifications.setdefault(notification.enrollment_id, []).append(notification)
    meetings = (
        session.query(ParentMeetingSchedule)
        .filter_by(section_id=section.id, term_id=term.id)
        .all()
    )
    section_meeting_row = next((m for m in meetings if m.enrollment_id is None), None)
    if section_meeting_row is not None:
        result.section_meeting = Meeting(
            section_meeting_row.meeting_date, section_meeting_row.meeting_time
        )
    learner_meetings = {
        m.enrollment_id: Meeting(m.meeting_date, m.meeting_time, is_learner_specific=True)
        for m in meetings
        if m.enrollment_id is not None
    }
    setter_ids = {o.set_by_user_id for o in overrides.values() if o.set_by_user_id}
    setters = (
        {u.id: u.full_name for u in session.query(User).filter(User.id.in_(setter_ids)).all()}
        if setter_ids
        else {}
    )

    last_class_day = all_days[-1] if all_days else None
    for enrollment in enrollments:
        learner = learners.get(enrollment.learner_id)
        if learner is None:
            continue
        window = compute_active_window(
            [Movement(m.movement_type, m.effective_date) for m in movements.get(enrollment.id, [])],
            default_start=school_year.start_date if school_year else None,
        )
        # On the roll on the term's last class day (§78.2). With no class
        # days on the calendar there is no such day, so everyone stays and
        # is reported Not ready for that reason.
        if last_class_day is not None and not window.contains(last_class_day):
            result.left_before_term_end += 1
            continue
        attendance = summarize_attendance(all_days, window, statuses.get(enrollment.id, {}))
        summary = summaries.get(enrollment.id)
        figures = LearnerTermFigures(
            summary_found=summary is not None,
            grades_complete=bool(summary and summary.completion_status == CompletionStatus.COMPLETE),
            failed_count=summary.failed_subject_count if summary else None,
            eligible_days=attendance.eligible_days,
            unencoded_days=attendance.unencoded_days,
            absences=attendance.days_absent,
            lates=attendance.late_count,
            cuttings=attendance.cutting_count,
        )
        if thresholds is None:
            no_policy = ("no parent-notice policy for this school year",)
            computed, reasons, incomplete, on_attendance = (
                NoticeGroup.NOT_READY, no_policy, no_policy, False
            )
        else:
            classification = classify(figures, thresholds)
            computed, reasons = classification.group, classification.reasons
            incomplete = classification.incomplete
            on_attendance = classification.attendance_concern
        override = overrides.get(enrollment.id)
        override_decision = NoticeGroup(override.decision) if override else None
        result.rows.append(
            NoticeRow(
                enrollment_id=enrollment.id,
                learner=learner,
                figures=figures,
                computed=computed,
                reasons=reasons,
                group=effective_group(computed, override_decision),
                incomplete=incomplete,
                attendance_concern=on_attendance,
                encoding_open=encoding_open,
                override_decision=override_decision,
                override_reason=override.reason if override else None,
                override_by=setters.get(override.set_by_user_id) if override else None,
                meeting=learner_meetings.get(enrollment.id) or result.section_meeting,
                basis_at=max(
                    (
                        t for t in (
                            _naive_utc(summary.computed_at) if summary else None,
                            attendance_changed.get(enrollment.id),
                        )
                        if t is not None
                    ),
                    default=None,
                ),
                notifications=notifications.get(enrollment.id, []),
            )
        )
    result.rows.sort(key=lambda row: learner_sort_key(row.learner))
    return result


# --- Writes -----------------------------------------------------------------


def set_override(session, *, enrollment_id, term_id, decision: NoticeGroup, reason: str, user_id):
    """Creates or replaces the learner's override for the term. The reason
    is required (§78.3) — refused here as well as by the table's CHECK, so
    the page can say so instead of hitting a constraint."""
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A reason is required.")
    if decision not in (NoticeGroup.RELEASE, NoticeGroup.CONCERN):
        raise ValueError("An override can only be Release or Concern.")
    row = (
        session.query(ParentNoticeOverride)
        .filter_by(enrollment_id=enrollment_id, term_id=term_id)
        .one_or_none()
    )
    previous = {"decision": row.decision, "reason": row.reason} if row else None
    if row is None:
        row = ParentNoticeOverride(enrollment_id=enrollment_id, term_id=term_id)
        session.add(row)
    else:
        row.version += 1
    row.decision = decision.value
    row.reason = reason
    row.set_by_user_id = user_id
    audit_service.record(
        session,
        action=audit_service.PARENT_NOTICE_OVERRIDDEN,
        object_type="enrollments",
        object_id=enrollment_id,
        user_id=user_id,
        previous=previous,
        new={"term_id": term_id, "decision": decision.value},
        reason=reason,
    )


def clear_override(session, *, enrollment_id, term_id, user_id) -> bool:
    row = (
        session.query(ParentNoticeOverride)
        .filter_by(enrollment_id=enrollment_id, term_id=term_id)
        .one_or_none()
    )
    if row is None:
        return False
    audit_service.record(
        session,
        action=audit_service.PARENT_NOTICE_OVERRIDE_CLEARED,
        object_type="enrollments",
        object_id=enrollment_id,
        user_id=user_id,
        previous={"term_id": term_id, "decision": row.decision, "reason": row.reason},
    )
    session.delete(row)
    return True


def set_meeting(session, *, section_id, term_id, meeting_date: date, meeting_time: time,
                user_id, enrollment_id=None) -> None:
    """The section's meeting for the term, or one learner's own when
    `enrollment_id` is given."""
    row = (
        session.query(ParentMeetingSchedule)
        .filter_by(section_id=section_id, term_id=term_id, enrollment_id=enrollment_id)
        .one_or_none()
    )
    previous = (
        {"meeting_date": row.meeting_date, "meeting_time": row.meeting_time} if row else None
    )
    if row is None:
        row = ParentMeetingSchedule(
            section_id=section_id, term_id=term_id, enrollment_id=enrollment_id
        )
        session.add(row)
    else:
        row.version += 1
    row.meeting_date = meeting_date
    row.meeting_time = meeting_time
    row.set_by_user_id = user_id
    audit_service.record(
        session,
        action=audit_service.PARENT_MEETING_SCHEDULED,
        object_type="enrollments" if enrollment_id else "sections",
        object_id=enrollment_id or section_id,
        user_id=user_id,
        previous=previous,
        new={"term_id": term_id, "meeting_date": meeting_date, "meeting_time": meeting_time},
    )


def clear_meeting(session, *, section_id, term_id, user_id, enrollment_id=None) -> bool:
    row = (
        session.query(ParentMeetingSchedule)
        .filter_by(section_id=section_id, term_id=term_id, enrollment_id=enrollment_id)
        .one_or_none()
    )
    if row is None:
        return False
    audit_service.record(
        session,
        action=audit_service.PARENT_MEETING_SCHEDULED,
        object_type="enrollments" if enrollment_id else "sections",
        object_id=enrollment_id or section_id,
        user_id=user_id,
        previous={"term_id": term_id, "meeting_date": row.meeting_date,
                  "meeting_time": row.meeting_time},
        new=None,
    )
    session.delete(row)
    return True


# --- Emailing term cards (§78.4) --------------------------------------------


@dataclass(frozen=True)
class EmailStatus:
    """Where one learner stands for one kind of email."""

    sendable: bool
    label: str
    is_resend: bool = False


# Kept under its step-3 name: the page and tests read it for term cards.
CardEmailStatus = EmailStatus

_GROUP_FOR_KIND = {TERM_CARD: NoticeGroup.RELEASE, CONCERN: NoticeGroup.CONCERN}


def email_status(row: NoticeRow, kind: str, now: datetime | None = None) -> EmailStatus:
    """Read by both the page (to show it) and the sender (to obey it), so
    what the table says is exactly what Send will do. A term card goes to
    Release, a concern email to Concern (§78.4, §78.5)."""
    group = _GROUP_FOR_KIND[kind]
    if row.group is not group:
        return EmailStatus(False, f"not in {GROUP_LABELS[group]}")
    live = row.live_email(kind)
    if live is not None and live.status == "PENDING":
        if _is_stuck(live, now or _utcnow()):
            return EmailStatus(False, "interrupted — may have gone out")
        return EmailStatus(False, "sending…")
    # Held only blocks a *new* send; one already sent still says it was.
    held = kind == CONCERN and row.concern_held
    if held and live is None:
        return EmailStatus(False, held)
    if held and row.concern_is_outdated():
        when = _naive_utc(live.sent_at)
        return EmailStatus(
            False, f"sent{f' {when:%b %d}' if when else ''}; meeting changed, re-send {held}"
        )
    # An override can put an incomplete record in Release; its card would
    # carry blanks, so it waits for the record (§78.2, rule 2).
    if kind == TERM_CARD and row.incomplete and (live is None or row.card_is_outdated()):
        return EmailStatus(False, "record incomplete: " + "; ".join(row.incomplete))
    if not row.learner.notices_consent:
        return EmailStatus(False, "no consent on file")
    if not row.learner.guardian_email:
        return EmailStatus(False, "no parent email on file")
    if live is not None:
        if kind == TERM_CARD and row.card_is_outdated():
            return EmailStatus(True, "outdated — grades or attendance changed since", True)
        if kind == CONCERN and row.concern_is_outdated():
            return EmailStatus(True, "meeting changed since it was emailed", True)
        when = _naive_utc(live.sent_at)
        return EmailStatus(False, f"sent{f' {when:%b %d}' if when else ''} to {live.recipient}")
    failed = next(
        (
            n for n in row.notifications
            if n.kind == kind and n.channel == "EMAIL" and n.status == "FAILED"
        ),
        None,
    )
    if failed is not None:
        return EmailStatus(True, f"ready — last try failed: {failed.error or 'unknown error'}")
    return EmailStatus(True, "ready to email")


def card_email_status(row: NoticeRow, now: datetime | None = None) -> EmailStatus:
    return email_status(row, TERM_CARD, now)


def concern_email_status(row: NoticeRow, now: datetime | None = None) -> EmailStatus:
    return email_status(row, CONCERN, now)


def _is_stuck(notification, now: datetime) -> bool:
    created = _naive_utc(notification.created_at)
    return bool(created) and now - created > STUCK_AFTER


def term_encoding_open(term: Term, today: date) -> bool:
    from app.encoding_window import encoding_is_open
    from app.models.enums import GradeEncodingStatus

    return encoding_is_open(
        term.grade_encoding_status == GradeEncodingStatus.OPEN, term.submission_deadline, today
    )


@dataclass
class SendResult:
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list = field(default_factory=list)
    stopped: str | None = None


@dataclass(frozen=True)
class _Sender:
    """The adviser as primitives: committing expires the ORM `User`."""

    full_name: str
    email: str | None


@dataclass(frozen=True)
class _Prepared:
    """Everything one email needs, captured before the first commit —
    committing expires the ORM objects, and re-reading each learner after
    it would be a round trip per email."""

    kind: str
    enrollment_id: object
    to: str
    ctx: object
    basis_at: datetime | None = None
    supersede_id: object | None = None
    # Term card only.
    password: str | None = None
    card: object | None = None
    # Concern only: (date, time) or None.
    meeting: tuple | None = None


def notice_context(session, section, term, row, adviser):
    """The one way a page or sender builds a learner's `NoticeContext`, so
    a preview always matches what is sent."""
    from app.models.academic_structure import GradeLevel

    grade_level = session.get(GradeLevel, section.grade_level_id)
    school_year = session.get(SchoolYear, term.school_year_id)
    return _context_for(row.learner, section, term, grade_level, school_year, adviser)


def _context_for(learner, section, term, grade_level, school_year, adviser):
    from app.notice_messages import NoticeContext

    return NoticeContext(
        learner_first=learner.first_name,
        learner_last=learner.last_name,
        section=section.name,
        grade_level=grade_level.name if grade_level else "",
        term_name=term.name,
        school_year=school_year.name if school_year else "",
        adviser_name=adviser.full_name,
    )


def _prepare(session, section, term, rows, *, adviser, kind=TERM_CARD) -> list:
    from app.models.academic_structure import GradeLevel
    from app.models.organization import School
    from app.notice_messages import birthdate_password
    from app.report_card import load_report_context, term_card_data

    grade_level = session.get(GradeLevel, section.grade_level_id)
    school_year = session.get(SchoolYear, term.school_year_id)

    if kind == CONCERN:
        prepared = []
        for row in rows:
            live = row.live_email(CONCERN)
            prepared.append(
                _Prepared(
                    kind=CONCERN,
                    enrollment_id=row.enrollment_id,
                    to=row.learner.guardian_email,
                    ctx=_context_for(row.learner, section, term, grade_level, school_year, adviser),
                    # The meeting announced, so a later change is noticed.
                    basis_at=row.meeting_at,
                    supersede_id=live.id if live is not None and live.status == "SENT" else None,
                    meeting=(
                        (row.meeting.meeting_date, row.meeting.meeting_time) if row.meeting else None
                    ),
                )
            )
        return prepared

    enrollments = (
        session.query(Enrollment).filter(Enrollment.id.in_([r.enrollment_id for r in rows])).all()
    )
    by_id = {e.id: e for e in enrollments}
    context = load_report_context(session, enrollments)
    summaries = {
        s.enrollment_id: s
        for s in session.query(TermGradeSummary)
        .filter(
            TermGradeSummary.enrollment_id.in_(list(by_id)),
            TermGradeSummary.term_id == term.id,
        )
        .all()
    }
    school = session.query(School).one_or_none()

    prepared = []
    for row in rows:
        enrollment = by_id.get(row.enrollment_id)
        if enrollment is None:
            continue
        learner = row.learner
        live = row.live_email(TERM_CARD)
        prepared.append(
            _Prepared(
                kind=TERM_CARD,
                enrollment_id=row.enrollment_id,
                to=learner.guardian_email,
                ctx=_context_for(learner, section, term, grade_level, school_year, adviser),
                basis_at=row.basis_at,
                supersede_id=live.id if live is not None and live.status == "SENT" else None,
                password=birthdate_password(learner.birthdate),
                card=term_card_data(
                    session, enrollment, learner, school=school, term=term,
                    grade_level=grade_level, section=section, adviser=adviser,
                    context=context, summary=summaries.get(enrollment.id),
                ),
            )
        )
    return prepared


def _message_for(settings, item: _Prepared, sender: _Sender, *, to=None, test=False):
    from app.notice_mailer import build_message
    from app.notice_messages import (
        attachment_filename,
        concern_email,
        sender_display_name,
        term_card_email,
    )

    attachment = attachment_name = None
    if item.kind == TERM_CARD:
        from app.term_card import generate_term_cards

        subject, body = term_card_email(item.ctx)
        attachment = generate_term_cards([item.card], password=item.password)
        attachment_name = attachment_filename(item.ctx)
    else:
        subject, body = concern_email(item.ctx, item.meeting)
    if test:
        subject = f"[TEST, not sent to the parent] {subject}"
        body = f"This is a test. The real email goes to {item.to}.\n\n{body}"
    return build_message(
        settings,
        to=to or item.to,
        sender_name=sender_display_name(sender.full_name),
        reply_to=sender.email,
        subject=subject,
        body=body,
        attachment=attachment,
        attachment_name=attachment_name,
    )


def send_emails(
    session, section, term, rows, *, kind, user_id, adviser, settings,
    progress=None, mailer_cls=None,
) -> SendResult:
    """Emails each sendable row's parent, one at a time — the term card to
    Release (§78.4) or the concern notice to Concern (§78.5).

    For each learner: commit a PENDING claim (the unique index refuses a
    second one, so a concurrent or repeated send skips that learner), build
    the message, send, then commit SENT or FAILED. **Commits per message**:
    an interrupted batch resumes where it stopped, and a crash can never
    leave a parent emailed twice. One PDF in memory at a time.

    Each row is re-checked with `email_status` here rather than trusted
    from the page that drew the button.
    """
    from sqlalchemy.exc import IntegrityError

    from app.notice_mailer import MailAuthError, Mailer

    mailer_cls = mailer_cls or Mailer
    result = SendResult()
    targets = [r for r in rows if email_status(r, kind).sendable]
    result.skipped = len(rows) - len(targets)
    if not targets:
        return result

    prepared = _prepare(session, section, term, targets, adviser=adviser, kind=kind)
    sender = _Sender(full_name=adviser.full_name, email=adviser.email)
    section_id, term_id = section.id, term.id
    with mailer_cls(settings) as mailer:
        for index, item in enumerate(prepared, start=1):
            if item.supersede_id is not None:
                old = session.get(ParentNotification, item.supersede_id)
                if old is not None and old.status == "SENT":
                    old.status = "SUPERSEDED"
            claim = ParentNotification(
                enrollment_id=item.enrollment_id, term_id=term_id, kind=kind,
                channel="EMAIL", status="PENDING", recipient=item.to,
                basis_at=item.basis_at, sent_by_user_id=user_id,
            )
            session.add(claim)
            try:
                session.commit()
            except IntegrityError:
                # Someone else is sending, or has sent, this learner's email.
                session.rollback()
                result.skipped += 1
                continue

            try:
                mailer.send(_message_for(settings, item, sender))
            except MailAuthError as exc:
                claim.status, claim.error = "FAILED", str(exc)[:500]
                session.commit()
                result.failed += 1
                result.stopped = str(exc)
                break
            except Exception as exc:  # noqa: BLE001 - recorded against the learner
                claim.status, claim.error = "FAILED", f"{type(exc).__name__}: {exc}"[:500]
                session.commit()
                result.failed += 1
                result.errors.append((item.ctx.learner_upper, claim.error))
            else:
                claim.status, claim.sent_at = "SENT", _utcnow()
                session.commit()
                result.sent += 1
            if progress:
                progress(index, len(prepared))

    audit_service.record(
        session,
        action=audit_service.PARENT_NOTICES_SENT,
        object_type="sections",
        object_id=section_id,
        user_id=user_id,
        new={
            "term_id": term_id, "kind": kind, "channel": "EMAIL",
            "sent": result.sent, "failed": result.failed, "skipped": result.skipped,
        },
    )
    session.commit()
    return result


def send_term_cards(session, section, term, rows, **kwargs) -> SendResult:
    return send_emails(session, section, term, rows, kind=TERM_CARD, **kwargs)


def send_concern_emails(session, section, term, rows, **kwargs) -> SendResult:
    return send_emails(session, section, term, rows, kind=CONCERN, **kwargs)


def send_test_email(session, section, term, row, *, kind=TERM_CARD, adviser, to, settings,
                    mailer_cls=None):
    """Sends one learner's email to `to` (the person pressing the button)
    instead of the parent, marked as a test. Not recorded as a notice:
    nothing reached a parent. For checking the account and the wording."""
    from app.notice_mailer import Mailer

    mailer_cls = mailer_cls or Mailer
    (item,) = _prepare(session, section, term, [row], adviser=adviser, kind=kind)
    sender = _Sender(full_name=adviser.full_name, email=adviser.email)
    with mailer_cls(settings) as mailer:
        mailer.send(_message_for(settings, item, sender, to=to, test=True))


def send_test_card(session, section, term, row, **kwargs):
    return send_test_email(session, section, term, row, kind=TERM_CARD, **kwargs)


def release_stuck_sends(session, rows, *, term_id, user_id, kind=TERM_CARD) -> int:
    """Marks interrupted sends (PENDING past `STUCK_AFTER`) FAILED so they
    can be retried. The page warns first: the email may already have gone."""
    now = _utcnow()
    released = 0
    for row in rows:
        live = row.live_email(kind)
        if live is not None and live.status == "PENDING" and _is_stuck(live, now):
            live.status = "FAILED"
            live.error = "interrupted before it finished; it may have been sent"
            released += 1
    if released:
        audit_service.record(
            session,
            action=audit_service.PARENT_NOTICES_SENT,
            object_type="terms",
            object_id=term_id,
            user_id=user_id,
            new={"kind": kind, "released_interrupted_sends": released},
        )
    return released


# --- Text messages and letters (§78.5) ----------------------------------------


def contactable_concern(rows) -> list[NoticeRow]:
    """Concern learners whose parents may be contacted now (§78.5)."""
    return [r for r in rows if r.group is NoticeGroup.CONCERN and not r.concern_held]


def sms_status(row: NoticeRow) -> str | None:
    """Why this Concern learner's parent can't be texted, or None if they can."""
    if row.group is not NoticeGroup.CONCERN:
        return f"not in {GROUP_LABELS[NoticeGroup.CONCERN]}"
    if row.concern_held:
        return row.concern_held
    if not row.learner.notices_consent:
        return "no consent on file"
    if not row.learner.guardian_mobile:
        return "no parent mobile on file"
    return None


def last_texted(row: NoticeRow):
    return next(
        (n for n in row.notifications if n.kind == CONCERN and n.channel == "SMS"), None
    )


def record_sms(session, row: NoticeRow, *, term_id, user_id) -> None:
    """The adviser marks a text as sent from their own phone. The app never
    sees the SMS itself, so this records the adviser's word that they sent
    it, not that it was delivered (§78.5). Repeats are allowed — a second
    text is a second row.

    Re-checks eligibility itself rather than trusting the page, as the
    email sender does: no text is recorded to a parent outside Concern or
    without consent and a mobile on file.
    """
    blocked = sms_status(row)
    if blocked is not None:
        raise ValueError(f"Can't record a text: {blocked}.")
    session.add(
        ParentNotification(
            enrollment_id=row.enrollment_id, term_id=term_id, kind=CONCERN,
            channel="SMS", status="SENT", recipient=row.learner.guardian_mobile,
            basis_at=row.meeting_at, sent_at=_utcnow(), sent_by_user_id=user_id,
        )
    )


def sms_message(session, section, term, row: NoticeRow, *, adviser, language="FIL") -> str:
    from app.notice_messages import sms_text

    ctx = notice_context(session, section, term, row, adviser)
    meeting = (row.meeting.meeting_date, row.meeting.meeting_time) if row.meeting else None
    return sms_text(ctx, meeting, language=language)


def build_concern_letters(session, section, term, rows, *, adviser, user_id, today: date):
    """One letter per Concern row that has a meeting, as one PDF, and a
    LETTER row in the sent record for each. Letters need no consent — they
    go home on paper — but they do need a meeting, since they name one.

    Returns (pdf_bytes, printed_rows). Built only on request (a button),
    never on render.

    A letter is recorded **once per learner per meeting**: building the
    same letters again (a reprint, a jammed printer) adds no rows, and a
    changed meeting — a genuinely different letter — is recorded anew.
    """
    from app.concern_letter import LetterData, generate_concern_letters
    from app.models.academic_structure import GradeLevel
    from app.models.organization import School
    from app.notice_messages import date_plain_en, letter_paragraphs

    printable = [r for r in contactable_concern(rows) if r.meeting]
    if not printable:
        return None, []
    school = session.query(School).one_or_none()
    grade_level = session.get(GradeLevel, section.grade_level_id)
    school_year = session.get(SchoolYear, term.school_year_id)
    letters = []
    for row in printable:
        ctx = _context_for(row.learner, section, term, grade_level, school_year, adviser)
        letters.append(
            LetterData(
                school_name=school.school_name if school else "",
                school_address=school.address if school else "",
                letter_date=date_plain_en(today),
                words=letter_paragraphs(
                    ctx, (row.meeting.meeting_date, row.meeting.meeting_time)
                ),
            )
        )
        already = any(
            n.kind == CONCERN and n.channel == "LETTER" and n.basis_at == row.meeting_at
            for n in row.notifications
        )
        if not already:
            session.add(
                ParentNotification(
                    enrollment_id=row.enrollment_id, term_id=term.id, kind=CONCERN,
                    channel="LETTER", status="SENT", recipient=None,
                    basis_at=row.meeting_at, sent_at=_utcnow(), sent_by_user_id=user_id,
                )
            )
    return generate_concern_letters(letters), printable
