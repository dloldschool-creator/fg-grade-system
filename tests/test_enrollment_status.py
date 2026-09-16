"""Tests for app/enrollment_status.py — the shared movement vocabulary
behind SF2's Remarks column and SF9's exit-status line (§35 amendment,
2026-09-05)."""

from datetime import date

from app.enrollment_status import (
    EXIT_MOVEMENT_TYPES,
    exit_status_line,
    latest_exit_movement,
    movement_label,
    movement_reason_text,
    movement_status_line,
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


# --- The NLS/Dropped legend's main cause + sub-reason ----------------------


def test_reason_joins_main_cause_and_sub_reason_with_a_dash():
    """The shape the Log Movement form's two dropdowns write (§32,
    app/nls_reasons.py): nls_reason holds the main cause, details holds
    the sub-reason picked under it."""
    movement = _movement(
        EnrollmentStatus.DROPPED,
        date(2026, 8, 5),
        nls_reason="Financial-Related",
        details="Child labor, work",
    )
    assert movement_reason_text(movement) == "Financial-Related - Child labor, work"
    assert (
        movement_status_line(movement)
        == "Dropped as of 08/05/2026 due to Financial-Related - Child labor, work"
    )


def test_reason_is_just_the_main_cause_when_no_sub_reason_is_on_file():
    """"Others (Specify)" with a blank free-text box, or a movement logged
    before the dropdowns existed with only the old single free-text field
    filled in."""
    movement = _movement(EnrollmentStatus.NLS, date(2026, 8, 5), nls_reason="Others")
    assert movement_reason_text(movement) == "Others"


# --- Transferred In/Out name the school, not a reason ----------------------


def test_transferred_in_prints_the_previous_school():
    movement = _movement(
        EnrollmentStatus.TRANSFERRED_IN, date(2026, 9, 1), previous_school="Rizal NHS"
    )
    assert movement_status_line(movement) == "Transferred In as of 09/01/2026 from Rizal NHS"


def test_transferred_out_prints_the_receiving_school():
    movement = _movement(
        EnrollmentStatus.TRANSFERRED_OUT, date(2026, 9, 12), receiving_school="Bonifacio NHS"
    )
    assert movement_status_line(movement) == "Transferred Out as of 09/12/2026 to Bonifacio NHS"


def test_transferred_in_ignores_a_receiving_school_left_over_from_another_pick():
    """The Log Movement form disables the inapplicable field but a stray
    value there must never leak into the printed line — this is the
    consequence of that if the form-level guard were ever removed."""
    movement = _movement(
        EnrollmentStatus.TRANSFERRED_IN,
        date(2026, 9, 1),
        previous_school="Rizal NHS",
        receiving_school="Should not print",
    )
    assert movement_status_line(movement) == "Transferred In as of 09/01/2026 from Rizal NHS"


def test_transferred_in_without_a_previous_school_still_names_status_and_date():
    movement = _movement(EnrollmentStatus.TRANSFERRED_IN, date(2026, 9, 1))
    assert movement_status_line(movement) == "Transferred In as of 09/01/2026"
