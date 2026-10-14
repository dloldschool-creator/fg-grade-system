"""Correcting the section an enrollment was entered into.

**What this is for.** An adviser or registrar picks the wrong section on
the Enrollment page. Nothing else can fix it: `enrollments` is unique on
(learner, school year), so the learner cannot be enrolled again, and a
*Shifted* movement only changes a status — SF2 and SF4 would then count a
typing mistake as a real movement on an official form.

**Every table pointing at `enrollments.id` falls into one of three
groups**, and the groups are what this module is about:

1. **Follows the enrollment.** Rows that describe the learner, not the
   section, and stay correct when `section_id` changes:
   `learner_movements` (a Transferred In stays true) and
   `attendance_records`. Attendance is keyed by (enrollment, calendar
   date) and carries no section, so the rows simply appear on the new
   section's sheet. That matters because saving a month's attendance grid writes a
   PRESENT row for every learner (the old Prepare button did the same) —
   blocking on attendance would refuse nearly every real correction. The
   one real attendance hazard is a **finalized** month on either side, and
   that is checked separately.
2. **Derived — deleted, then rebuilt.** Summaries, final grades, combined
   results and computed (non-override) awards are outputs of a recompute.
   Grade Summary's *Recompute all* writes them for every learner, grades or
   not. They can only exist here without grades (grades block, below), so
   they describe nothing and are removed rather than allowed to block;
   the next recompute builds them against the new section.
3. **Blocks.** Everything else, and that is deliberately the default: a
   table added later that points at `enrollments` blocks the move the day
   it exists. Grades point at the old section's offerings, substitutions
   name them, an award override is a person's decision, and a frozen
   academic record or a sent parent notice is history. Whether any of
   those carry over is for the registrar to decide by hand — the school
   chose on 2026-10-14 to keep refusing rather than carry grades across.

**Concurrency.** The enrollment row is locked `FOR UPDATE` *before*
anything is counted. A grade or attendance insert takes `FOR KEY SHARE`
on the enrollment it references (the FK check), which `FOR UPDATE`
conflicts with — so a teacher saving the old section's Gradebook either
commits first and is counted, or waits until the move is done. A plain
UPDATE of `section_id` takes only `FOR NO KEY UPDATE`, which does *not*
conflict, which is why the explicit lock is needed. The caller also passes
the section it was showing, so two people correcting the same learner
cannot silently overwrite each other (rule 9).
"""

from sqlalchemy import Date, func, literal, select, union_all

from app import audit_service
from app.models.academic_structure import Section
from app.models.attendance import AcademicCalendarDate, AttendanceMonthStatus, AttendanceRecord
from app.models.base import Base
from app.models.enums import FinalizationState
from app.models.learners import Enrollment

FOLLOWS = {"learner_movements", "attendance_records"}
DERIVED = {
    "subject_final_grades",
    "combined_learning_area_results",
    "term_grade_summaries",
    "annual_grade_summaries",
    "learner_awards",  # non-override rows only — see _derived_filter
}

_LABELS = {
    "term_grades": "grades",
    "grade_finalization_records": "finalization records",
    "enrollment_subject_overrides": "subject substitutions",
    "learner_awards": "award overrides",
    "learner_academic_records": "permanent academic record",
    "parent_notice_overrides": "parent notice overrides",
    "parent_meeting_schedules": "parent meeting schedules",
    "parent_notifications": "parent notifications",
}


def _enrollment_fks() -> list:
    """(table, column) for every FK to `enrollments.id`. Metadata only."""
    found = []
    for table in Base.metadata.sorted_tables:
        if table.name == "enrollments":
            continue
        for column in table.columns:
            if any(fk.target_fullname == "enrollments.id" for fk in column.foreign_keys):
                found.append((table, column))
    return found


def _blocking_tables() -> list:
    """Every dependent table that blocks. `learner_awards` appears here
    *and* in DERIVED: its override rows block, its computed rows don't."""
    return [
        (t, c) for t, c in _enrollment_fks()
        if t.name not in FOLLOWS and (t.name not in DERIVED or t.name == "learner_awards")
    ]


def _derived_filter(table, column, enrollment_id):
    condition = column == enrollment_id
    if table.name == "learner_awards":
        condition = condition & table.c.is_override.is_(False)
    return condition


def _blocking_filter(table, column, enrollment_id):
    condition = column == enrollment_id
    if table.name == "learner_awards":
        condition = condition & table.c.is_override.is_(True)
    return condition


def blockers(session, enrollment_id) -> dict[str, int]:
    """Label -> row count for everything that stops a correction. Empty
    means it can go ahead. One round trip, whatever the table count."""
    parts = [
        select(
            literal(table.name).label("name"),
            select(func.count())
            .select_from(table)
            .where(_blocking_filter(table, column, enrollment_id))
            .scalar_subquery()
            .label("n"),
        )
        for table, column in _blocking_tables()
    ]
    if not parts:
        return {}
    found: dict[str, int] = {}
    for name, count in session.execute(union_all(*parts)).all():
        if count:
            label = _LABELS.get(name, name.replace("_", " "))
            found[label] = found.get(label, 0) + count
    return found


def finalized_attendance_months(session, enrollment, section_ids) -> list:
    """Months this learner has attendance in that are FINALIZED for any of
    `section_ids`. Moving out of one would change a finalized SF2; moving
    into one would add a learner to it."""
    months = (
        select(func.date_trunc("month", AcademicCalendarDate.calendar_date).cast(Date))
        .join(AttendanceRecord, AttendanceRecord.calendar_date_id == AcademicCalendarDate.id)
        .where(AttendanceRecord.enrollment_id == enrollment.id)
        .distinct()
    )
    return sorted(
        {
            row[0]
            for row in session.query(AttendanceMonthStatus.year_month)
            .filter(
                AttendanceMonthStatus.section_id.in_(list(section_ids)),
                AttendanceMonthStatus.status == FinalizationState.FINALIZED,
                AttendanceMonthStatus.year_month.in_(months),
            )
            .all()
        }
    )


def describe_blockers(found: dict[str, int]) -> str:
    return ", ".join(f"{label} ({count})" for label, count in found.items())


class CorrectionRefused(Exception):
    """Raised with a message meant to be shown to the user as is."""


def correct_section(
    session,
    *,
    enrollment: Enrollment,
    expected_section_id,
    new_section_id,
    user_id,
    reason: str,
    allowed_section_ids=None,
) -> Section:
    """Moves `enrollment` to `new_section_id`. The caller commits — and
    must, promptly, since the enrollment row stays locked until it does.

    `expected_section_id` is the section the person was looking at; if the
    enrollment has moved since, the request is refused rather than applied
    on top. `allowed_section_ids` is the set of sections the actor may
    correct between (an adviser: the ones they advise); `None` means
    unrestricted. Both the current and the new section must be inside it.
    """
    reason = (reason or "").strip()
    if not reason:
        raise CorrectionRefused("A reason is required.")
    unrestricted = allowed_section_ids is None

    # Lock first, then read: see the module docstring.
    session.refresh(enrollment, with_for_update=True)
    if str(enrollment.section_id) != str(expected_section_id):
        raise CorrectionRefused(
            "Someone else has changed this learner's section since you opened the "
            "page. Reload and check before trying again."
        )

    old_section = session.get(Section, enrollment.section_id)
    new_section = session.get(Section, new_section_id)
    if new_section is None:
        raise CorrectionRefused("That section no longer exists.")
    if new_section.id == enrollment.section_id:
        raise CorrectionRefused("The learner is already in that section.")
    if new_section.school_year_id != enrollment.school_year_id:
        raise CorrectionRefused(
            "That section belongs to a different school year. Correcting a "
            "section never moves an enrollment between years."
        )
    if not unrestricted and not (
        {str(enrollment.section_id), str(new_section.id)} <= {str(s) for s in allowed_section_ids}
    ):
        raise CorrectionRefused("You can only correct between sections you advise.")

    found = blockers(session, enrollment.id)
    if found:
        raise CorrectionRefused(
            "This learner already has " + describe_blockers(found) + " tied to the "
            "current section, so the section can't simply be changed. "
            + (
                "Those have to be removed or reversed by hand first, then try again."
                if unrestricted
                else "Ask the registrar to decide what happens to them."
            )
        )

    finalized = finalized_attendance_months(session, enrollment, {old_section.id, new_section.id})
    if finalized:
        months = ", ".join(m.strftime("%B %Y") for m in finalized)
        raise CorrectionRefused(
            f"Attendance for {months} is finalized for one of the two sections, and "
            "moving the learner would change it. "
            + (
                "Reopen that month on the Attendance page first, then try again."
                if unrestricted
                else "Ask the registrar to reopen it first."
            )
        )

    removed: dict[str, int] = {}
    for table, column in _enrollment_fks():
        if table.name not in DERIVED:
            continue
        result = session.execute(table.delete().where(_derived_filter(table, column, enrollment.id)))
        if result.rowcount:
            removed[table.name] = result.rowcount

    previous = {
        "section_id": enrollment.section_id,
        "section": old_section.name if old_section else None,
        "grade_level_id": enrollment.grade_level_id,
        "version": enrollment.version,
    }
    enrollment.section_id = new_section.id
    enrollment.grade_level_id = new_section.grade_level_id
    enrollment.version += 1
    audit_service.record(
        session,
        action=audit_service.ENROLLMENT_SECTION_CORRECTED,
        object_type="enrollments",
        object_id=enrollment.id,
        user_id=user_id,
        previous=previous,
        new={
            "section_id": new_section.id,
            "section": new_section.name,
            "grade_level_id": new_section.grade_level_id,
            "version": enrollment.version,
            "derived_rows_removed": removed,
        },
        reason=reason,
    )
    return new_section

