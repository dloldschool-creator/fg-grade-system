"""What a change made outside the Attendance grid does to a month that is
already FINALIZED (§33, 2026-10-17).

A finalized month's SF2/SF4 is meant to be final, but three changes reach
it without anyone reopening it:

- **An Academic Calendar day flipped** (`academic_calendar._month_editor`).
  School-wide, so every section's finalized month for that date at once.
- **A movement logged** with an effective date in or before a finalized
  month (`enrollment` page, *Log movement*). The learner's window narrows
  and a remark appears.
- **A movement removed** (registrar-only, same page). The window can widen
  again.

Each now does one of two things to every finalized month it touches:

- **Flags it for reprint** (`mark_changed_after_finalize`), the same
  "reprint the SF2/SF4" notice a reopen raises, naming the change. Nothing
  for the adviser to encode; the totals were already right in the system,
  only the paper is stale.
- **Reopens it** (`mark_reopened`) when the change leaves class days
  *nobody has marked*: a make-up class added to the calendar, or a removed
  movement putting a learner back on days they were off. SF2 prints an
  unmarked day blank, which reads as present, so keeping the month
  finalized would print everyone present on a day no one encoded —
  breaking the guarantee `sf2_report.printed_code` relies on, that "a
  finalized SF2 has no silent gaps". The school chose this (2026-10-17).
  The reopen raises the reprint notice by itself.

Nothing here commits: the caller's own change and these writes go in one
transaction, so they succeed or fail together.
"""

import calendar as _calendar
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app import audit_service
from app.attendance_engine import ActiveWindow, Movement, compute_active_window
from app.attendance_service import (
    mark_changed_after_finalize,
    mark_reopened,
    movements_by_enrollment,
    section_year_windows,
    statuses_for_days,
)
from app.enrollment_status import movement_label
from app.models.academic_structure import Section
from app.models.attendance import AcademicCalendarDate, AttendanceMonthStatus
from app.models.enums import FinalizationState
from app.models.learners import Enrollment
from app.models.organization import SchoolYear
from app.text_rules import COMPOSED_REASON_MAX, validate_reason


# A calendar day can reach every section at once. The flash is also a toast,
# so it names a few and counts the rest; the full list is the Dashboard's
# "Reprint SF2/SF4?" column.
NAMED_IN_MESSAGE = 3
# The longest change note kept. A removal of many movements would otherwise
# push the automatic reopen's composed reason past COMPOSED_REASON_MAX.
NOTE_MAX_CHARS = 300


def _some(labels: list[str]) -> str:
    shown = ", ".join(labels[:NAMED_IN_MESSAGE])
    more = len(labels) - NAMED_IN_MESSAGE
    return f"{shown} and {more} more" if more > 0 else shown


@dataclass
class Outcome:
    """Which finalized months a change touched, as "SECTION — Month YYYY"."""

    flagged: list[str] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)

    def message(self) -> str | None:
        """For the page to flash after its commit, or None if nothing was
        finalized in the first place."""
        parts = []
        if self.reopened:
            parts.append(
                f"{len(self.reopened)} month(s) reopened so the new day(s) can be "
                f"marked, then finalized again: {_some(self.reopened)}."
            )
        if self.flagged:
            parts.append(
                f"{len(self.flagged)} month(s) already finalized, so a printed "
                f"SF2/SF4 may now be out of date: {_some(self.flagged)}. Each shows "
                "a reprint reminder until someone confirms the reprint."
            )
        return " ".join(parts) or None


def _clip(text: str, limit: int = NOTE_MAX_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _months_between(a: date, b: date) -> set[date]:
    """First-of-month dates for every month from a's to b's, inclusive."""
    lo, hi = min(a, b).replace(day=1), max(a, b).replace(day=1)
    months = set()
    current = lo
    while current <= hi:
        months.add(current)
        current = date(current.year + current.month // 12, current.month % 12 + 1, 1)
    return months


def months_touched_by_movements(
    before: ActiveWindow,
    after: ActiveWindow,
    effective_dates: list[date],
    year_start: date,
    year_end: date,
) -> set[date]:
    """The months whose SF2 a movement change can alter, as first-of-month
    dates within the school year. Pure, so it is tested on its own.

    A movement's own month always changes (its remark, and the month's
    movement counts on SF2 and SF4). Beyond that, every month between the
    old and the new edge of the learner's window: an exit dated 12
    September takes them off October onwards, and an entry dated 10
    September takes them off every month before it.
    """
    touched = {d.replace(day=1) for d in effective_dates}
    old_start, new_start = before.start or year_start, after.start or year_start
    if old_start != new_start:
        touched |= _months_between(old_start, new_start)
    old_end, new_end = before.end or year_end, after.end or year_end
    if old_end != new_end:
        touched |= _months_between(old_end, new_end)
    return {m for m in touched if year_start.replace(day=1) <= m <= year_end.replace(day=1)}


def _finalized_months(
    session: Session, school_year_id, year_months, section_ids=None
) -> list[AttendanceMonthStatus]:
    """The FINALIZED months among those asked for, locked like `reopen_month`
    locks one: a Reopen or Finalize landing between this read and the commit
    would otherwise be overwritten. One query for any number of sections."""
    if not year_months:
        return []
    query = session.query(AttendanceMonthStatus).filter(
        AttendanceMonthStatus.school_year_id == school_year_id,
        AttendanceMonthStatus.year_month.in_(list(year_months)),
        AttendanceMonthStatus.status == FinalizationState.FINALIZED,
    )
    if section_ids is not None:
        query = query.filter(AttendanceMonthStatus.section_id.in_(list(section_ids)))
    return query.with_for_update().all()


def _settle(
    session: Session,
    statuses: list[AttendanceMonthStatus],
    gaps: set,
    user_id,
    note: str,
) -> Outcome:
    """Reopen the months in `gaps` ((section_id, year_month) pairs), flag the
    rest, and audit each. `note` says what changed, in a sentence."""
    outcome = Outcome()
    if not statuses:
        return outcome
    note = _clip(note)
    names = {
        s.id: s.name
        for s in session.query(Section).filter(
            Section.id.in_({status.section_id for status in statuses})
        )
    }
    reopen_reason = None
    if any((s.section_id, s.year_month) in gaps for s in statuses):
        reopen_reason = validate_reason(
            f"Reopened automatically: {note} Mark the new day(s), then finalize again.",
            max_chars=COMPOSED_REASON_MAX,
        )
    for status in sorted(statuses, key=lambda s: (s.year_month, names.get(s.section_id, ""))):
        label = (
            f"{names.get(status.section_id, 'a section')} — "
            f"{_calendar.month_name[status.year_month.month]} {status.year_month.year}"
        )
        month = {"year": status.year_month.year, "month": status.year_month.month}
        if (status.section_id, status.year_month) in gaps:
            mark_reopened(status, user_id, reopen_reason)
            audit_service.record(
                session,
                action=audit_service.ATTENDANCE_MONTH_REOPENED,
                object_type="attendance_month_status",
                object_id=status.id,
                user_id=user_id,
                previous={"status": FinalizationState.FINALIZED},
                new={"status": status.status, **month, "automatic": True},
                reason=reopen_reason,
            )
            outcome.reopened.append(label)
        else:
            previous = mark_changed_after_finalize(status, user_id, note)
            audit_service.record(
                session,
                action=audit_service.ATTENDANCE_CHANGED_AFTER_FINALIZE,
                object_type="attendance_month_status",
                object_id=status.id,
                user_id=user_id,
                previous=previous,
                new={
                    **month,
                    "changed_after_finalize_by_user_id": status.changed_after_finalize_by_user_id,
                    "changed_after_finalize_at": status.changed_after_finalize_at,
                    "change_after_finalize_note": note,
                },
                reason=note,
            )
            outcome.flagged.append(label)
    return outcome


# --- Academic Calendar ------------------------------------------------------


def apply_calendar_flip(
    session: Session, day: AcademicCalendarDate, user_id, note: str | None
) -> Outcome:
    """After `day.is_default_class_day` has been flipped (not yet committed).

    A day that stopped being a class day only flags: its column drops off
    and the totals shift, but nothing is missing. A day that became one
    reopens each section with a learner in window and no mark that day —
    usually all of them, though a day unticked and ticked again can still
    hold the marks it had, and those sections are only flagged.
    """
    statuses = _finalized_months(session, day.school_year_id, [day.calendar_date.replace(day=1)])
    if not statuses:
        return Outcome()
    became = "is now a class day" if day.is_default_class_day else "is no longer a class day"
    text = f"{day.calendar_date:%b %d, %Y} {became} on the Academic Calendar"
    text += f" ({note})." if note else "."
    gaps = set()
    if day.is_default_class_day:
        gaps = {
            (section_id, day.calendar_date.replace(day=1))
            for section_id in _sections_missing_a_mark(
                session, day, [s.section_id for s in statuses]
            )
        }
    return _settle(session, statuses, gaps, user_id, text)


def _sections_missing_a_mark(session: Session, day: AcademicCalendarDate, section_ids) -> set:
    """Sections with at least one learner in window on `day` and no mark
    for it. A fixed handful of queries whatever the number of sections."""
    rows = section_year_windows(session, set(section_ids), day.school_year_id)
    in_window = {e.id: e.section_id for e, _, window in rows if window.contains(day.calendar_date)}
    if not in_window:
        return set()
    marked = {enrollment_id for enrollment_id, _ in statuses_for_days(session, list(in_window), [day])}
    return {section_id for enrollment_id, section_id in in_window.items() if enrollment_id not in marked}


# --- Learner movements ------------------------------------------------------


def lock_movements(session: Session, enrollment: Enrollment) -> list[Movement]:
    """Call BEFORE the page writes anything: locks the enrollment, so two
    people changing one learner's movements take turns, and returns the log
    as it is in the database now rather than as the page rendered it. The
    "before" side of `apply_movement_change`."""
    session.refresh(enrollment, with_for_update=True)
    log = movements_by_enrollment(session, [enrollment.id]).get(enrollment.id, [])
    return [Movement(m.movement_type, m.effective_date) for m in log]


def apply_movement_change(
    session: Session,
    enrollment: Enrollment,
    before: list[Movement],
    changed: list,
    user_id,
    note: str,
) -> Outcome:
    """After a movement is logged or removed and flushed (not yet committed).

    `before` is `lock_movements`' result, taken before the change; the log
    after it is read back here, so neither side is a copy of what the page
    rendered. `changed` is the movements added or removed — anything with
    `effective_date`. Logging one only ever narrows the window
    (`compute_active_window` keeps the latest entry and the earliest exit),
    so it only flags. Removing one can widen it, and a finalized month that
    regains days with no mark for this learner is reopened.
    """
    after = [
        Movement(m.movement_type, m.effective_date)
        for m in movements_by_enrollment(session, [enrollment.id]).get(enrollment.id, [])
    ]
    school_year = session.get(SchoolYear, enrollment.school_year_id)
    old = compute_active_window(before, default_start=school_year.start_date)
    new = compute_active_window(after, default_start=school_year.start_date)
    months = months_touched_by_movements(
        old, new, [m.effective_date for m in changed], school_year.start_date, school_year.end_date
    )
    statuses = _finalized_months(session, enrollment.school_year_id, months, [enrollment.section_id])
    if not statuses:
        return Outcome()
    gaps = _months_with_unmarked_regained_days(session, enrollment, old, new, statuses)
    return _settle(session, statuses, gaps, user_id, note)


def _months_with_unmarked_regained_days(
    session: Session, enrollment: Enrollment, old: ActiveWindow, new: ActiveWindow, statuses
) -> set:
    """(section_id, year_month) for each finalized month holding a class day
    the new window covers, the old one did not, and nobody marked. Empty
    without a query when the window did not widen."""
    if not _widened(old, new):
        return set()
    year_months = {s.year_month for s in statuses}
    last = max(year_months)
    month_end = date(last.year, last.month, _calendar.monthrange(last.year, last.month)[1])
    candidates = [
        d
        for d in session.query(AcademicCalendarDate).filter(
            AcademicCalendarDate.school_year_id == enrollment.school_year_id,
            AcademicCalendarDate.is_default_class_day.is_(True),
            AcademicCalendarDate.calendar_date >= min(year_months),
            AcademicCalendarDate.calendar_date <= month_end,
        )
        if d.calendar_date.replace(day=1) in year_months
        and new.contains(d.calendar_date)
        and not old.contains(d.calendar_date)
    ]
    if not candidates:
        return set()
    marked = {day_id for _, day_id in statuses_for_days(session, [enrollment.id], candidates)}
    return {
        (enrollment.section_id, d.calendar_date.replace(day=1))
        for d in candidates
        if d.id not in marked
    }


def _widened(old: ActiveWindow, new: ActiveWindow) -> bool:
    starts_earlier = old.start is not None and (new.start is None or new.start < old.start)
    ends_later = old.end is not None and (new.end is None or new.end > old.end)
    return starts_earlier or ends_later


def movement_note(action: str, learner, movement_type, effective_date: date) -> str:
    """e.g. "Movement logged: DELA CRUZ, JUAN — Transferred Out, effective
    Sep 12, 2026." — the cause the reprint notice shows."""
    return (
        f"Movement {action}: {learner.last_name}, {learner.first_name} — "
        f"{movement_label(movement_type)}, effective {effective_date:%b %d, %Y}."
    )


def removal_note(learner, movements: list, reason: str) -> str:
    """The note for removing one or several movements, with the registrar's
    typed reason, so the reprint notice says why the SF2 changed. Several
    are summarised: a long list would be clipped and lose the reason."""
    if len(movements) == 1:
        m = movements[0]
        what = movement_note("removed", learner, m.movement_type, m.effective_date)
    else:
        what = f"{len(movements)} movements removed: {learner.last_name}, {learner.first_name}."
    return f"{what} Reason: {reason}"
