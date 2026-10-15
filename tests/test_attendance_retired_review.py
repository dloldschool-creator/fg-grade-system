"""Spec §33 amended 2026-10-15: FOR REVIEW was retired. The enum value stays
in the database, so months already marked must keep working as open ones."""

import pytest

from app.admin_pages.attendance import EDITABLE_STATES
from app.attendance_service import display_state, finalize_month
from app.models.attendance import AttendanceMonthStatus
from app.models.enums import FinalizationState


def test_a_month_marked_for_review_is_still_editable():
    assert FinalizationState.FOR_REVIEW in EDITABLE_STATES


@pytest.mark.parametrize(
    "state, shown",
    [
        (FinalizationState.FOR_REVIEW, "OPEN"),
        (FinalizationState.OPEN, "OPEN"),
        (FinalizationState.NOT_STARTED, "NOT_STARTED"),
        (FinalizationState.FINALIZED, "FINALIZED"),
    ],
)
def test_display_state_hides_the_retired_state(state, shown):
    assert display_state(state) == shown


def test_finalize_month_finalizes_a_new_unsaved_month():
    status = AttendanceMonthStatus(status=FinalizationState.NOT_STARTED, version=1)
    finalize_month(None, status, None)
    assert status.status == FinalizationState.FINALIZED


def test_finalize_month_refuses_one_already_finalized():
    status = AttendanceMonthStatus(status=FinalizationState.FINALIZED, version=1)
    with pytest.raises(ValueError):
        finalize_month(None, status, None)
