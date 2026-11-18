"""Editing a logged movement in place, and refusing a second one of a kind
(2026-11-18).

Until now a movement could only be logged or removed (removal is
registrar-only), so an adviser who typed the wrong date or school had to ask
the registrar to remove the entry and then log it again — a second instance
of the same movement, which is exactly what should never exist.

Two rules live here:

1. **A learner has at most one Transferred In, one Transferred Out, one
   Dropped and one NLS per enrollment.** The check is per *type*, not "one
   exit in total": a learner may leave and come back (Transferred Out, then
   Transferred In), and `enrollment_status.latest_exit_movement` already
   reads the log that way. `duplicate_problem` is the guard for logging.
   Shifted In/Out and the other types are not limited — the school named
   only the four above.
2. **An entry is corrected, not re-logged.** `edit_movement` changes the
   date, schools, reason and remarks of an existing row — never its type —
   with a required reason, an audit entry holding the old and new values,
   and the same finalized-month handling a log or removal gets
   (`finalized_month_changes.apply_movement_change`): a date that moves into
   or out of a finalized month flags it for SF2/SF4 reprint, and one that
   leaves class days nobody marked reopens it.

An enrollment's `enrollment_status` is deliberately left alone: it is set
when a movement is logged and recomputed when one is removed, and an edit
changes neither which movement is latest by *type* nor what was logged.

`learner_movements` has no `version` column, so rule 9 is met by locking the
enrollment (`lock_movements`) and comparing the row with the values the page
showed (`expected`): a stale edit is refused, never silently merged.

Nothing here commits — the caller's `try_commit` does.
"""

from types import SimpleNamespace

from sqlalchemy.exc import SQLAlchemyError

from app import audit_service
from app.enrollment_status import movement_label
from app.finalized_month_changes import Outcome, apply_movement_change, lock_movements, movement_note
from app.models.enums import EnrollmentStatus
from app.models.learners import Enrollment, LearnerMovement
from app.text_rules import clean_text, reason_problem

# One of each per enrollment. See the module docstring for why this is a
# per-type limit and not a limit on exits.
ONCE_PER_ENROLLMENT = (
    EnrollmentStatus.TRANSFERRED_IN,
    EnrollmentStatus.TRANSFERRED_OUT,
    EnrollmentStatus.DROPPED,
    EnrollmentStatus.NLS,
)

# The columns an edit may change. The type is never one of them.
EDITABLE_FIELDS = (
    "effective_date",
    "details",
    "previous_school",
    "receiving_school",
    "nls_reason",
    "remarks",
)


class MovementRefused(Exception):
    """The edit was not made. The message is for the person pressing Save."""


class StaleMovement(MovementRefused):
    """The row changed since the page showed it, so the form must reload."""


def duplicate_problem(log_before: list, movement_type: EnrollmentStatus) -> str | None:
    """A message if `movement_type` is already on the log, else None.

    `log_before` is `lock_movements`' result (the log as the database holds
    it now, not as the page rendered), so two people logging the same
    movement at once take turns and the second is refused.
    """
    if movement_type not in ONCE_PER_ENROLLMENT:
        return None
    for existing in log_before:
        if existing.movement_type == movement_type:
            return (
                f"{movement_label(movement_type)} is already logged for this learner "
                f"(effective {existing.effective_date:%b %d, %Y}). A learner can only "
                "have one — edit that entry instead."
            )
    return None


def fields_problem(movement_type: EnrollmentStatus, previous_school, receiving_school) -> str | None:
    """The school each transfer needs: Previous school for Transferred In,
    Receiving school for Transferred Out."""
    if movement_type == EnrollmentStatus.TRANSFERRED_IN and not (previous_school or "").strip():
        return "Previous school is required for a Transferred In movement."
    if movement_type == EnrollmentStatus.TRANSFERRED_OUT and not (receiving_school or "").strip():
        return "Receiving school is required for a Transferred Out movement."
    return None


def normalise_fields(
    movement_type: EnrollmentStatus,
    *,
    effective_date,
    details,
    previous_school,
    receiving_school,
    nls_reason,
    remarks,
) -> dict:
    """What gets stored. A school that does not apply to the type is dropped
    rather than kept — a Transferred In row must never carry a Receiving
    school, and vice versa — and blanks become None, as when logging."""
    return {
        "effective_date": effective_date,
        "details": clean_text(details) or None,
        "previous_school": (
            None if movement_type == EnrollmentStatus.TRANSFERRED_OUT else clean_text(previous_school) or None
        ),
        "receiving_school": (
            None if movement_type == EnrollmentStatus.TRANSFERRED_IN else clean_text(receiving_school) or None
        ),
        "nls_reason": clean_text(nls_reason) or None,
        "remarks": clean_text(remarks) or None,
    }


def _same(new_value, stored_value) -> bool:
    """Equal once the stored text is cleaned the way `normalise_fields`
    cleans input, so an untouched field holding stray spacing from an older
    entry is not counted (and audited) as a change."""
    if isinstance(stored_value, str):
        stored_value = clean_text(stored_value) or None
    return new_value == stored_value


def snapshot(movement) -> dict:
    """The editable columns of a movement, as the page shows them. The page
    keeps this as `expected` and `edit_movement` compares it with the row
    under the lock."""
    return {name: getattr(movement, name) for name in EDITABLE_FIELDS}


def edit_movement(
    session,
    *,
    enrollment: Enrollment,
    movement: LearnerMovement,
    learner,
    expected: dict,
    new: dict,
    reason: str,
    user_id,
    allowed_section_ids,
) -> Outcome:
    """Change one movement's own fields. Raises `MovementRefused`; the caller
    rolls back. `allowed_section_ids` is None for a school-wide role, else the
    sections the user advises — the page only lists those, and this is the
    backstop for anything that reaches here another way."""
    if allowed_section_ids is not None and enrollment.section_id not in allowed_section_ids:
        raise MovementRefused("That learner is not in a section you advise.")
    if movement.enrollment_id != enrollment.id:
        raise MovementRefused("That movement belongs to another learner.")
    problem = reason_problem(reason)
    if problem:
        raise MovementRefused(problem)
    problem = fields_problem(movement.movement_type, new["previous_school"], new["receiving_school"])
    if problem:
        raise MovementRefused(problem)

    # Locks the enrollment and reads the log now, before anything is written.
    log_before = lock_movements(session, enrollment)
    session.refresh(movement)
    if snapshot(movement) != expected:
        raise StaleMovement(
            "Someone else changed this movement while you were editing it. "
            "The form now shows the current values — check them and try again."
        )
    changed = {name: new[name] for name in EDITABLE_FIELDS if not _same(new[name], expected[name])}
    if not changed:
        raise MovementRefused("Nothing was changed.")

    old_date = movement.effective_date
    for name, value in changed.items():
        setattr(movement, name, value)
    enrollment.version += 1
    audit_service.record(
        session,
        action=audit_service.LEARNER_MOVEMENT_EDITED,
        object_type="learner_movements",
        object_id=movement.id,
        user_id=user_id,
        previous={"movement_type": movement.movement_type, **{k: expected[k] for k in changed}},
        new={"movement_type": movement.movement_type, **changed},
        reason=clean_text(reason),
    )
    try:
        session.flush()
    except SQLAlchemyError as exc:  # pragma: no cover - no constraint can fire today
        raise MovementRefused("Couldn't save the change.") from exc
    # Both dates: the month the entry left and the month it now sits in each
    # change what SF2/SF4 print.
    touched = [SimpleNamespace(effective_date=old_date), SimpleNamespace(effective_date=movement.effective_date)]
    note = (
        movement_note("edited", learner, movement.movement_type, movement.effective_date)
        + f" Reason: {clean_text(reason)}"
    )
    return apply_movement_change(
        session, enrollment, before=log_before, changed=touched, user_id=user_id, note=note
    )
