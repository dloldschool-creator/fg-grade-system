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

Writes (`set_override`, `clear_override`, `set_meeting`, `clear_meeting`)
add to the caller's session with their audit entries and leave the commit
to the caller, so a refused commit rolls both back together.
"""

from dataclasses import dataclass, field
from datetime import date, time

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
)
from app.models.rbac import User
from app.notice_rules import (
    LearnerTermFigures,
    NoticeGroup,
    NoticeThresholds,
    classify,
    effective_group,
)
from app.roster_order import learner_sort_key


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
    override_decision: NoticeGroup | None = None
    override_reason: str | None = None
    override_by: str | None = None
    meeting: Meeting | None = None

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


def load_section_notices(session, section, term: Term) -> SectionNotices:
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
    result = SectionNotices(term=term, thresholds=thresholds, class_day_count=len(all_days))

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
    if day_by_id:
        for enrollment_id, calendar_date_id, status in (
            session.query(
                AttendanceRecord.enrollment_id,
                AttendanceRecord.calendar_date_id,
                AttendanceRecord.status,
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
            computed, reasons = NoticeGroup.NOT_READY, (
                "no parent-notice policy for this school year",
            )
        else:
            classification = classify(figures, thresholds)
            computed, reasons = classification.group, classification.reasons
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
                override_decision=override_decision,
                override_reason=override.reason if override else None,
                override_by=setters.get(override.set_by_user_id) if override else None,
                meeting=learner_meetings.get(enrollment.id) or result.section_meeting,
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
