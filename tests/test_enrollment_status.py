"""Tests for app/enrollment_status.py — the shared movement vocabulary
behind SF2's Remarks column and SF9's exit-status line (§35 amendment,
2026-09-05)."""

from datetime import date

from app.enrollment_status import (
    EXIT_MOVEMENT_TYPES,
    exit_status_line,
    latest_exit_movement,
    movement_label,
)
from app.models.enums import EnrollmentStatus
from app.models.learners import LearnerMovement


def _movement(movement_type, effective_date, **kwargs) -> LearnerMovement:
    return LearnerMovement(movement_type=movement_type, effective_date=effective_date, **kwargs)


# --- Which statuses count as an exit ---------------------------------------


def test_exit_types_are_exactly_dropped_nls_and_transferred_out():
    """The school's own call (2026-09-05): not every movement that sounds
    terminal counts — SHIFTED_OUT and TRANSFERRED_IN don't leave a stack
    of blank subject rows needing this explanation."""
    assert EXIT_MOVEMENT_TYPES == {
        EnrollmentStatus.DROPPED,
        EnrollmentStatus.NLS,
        EnrollmentStatus.TRANSFERRED_OUT,
    }


def test_a_non_exit_movement_produces_no_status_line():
    movements = [_movement(EnrollmentStatus.TRANSFERRED_IN, date(2026, 6, 15))]
    assert latest_exit_movement(movements) is None
    assert exit_status_line(movements) is None


def test_no_movements_at_all_produces_no_status_line():
    assert exit_status_line([]) is None


# --- The printed line -------------------------------------------------------


def test_exit_status_line_names_the_status_date_and_reason():
    movements = [
        _movement(
            EnrollmentStatus.DROPPED, date(2026, 8, 30), nls_reason="Child labor, work"
        )
    ]
    assert exit_status_line(movements) == "Dropped as of 08/30/2026 due to Child labor, work"


def test_reason_falls_back_to_details_then_remarks_when_nls_reason_is_blank():
    only_details = [_movement(EnrollmentStatus.DROPPED, date(2026, 8, 30), details="Moved away")]
    assert exit_status_line(only_details) == "Dropped as of 08/30/2026 due to Moved away"

    only_remarks = [_movement(EnrollmentStatus.DROPPED, date(2026, 8, 30), remarks="Per parent call")]
    assert exit_status_line(only_remarks) == "Dropped as of 08/30/2026 due to Per parent call"


def test_no_reason_at_all_still_names_status_and_date():
    movements = [_movement(EnrollmentStatus.NLS, date(2026, 8, 30))]
    assert exit_status_line(movements) == "NLS as of 08/30/2026"


def test_uses_sf2s_own_labels_so_the_two_forms_agree():
    """SF2's Remarks column already prints "Transferred Out 09/12/2026" via
    `sf2_report.movement_remark` — the label here must be the same word,
    not a second wording of the same event."""
    movements = [_movement(EnrollmentStatus.TRANSFERRED_OUT, date(2026, 9, 12))]
    assert movement_label(EnrollmentStatus.TRANSFERRED_OUT) == "Transferred Out"
    assert exit_status_line(movements) == "Transferred Out as of 09/12/2026"


# --- Most recent exit wins --------------------------------------------------


def test_the_most_recent_exit_movement_is_the_one_reported():
    """Left, came back, left again — the status that applies to a card
    printed today is the last one, not the first one on file."""
    movements = [
        _movement(EnrollmentStatus.DROPPED, date(2026, 8, 1), nls_reason="First reason"),
        _movement(EnrollmentStatus.TRANSFERRED_IN, date(2026, 8, 15)),  # not an exit
        _movement(EnrollmentStatus.NLS, date(2026, 9, 1), nls_reason="Second reason"),
    ]
    latest = latest_exit_movement(movements)
    assert latest.movement_type == EnrollmentStatus.NLS
    assert exit_status_line(movements) == "NLS as of 09/01/2026 due to Second reason"
