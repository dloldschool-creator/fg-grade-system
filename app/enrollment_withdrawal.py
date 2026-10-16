"""Withdrawing an enrollment — and the record check that comes before it.

**What a withdrawal is.** The school keeps the Grading System in step with
DepEd's LIS. When the school head directs that a learner is not to be
enrolled in LIS at all (decided 2026-10-14 for three COWIE learners who
had stopped attending), the learner has to disappear from this school
year here too — not be *moved*. A Dropped or NLS movement is the official
way to record a learner who left, and it puts them on SF2, SF4 and SF9;
that is exactly the discrepancy with LIS the school wants gone. So a
withdrawal deletes the enrollment and everything hanging off it, and the
learner's own record (name, LRN, birthdate) stays, attached to no
section, so they can be enrolled again without retyping or a duplicate
LRN.

**Nothing is lost.** Before anything is deleted, every row about to go is
copied into one `ENROLLMENT_WITHDRAWN` audit entry, with the reason and
the name of the person who authorized it. No page edits `audit_logs`
(§50). The one way an entry leaves the table is the Super Admin's
**audit archive** (`app/audit_archive_service.py`), which exports entries
older than a cutoff to a file before deleting them — so once a
withdrawal is archived, its copy and its reprint list live in that
export file and no longer appear on the Withdrawn tab. Keep the archive
files.

**What it refuses.** A withdrawal undoes a learner's year, but it never
undoes someone else's deliberate decision on the way:

- **finalized grades** (`term_grades` FINALIZED/VERIFIED, or a FINALIZED
  `grade_finalization_records` row) — reopen on Grade Summary first;
- a **frozen academic record** (§38) — that is the permanent record;
- an **award override** — a person's decision, not a computed result;
- **any table this module does not know** — the same default as
  `app.enrollment_section_correction`: a table added later that points at
  `enrollments` blocks withdrawal the day it exists, until someone decides
  what withdrawing should do to it.

**Finalized attendance months are reopened and re-finalized
automatically** (decided 2026-10-14, to save the registrar three manual
trips to the Attendance page per learner). Each month is recorded as an
ordinary `ATTENDANCE_MONTH_REOPENED` + `ATTENDANCE_MONTH_FINALIZED` pair
naming the withdrawal as the reason, so the Attendance month history
reads correctly on its own. Re-finalizing **restores the original
finalizer and date** rather than stamping the withdrawer over them; the
reopen fields record that the withdrawal happened.

Every month the learner **appeared on SF2** is listed in the withdrawal
entry as an **SF2/SF4 to reprint**, because a copy already printed or
submitted still carries them. "Appeared" is
`attendance_engine.appears_in_month` over the learner's active window,
not "has an attendance row": saving a month writes a default PRESENT row
for everyone on that day's roster (the old Prepare button wrote the whole
month), so an exit movement backdated after a save leaves rows past the
exit date that no SF2 ever printed.

**Concurrency.** Same as the section correction: the enrollment row is
locked `FOR UPDATE` before anything is counted, which conflicts with the
`FOR KEY SHARE` every grade or attendance INSERT takes on it — so a
teacher saving at that moment is either counted and copied, or waits. The
caller passes the version of the record it last *showed*, and a record
that has changed since is refused rather than withdrawn on the strength
of a stale check (rule 9).

The record check (`load_record`) is also useful on its own — it is what
the Enrollment page shows under **Check record**, and what
`scripts/learner_footprint.py` prints — and its anomaly flags
(attendance rows marked present after the learner left; still Enrolled
but absent on every recent day) catch data problems whether or not
anyone withdraws.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import String, case, cast, func, literal, select, union_all

from app import audit_service
from app.attendance_engine import EXIT_MOVEMENTS, Movement, appears_in_month, compute_active_window
from app.attendance_service import bump_version, reopen_month
from app.display_time import to_school_time
from app.enrollment_section_correction import DERIVED, _enrollment_fks
from app.models.academic_structure import GradeLevel, Section, Strand, Track
from app.models.attendance import AcademicCalendarDate, AttendanceMonthStatus, AttendanceRecord
from app.models.enums import AttendanceStatus, FinalizationState
from app.models.grades import TermGrade
from app.models.learners import Enrollment, Learner, LearnerMovement
from app.models.organization import School, SchoolYear, Term
from app.models.rbac import Role, User, UserRole
from app.models.subjects import SectionSubjectOffering
from app.text_rules import SHORT_MAX, clean_text, reason_problem

# Rows a person entered for this enrollment that withdrawing removes —
# after copying them into the audit entry. Everything in DERIVED is
# removed too (a recompute writes it); anything in neither set blocks.
DELETED = {
    "term_grades",
    "attendance_records",
    "learner_movements",
    "enrollment_subject_overrides",
    "grade_finalization_records",
    "parent_notice_overrides",
    "parent_meeting_schedules",
    "parent_notifications",
}

LABELS = {
    "term_grades": "Term grades",
    "attendance_records": "Attendance days",
    "learner_movements": "Movements",
    "enrollment_subject_overrides": "Subject substitutions",
    "grade_finalization_records": "Grade finalization records",
    "parent_notice_overrides": "Parent notice overrides",
    "parent_meeting_schedules": "Parent meeting schedules",
    "parent_notifications": "Parent notices sent",
    "learner_academic_records": "Frozen academic record",
    "subject_final_grades": "Subject final grades (computed)",
    "combined_learning_area_results": "Combined language results (computed)",
    "term_grade_summaries": "Term averages (computed)",
    "annual_grade_summaries": "General average (computed)",
    "learner_awards": "Award results",
}

# LATE and CUTTING still count as present (app/attendance_engine.py).
PRESENT_LIKE = {AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.CUTTING}
# §31's warning is five consecutive absences; the same run is what makes
# "still Enrolled" worth questioning.
RECENT_ABSENCE_RUN = 5


class WithdrawalRefused(Exception):
    """Raised with a message meant to be shown to the user as is."""


@dataclass(frozen=True)
class TableCount:
    table: str
    label: str
    group: str  # "blocks" | "deleted" | "derived"
    total: int
    by_status: dict
    blocking: int  # rows of this table that stop a withdrawal


@dataclass
class LearnerRecord:
    learner_id: object
    enrollment_id: object
    enrollment_version: int
    name: str
    last_name: str
    lrn: str | None
    sex: str
    birthdate: date
    learner_created_by: str | None
    school_year: str
    grade_level: str
    track: str
    strand: str
    section_id: object
    section: str
    adviser: str | None
    status: str
    enrolled_on: date
    movements: list = field(default_factory=list)  # (type, effective date, reason)
    attendance: dict = field(default_factory=dict)  # status -> count
    first_marked: date | None = None
    last_present: date | None = None
    grades_by_term: dict = field(default_factory=dict)  # term -> {status: n, "offerings": n}
    tables: list = field(default_factory=list)  # [TableCount]
    affected_months: list = field(default_factory=list)  # [date], first of month
    finalized_months: list = field(default_factory=list)  # subset of affected_months
    anomalies: list = field(default_factory=list)  # [str]

    @property
    def blockers(self) -> list[str]:
        return [f"{t.label} ({t.blocking})" for t in self.tables if t.blocking]

    @property
    def masked_lrn(self) -> str:
        return mask_lrn(self.lrn)


def mask_lrn(lrn: str | None) -> str:
    return f"••••••••{lrn[-4:]}" if lrn else "—"


def _full_name(learner) -> str:
    parts = [learner.first_name, learner.middle_name, learner.extension_name]
    return f"{learner.last_name}, " + " ".join(p for p in parts if p)


def _known_tables() -> set:
    return DELETED | DERIVED


def _status_expr(table):
    """What a table's rows are broken down by on the check screen."""
    if table.name == "learner_awards":
        return case((table.c.is_override.is_(True), "OVERRIDE"), else_="COMPUTED")
    if "status" in table.c:
        return cast(table.c.status, String)
    return None


def _blocking_count(table, by_status: dict, total: int) -> int:
    if table.name not in _known_tables():
        return total
    if table.name == "term_grades":
        return by_status.get("FINALIZED", 0) + by_status.get("VERIFIED", 0)
    if table.name == "grade_finalization_records":
        return by_status.get("FINALIZED", 0)
    if table.name == "learner_awards":
        return by_status.get("OVERRIDE", 0)
    return 0


def _group(table) -> str:
    if table.name not in _known_tables():
        return "blocks"
    return "derived" if table.name in DERIVED else "deleted"


def table_counts(session, enrollment_id) -> list[TableCount]:
    """Every table pointing at `enrollments`, counted and broken down by
    status in **one** round trip, whatever the number of tables."""
    fks = _enrollment_fks()
    parts = []
    for table, column in fks:
        status = _status_expr(table)
        part = select(
            literal(f"{table.name}.{column.name}").label("t"),
            (status if status is not None else cast(literal(None), String)).label("s"),
            func.count().label("n"),
        ).where(column == enrollment_id)
        parts.append(part.group_by(status) if status is not None else part)
    found: dict = {}
    for name, status, n in session.execute(union_all(*parts)).all():
        found.setdefault(name, {})[status or "rows"] = n
    counts = []
    for table, column in fks:
        by_status = found.get(f"{table.name}.{column.name}", {})
        total = sum(by_status.values())
        if not total:
            continue
        counts.append(
            TableCount(
                table=table.name,
                label=LABELS.get(table.name, table.name.replace("_", " ").capitalize()),
                group=_group(table),
                total=total,
                by_status={k: v for k, v in by_status.items() if k != "rows"},
                blocking=_blocking_count(table, by_status, total),
            )
        )
    return counts


def _months(dates) -> list[date]:
    return sorted({d.replace(day=1) for d in dates})


def load_record(session, enrollment_id) -> LearnerRecord:
    """Everything worth knowing about one enrollment before acting on it.

    About ten round trips, flat — call it for **one** learner, behind a
    button, never per row of a roster.
    """
    enrollment = session.get(Enrollment, enrollment_id)
    if enrollment is None:
        raise WithdrawalRefused("That enrollment no longer exists — it may already have been withdrawn.")
    learner = session.get(Learner, enrollment.learner_id)
    section = session.get(Section, enrollment.section_id)
    grade_level = session.get(GradeLevel, enrollment.grade_level_id)
    track = session.get(Track, section.track_id)
    strand = session.get(Strand, section.strand_id)
    school_year = session.get(SchoolYear, enrollment.school_year_id)
    user_ids = {u for u in (section.adviser_user_id, learner.created_by_user_id) if u}
    users = (
        {u.id: u.full_name for u in session.query(User).filter(User.id.in_(user_ids)).all()}
        if user_ids
        else {}
    )

    movements = (
        session.query(LearnerMovement)
        .filter_by(enrollment_id=enrollment.id)
        .order_by(LearnerMovement.effective_date, LearnerMovement.created_at)
        .all()
    )
    days = session.execute(
        select(AcademicCalendarDate.calendar_date, AttendanceRecord.status)
        .join(AttendanceRecord, AttendanceRecord.calendar_date_id == AcademicCalendarDate.id)
        .where(AttendanceRecord.enrollment_id == enrollment.id)
        .order_by(AcademicCalendarDate.calendar_date)
    ).all()

    # Every term_grades row, so these add up to the Term grades line in
    # `tables` — a row blanked back to not-yet-encoded is still a row
    # withdrawal deletes. "blank" says how many of them hold no grade.
    blank = TermGrade.official_grade.is_(None)
    grades: dict = {}
    for term_name, status, is_blank, n in session.execute(
        select(Term.name, TermGrade.status, blank, func.count())
        .select_from(TermGrade)
        .join(Term, Term.id == TermGrade.term_id)
        .where(TermGrade.enrollment_id == enrollment.id)
        .group_by(Term.name, TermGrade.status, blank)
    ).all():
        counts = grades.setdefault(term_name, {})
        counts[status.value] = counts.get(status.value, 0) + n
        if is_blank:
            counts["blank"] = counts.get("blank", 0) + n
    for term_name, n in session.execute(
        select(Term.name, func.count())
        .select_from(SectionSubjectOffering)
        .join(Term, Term.id == SectionSubjectOffering.term_id)
        .where(SectionSubjectOffering.section_id == section.id)
        .group_by(Term.name)
    ).all():
        grades.setdefault(term_name, {})["offerings"] = n

    # The same window SF2 uses (§32): a month counts only if the learner
    # was printed on it, not merely if a default PRESENT row exists.
    window = compute_active_window([Movement(m.movement_type, m.effective_date) for m in movements])
    affected = [
        month
        for month in _months([d for d, _ in days] + [m.effective_date for m in movements])
        if appears_in_month(window, month.year, month.month)
    ]
    finalized = (
        sorted(
            row[0]
            for row in session.query(AttendanceMonthStatus.year_month)
            .filter(
                AttendanceMonthStatus.section_id == section.id,
                AttendanceMonthStatus.status == FinalizationState.FINALIZED,
                AttendanceMonthStatus.year_month.in_(affected),
            )
            .all()
        )
        if affected
        else []
    )

    present_days = [d for d, s in days if s in PRESENT_LIKE]
    record = LearnerRecord(
        learner_id=learner.id,
        enrollment_id=enrollment.id,
        enrollment_version=enrollment.version,
        name=_full_name(learner),
        last_name=learner.last_name,
        lrn=learner.lrn,
        sex=learner.sex.value,
        birthdate=learner.birthdate,
        learner_created_by=users.get(learner.created_by_user_id),
        school_year=school_year.name,
        grade_level=grade_level.name,
        track=track.name,
        strand=strand.name,
        section_id=section.id,
        section=section.name,
        adviser=users.get(section.adviser_user_id),
        status=enrollment.enrollment_status.value,
        enrolled_on=to_school_time(enrollment.created_at).date(),
        movements=[
            (m.movement_type.value, m.effective_date, " - ".join(p for p in (m.nls_reason, m.details) if p))
            for m in movements
        ],
        attendance={s.value: n for s, n in Counter(s for _, s in days).items()},
        first_marked=days[0][0] if days else None,
        last_present=present_days[-1] if present_days else None,
        grades_by_term=dict(sorted(grades.items())),
        tables=table_counts(session, enrollment.id),
        affected_months=affected,
        finalized_months=finalized,
    )
    record.anomalies = _anomalies(enrollment, window, days)
    return record


def _anomalies(enrollment, window, days) -> list[str]:
    """`window` is `compute_active_window` over the learner's movements —
    the engine's own definition of when they left (EXIT_MOVEMENTS)."""
    found = []
    if window.end is not None:
        after = [d for d, s in days if d > window.end and s in PRESENT_LIKE]
        if after:
            found.append(
                f"{len(after)} attendance row(s) marked present after the learner left on "
                f"{window.end:%d %b %Y} (latest {after[-1]:%d %b %Y}). SF2 does not count "
                "days after a learner leaves, so the printed form is unaffected — these "
                "are usually the month's default \"present\" rows that were never "
                "changed, and they are stray data."
            )
    elif enrollment.enrollment_status not in EXIT_MOVEMENTS:
        today = date.today()
        recent = [s for d, s in days if d <= today][-RECENT_ABSENCE_RUN:]
        if len(recent) == RECENT_ABSENCE_RUN and all(s == AttendanceStatus.ABSENT for s in recent):
            found.append(
                f"Still {enrollment.enrollment_status.value}, but absent on each of the "
                f"last {RECENT_ABSENCE_RUN} days marked. If the learner has stopped "
                "attending, log a movement (or withdraw, if so directed)."
            )
    return found


# --- Who may authorize ------------------------------------------------------


def authorizer_options(session) -> list[str]:
    """The school head named on School Info first — that is the name the
    forms print — then every active account holding the School Head role.
    De-duplicated, case-insensitively."""
    names = [row[0] for row in session.query(School.school_head_name).all() if row[0]]
    names += [
        row[0]
        for row in session.query(User.full_name)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .filter(Role.code == "SCHOOL_HEAD", User.is_active.is_(True))
        .order_by(User.full_name)
        .all()
    ]
    seen, unique = set(), []
    for name in names:
        key = " ".join(name.split()).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(name.strip())
    return unique


# --- The withdrawal ---------------------------------------------------------


def _snapshot_rows(session, table, column, enrollment_id) -> list[dict]:
    return [dict(row._mapping) for row in session.execute(select(table).where(column == enrollment_id))]


def withdraw_enrollment(
    session,
    *,
    enrollment_id,
    expected_version: int,
    user_id,
    reason: str,
    authorized_by: str,
    confirm_last_name: str,
) -> dict:
    """Withdraws one enrollment. The caller commits — and must, promptly,
    since the enrollment row stays locked until it does.

    Returns the summary written to the audit entry's `new` value:
    learner, section, the months to reprint and the months reopened.
    """
    problem = reason_problem(reason)
    if problem:
        raise WithdrawalRefused(problem)
    reason = clean_text(reason)
    authorized_by = clean_text(authorized_by)
    if not authorized_by:
        raise WithdrawalRefused("Name who authorized the withdrawal.")
    if len(authorized_by) > SHORT_MAX:
        raise WithdrawalRefused(f"Keep the authorizer's name to {SHORT_MAX} characters or fewer.")

    enrollment = session.get(Enrollment, enrollment_id)
    if enrollment is None:
        raise WithdrawalRefused("That enrollment no longer exists — it may already have been withdrawn.")
    # Lock first, then read: see the module docstring.
    session.refresh(enrollment, with_for_update=True)
    if enrollment.version != expected_version:
        raise WithdrawalRefused(
            "Someone changed this learner's enrollment after the record above was "
            "shown. It has been reloaded — review it, then withdraw again."
        )

    learner = session.get(Learner, enrollment.learner_id)
    typed = " ".join((confirm_last_name or "").split()).casefold()
    if typed != " ".join(learner.last_name.split()).casefold():
        raise WithdrawalRefused(
            f"Type the learner's last name exactly — {learner.last_name} — to confirm."
        )

    record = load_record(session, enrollment.id)
    if record.blockers:
        raise WithdrawalRefused(
            "This learner has " + ", ".join(record.blockers) + ", which a withdrawal "
            "will not undo by itself. Reopen finalized grades on Grade Summary and "
            "clear award overrides on Awards first; a frozen academic record or an "
            "unrecognised table needs the system administrator."
        )

    # Copy everything before anything goes.
    snapshot_rows = {}
    for table, column in _enrollment_fks():
        rows = _snapshot_rows(session, table, column, enrollment.id)
        if rows:
            snapshot_rows[table.name] = rows
    enrollment_row = {c.name: getattr(enrollment, c.name) for c in Enrollment.__table__.columns}

    # Reopen finalized months for the duration of the change.
    month_rows = (
        session.query(AttendanceMonthStatus)
        .filter(
            AttendanceMonthStatus.section_id == enrollment.section_id,
            AttendanceMonthStatus.year_month.in_(record.finalized_months),
            # load_record read these as FINALIZED without a lock; an adviser
            # may have reopened one since. A month that is already open needs
            # no reopen (and no re-finalize, which would undo theirs).
            AttendanceMonthStatus.status == FinalizationState.FINALIZED,
        )
        .with_for_update()
        .all()
        if record.finalized_months
        else []
    )
    # Name capped so the wrapped text always fits COMPOSED_REASON_MAX, which
    # reopen_month and audit_service enforce: 300 (reason) + 100 + 100 + the
    # fixed wording. Left unbounded, a long name would fail the reopen
    # halfway through the withdrawal.
    month_reason = (
        f"Enrollment withdrawal of {record.name[:SHORT_MAX]} "
        f"(authorized by {authorized_by}): {reason}"
    )
    reopened = []
    for status in month_rows:
        previous = {
            "status": status.status,
            "finalized_by_user_id": status.finalized_by_user_id,
            "finalized_at": status.finalized_at,
        }
        reopen_month(session, status, user_id, month_reason)
        audit_service.record(
            session,
            action=audit_service.ATTENDANCE_MONTH_REOPENED,
            object_type="attendance_month_status",
            object_id=status.id,
            user_id=user_id,
            previous=previous,
            new={
                "status": status.status,
                "year": status.year_month.year,
                "month": status.year_month.month,
                "withdrawn_enrollment_id": enrollment.id,
            },
            reason=month_reason,
        )
        reopened.append((status, previous))

    removed = {}
    for table, column in _enrollment_fks():
        result = session.execute(table.delete().where(column == enrollment.id))
        if result.rowcount:
            removed[table.name] = removed.get(table.name, 0) + result.rowcount
    session.delete(enrollment)
    session.flush()

    for status, previous in reopened:
        # Not `finalize_month`: that stamps the withdrawer as finalizer, and
        # the Attendance page would then credit the registrar with closing a
        # month an adviser closed weeks earlier. The reopen fields keep the
        # record that the withdrawal touched it.
        status.status = FinalizationState.FINALIZED
        status.finalized_by_user_id = previous["finalized_by_user_id"]
        status.finalized_at = previous["finalized_at"]
        bump_version(status)
        audit_service.record(
            session,
            action=audit_service.ATTENDANCE_MONTH_FINALIZED,
            object_type="attendance_month_status",
            object_id=status.id,
            user_id=user_id,
            new={
                "status": status.status,
                "year": status.year_month.year,
                "month": status.year_month.month,
                "refinalized_after_withdrawal_of": enrollment.id,
                "finalized_by_user_id": status.finalized_by_user_id,
                "finalized_at": status.finalized_at,
            },
        )

    summary = {
        "learner": record.name,
        "learner_id": record.learner_id,
        "lrn_last4": record.lrn[-4:] if record.lrn else None,
        "school_year": record.school_year,
        "grade_level": record.grade_level,
        "section": record.section,
        "section_id": record.section_id,
        "adviser": record.adviser,
        "status_at_withdrawal": record.status,
        "authorized_by": authorized_by,
        "sf2_sf4_months_to_reprint": [m.strftime("%Y-%m") for m in record.affected_months],
        "months_reopened_and_refinalized": [s.year_month.strftime("%Y-%m") for s, _ in reopened],
        "rows_removed": removed,
    }
    audit_service.record(
        session,
        action=audit_service.ENROLLMENT_WITHDRAWN,
        object_type="enrollments",
        object_id=enrollment_id,
        user_id=user_id,
        previous={"enrollment": enrollment_row, "rows": snapshot_rows},
        new=summary,
        reason=reason,
    )
    return summary


def withdrawal_history(session, limit: int = 200) -> list:
    """Past withdrawals, newest first, read from the audit log."""
    from app.models.admin import AuditLog

    return (
        session.query(AuditLog)
        .filter(AuditLog.action == audit_service.ENROLLMENT_WITHDRAWN)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
