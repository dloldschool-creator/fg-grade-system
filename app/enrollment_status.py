"""Shared vocabulary for a learner's enrollment/movement status (§27, §32).

`LearnerMovement` is the one record of a status change, and more than one
printed form needs to describe it in the same words — SF2's Remarks column
(`app/sf2_report.py`) and SF9's exit-status line (`app/sf9_report.py`) both
read this module rather than each keeping their own label map, which is
exactly how the same event could end up worded two different ways on two
official forms. Dependency-free by design, the same reasoning as
`app/roster_order.py` and `app/section_access.py` — nothing here should ever
affect import order (see CLAUDE.md's Python-version section).
"""

from app.models.enums import EnrollmentStatus
from app.models.learners import LearnerMovement

MOVEMENT_LABELS = {
    EnrollmentStatus.TRANSFERRED_OUT: "Transferred Out",
    EnrollmentStatus.TRANSFERRED_IN: "Transferred In",
    EnrollmentStatus.NLS: "NLS",
    EnrollmentStatus.DROPPED: "Dropped",
    EnrollmentStatus.SHIFTED_OUT: "Shifted Out",
    EnrollmentStatus.SHIFTED_IN: "Shifted In",
    EnrollmentStatus.LATE_ENROLLMENT: "Late Enrollment",
}


def movement_label(movement_type: EnrollmentStatus) -> str:
    return MOVEMENT_LABELS.get(movement_type, movement_type.value.replace("_", " ").title())


# A learner who left the school before the year's grades could be complete
# (§35 amendment, decided with the school 2026-09-05). SF9's Remarks column
# uses this to explain why every remaining subject reads blank instead of
# printing INCOMPLETE once per row. Deliberately narrower than every
# "exit-shaped" status — TRANSFERRED_IN, SHIFTED_IN, COMPLETED, GRADUATED
# and OTHER don't leave a stack of blank rows needing an explanation, and
# SHIFTED_OUT was left off the list the school confirmed.
EXIT_MOVEMENT_TYPES = {
    EnrollmentStatus.DROPPED,
    EnrollmentStatus.NLS,
    EnrollmentStatus.TRANSFERRED_OUT,
}


def latest_exit_movement(movements: list[LearnerMovement]) -> LearnerMovement | None:
    """The most recent exit-type movement in `movements`, or None.

    "Most recent by effective date" matters when a learner left and came
    back and left again — the status that actually applies to a card
    printed today is the last one, not the first one on file.
    """
    exits = [m for m in movements if m.movement_type in EXIT_MOVEMENT_TYPES]
    if not exits:
        return None
    return max(exits, key=lambda m: m.effective_date)


def exit_status_line(movements: list[LearnerMovement]) -> str | None:
    """SF9's Remarks-column text for a learner who exited before the year's
    grades were complete — "Dropped as of 08/30/2026 due to Child labor,
    work" — or None if none of `movements` is an exit.

    Reads the exact rows `sf2_report.movement_remark` already prints on
    SF2's own Remarks column, so the two documents describe one event
    instead of each computing it separately.
    """
    latest = latest_exit_movement(movements)
    if latest is None:
        return None
    reason = latest.nls_reason or latest.details or latest.remarks
    line = f"{movement_label(latest.movement_type)} as of {latest.effective_date:%m/%d/%Y}"
    return f"{line} due to {reason}" if reason else line
