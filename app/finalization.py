"""Whether a learner's year is finalized.

One reading of the finalize/reopen records, shared by Grade Summary (which
also needs the record itself, to offer Reopen) and the SF9 (whose
"Eligible for Admission to Grade" line needs the year finalized). Two
copies would let the two pages disagree about the same learner.
"""

from sqlalchemy.orm import Session

from app.models.enums import FinalizationRecordStatus, FinalizationScopeType
from app.models.grades import GradeFinalizationRecord


def latest_annual_finalization_records(session: Session, enrollment_ids) -> dict:
    """`{enrollment_id: newest ANNUAL_ENROLLMENT record}` — FINALIZED or
    REOPENED. One query for any number of enrollments."""
    enrollment_ids = list(enrollment_ids)
    if not enrollment_ids:
        return {}
    latest: dict = {}
    for record in (
        session.query(GradeFinalizationRecord)
        .filter(
            GradeFinalizationRecord.scope_type == FinalizationScopeType.ANNUAL_ENROLLMENT,
            GradeFinalizationRecord.enrollment_id.in_(enrollment_ids),
        )
        .order_by(GradeFinalizationRecord.created_at.desc())
        .all()
    ):
        latest.setdefault(record.enrollment_id, record)
    return latest


def finalized_enrollment_ids(session: Session, enrollment_ids) -> set:
    """The enrollments whose year is currently FINALIZED: the newest annual
    record says FINALIZED, not REOPENED."""
    return {
        enrollment_id
        for enrollment_id, record in latest_annual_finalization_records(
            session, enrollment_ids
        ).items()
        if record.status == FinalizationRecordStatus.FINALIZED
    }
