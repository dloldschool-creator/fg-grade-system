"""The SF9's "Eligible for Admission to Grade" line needs a FINALIZED year.

Everything runs inside a session that is rolled back; nothing is committed.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.database import SessionLocal
from app.models.enums import (
    CompletionStatus,
    FinalizationRecordStatus,
    FinalizationScopeType,
)
from app.models.grades import AnnualGradeSummary, GradeFinalizationRecord
from app.sf9_report import (
    CELL_ELIGIBLE_FOR_GRADE,
    SHEET_NAME,
    build_sf9_workbook,
    eligible_for_next_level,
    finalized_enrollment_ids,
    load_sf9_context,
)
from app.models.learners import Enrollment


def _summary(complete=True, failed=0):
    return SimpleNamespace(
        completion_status=CompletionStatus.COMPLETE if complete else CompletionStatus.INCOMPLETE,
        failed_subject_count=failed,
    )


def test_eligibility_needs_a_finalized_complete_year_with_nothing_failed():
    assert eligible_for_next_level(_summary(), finalized=True) is True
    assert eligible_for_next_level(_summary(), finalized=False) is False  # not finalized
    assert eligible_for_next_level(_summary(complete=False), finalized=True) is False
    assert eligible_for_next_level(_summary(failed=1), finalized=True) is False
    assert eligible_for_next_level(None, finalized=True) is False


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _enrollment_with_summary(session):
    summary = session.query(AnnualGradeSummary).first()
    if summary is None:
        pytest.skip("no annual grade summary in the database")
    enrollment = session.get(Enrollment, summary.enrollment_id)
    session.query(GradeFinalizationRecord).filter_by(
        enrollment_id=enrollment.id, scope_type=FinalizationScopeType.ANNUAL_ENROLLMENT
    ).delete()
    # In-session only: the card reads these through the same transaction.
    summary.completion_status = CompletionStatus.COMPLETE
    summary.failed_subject_count = 0
    session.flush()
    return enrollment


def _record(session, enrollment, status, created_at):
    session.add(
        GradeFinalizationRecord(
            scope_type=FinalizationScopeType.ANNUAL_ENROLLMENT,
            enrollment_id=enrollment.id,
            finalized_at=created_at,
            status=status,
            created_at=created_at,
        )
    )
    session.flush()


def _eligible_cell(session, enrollment, *, with_context):
    context = load_sf9_context(session, [enrollment]) if with_context else None
    workbook = build_sf9_workbook(session, enrollment.id, context)
    return workbook[SHEET_NAME][CELL_ELIGIBLE_FOR_GRADE].value


@pytest.mark.parametrize("with_context", [False, True], ids=["single", "batch"])
def test_card_prints_eligibility_only_while_the_year_is_finalized(session, with_context):
    enrollment = _enrollment_with_summary(session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Complete, nothing failed, but never finalized: blank.
    assert not _eligible_cell(session, enrollment, with_context=with_context)

    # Finalized: the next level is printed.
    _record(session, enrollment, FinalizationRecordStatus.FINALIZED, now - timedelta(hours=2))
    assert finalized_enrollment_ids(session, [enrollment.id]) == {enrollment.id}
    assert _eligible_cell(session, enrollment, with_context=with_context)

    # Reopened afterwards (the newest record says REOPENED): blank again.
    _record(session, enrollment, FinalizationRecordStatus.REOPENED, now - timedelta(hours=1))
    assert finalized_enrollment_ids(session, [enrollment.id]) == set()
    assert not _eligible_cell(session, enrollment, with_context=with_context)

    # Re-finalized later still: printed again.
    _record(session, enrollment, FinalizationRecordStatus.FINALIZED, now)
    assert finalized_enrollment_ids(session, [enrollment.id]) == {enrollment.id}
    assert _eligible_cell(session, enrollment, with_context=with_context)
