import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Numeric, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, VersionMixin
from app.models.enums import (
    AveragingMethod,
    CompletionStatus,
    FinalizationRecordStatus,
    FinalizationScopeType,
    GradeWorkflowStatus,
    SubjectRemark,
)


class TermGrade(UUIDPKMixin, TimestampMixin, VersionMixin, Base):
    """The central mutable grade record. Grade entry is direct-only (Mode
    B, confirmed) — `official_grade` is the one number a teacher types in
    per learner per term."""

    __tablename__ = "term_grades"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "section_subject_offering_id", "term_id"),
    )

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    section_subject_offering_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("section_subject_offerings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terms.id", ondelete="RESTRICT"), nullable=False
    )
    official_grade: Mapped[float | None] = mapped_column(Numeric(5, 2))
    grading_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grading_policy_versions.id", ondelete="RESTRICT")
    )
    source: Mapped[str | None] = mapped_column(String)
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_jobs.id", ondelete="SET NULL")
    )
    remarks: Mapped[str | None] = mapped_column(String)
    status: Mapped[GradeWorkflowStatus] = mapped_column(
        default=GradeWorkflowStatus.DRAFT, server_default=GradeWorkflowStatus.DRAFT.value
    )
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    submitted_at: Mapped[datetime | None]
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None]
    finalized_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    finalized_at: Mapped[datetime | None]


class SubjectFinalGrade(UUIDPKMixin, Base):
    """Derived/cached per subject per school year — recomputed from
    `term_grades`, never entered directly."""

    __tablename__ = "subject_final_grades"
    __table_args__ = (UniqueConstraint("enrollment_id", "subject_id", "school_year_id"),)

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False
    )
    school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    final_grade: Mapped[float | None] = mapped_column(Numeric(5, 2))
    # What this subject contributed to the General Average (DO 017 s. 2026,
    # Annex E): its per-term units, the annual units those became once
    # multiplied by the terms it actually ran, and the unrounded final that
    # the weighting used. `final_grade` stays the reported whole number.
    units_per_term: Mapped[float | None] = mapped_column(Numeric(5, 2))
    units: Mapped[float | None] = mapped_column(Numeric(7, 2))
    unrounded_final_grade: Mapped[float | None] = mapped_column(Numeric(9, 4))
    remark: Mapped[SubjectRemark | None]
    computed_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class CombinedLearningAreaResult(UUIDPKMixin, Base):
    """Grade 11 combined-language results (§15-16) — what's actually
    displayed/counted for the combined parent row on SF9 and in the GA."""

    __tablename__ = "combined_learning_area_results"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "combined_learning_area_id", "school_year_id"),
    )

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    combined_learning_area_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("combined_learning_areas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    term1_combined: Mapped[float | None] = mapped_column(Numeric(5, 2))
    term2_combined: Mapped[float | None] = mapped_column(Numeric(5, 2))
    term3_combined: Mapped[float | None] = mapped_column(Numeric(5, 2))
    final_grade: Mapped[float | None] = mapped_column(Numeric(5, 2))
    # The pair's weight as ONE learning area — deliberately not the sum of
    # its two components, which would double-count the languages (§19).
    units_per_term: Mapped[float | None] = mapped_column(Numeric(5, 2))
    units: Mapped[float | None] = mapped_column(Numeric(7, 2))
    unrounded_final_grade: Mapped[float | None] = mapped_column(Numeric(9, 4))
    remark: Mapped[SubjectRemark | None]
    computed_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class TermGradeSummary(UUIDPKMixin, Base):
    """Per-term aggregate (§17 Term Average, §22 Term Completion Check) —
    the term-level counterpart of `AnnualGradeSummary`, and what a
    TERM-scoped award policy is judged against.

    `term_average` counts the Grade 11 combined-language components as **two
    separate subjects** (§17 is explicit: "Do not substitute the combined
    language grade when calculating the Term Average"). That's the opposite
    of the General Average rule, where the pair collapses into one virtual
    learning area — the single most common way to get this wrong. DO 017
    s. 2026 reads it the other way, so which applies is now a policy switch
    (`grading_policy_versions.combine_language_pair_in_term_average`), and
    §17 remains the default.

    `averaging_method` and `total_units` record how the stored average was
    actually reached. Without them a 2 that should have been a 12 is
    invisible — the average is simply a slightly different plausible number.
    """

    __tablename__ = "term_grade_summaries"
    __table_args__ = (UniqueConstraint("enrollment_id", "term_id"),)

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terms.id", ondelete="RESTRICT"), nullable=False
    )
    term_average: Mapped[float | None] = mapped_column(Numeric(5, 2))
    # How the average above was reached, so the number can be explained and
    # a mis-set unit is visible rather than silent (DO 017 s. 2026, Annex E).
    averaging_method: Mapped[AveragingMethod] = mapped_column(
        default=AveragingMethod.UNWEIGHTED, server_default=AveragingMethod.UNWEIGHTED.value
    )
    total_units: Mapped[float | None] = mapped_column(Numeric(7, 2))
    lowest_term_grade: Mapped[float | None] = mapped_column(Numeric(5, 2))
    failed_subject_count: Mapped[int | None] = mapped_column(SmallInteger)
    completion_status: Mapped[CompletionStatus] = mapped_column(
        default=CompletionStatus.INCOMPLETE, server_default=CompletionStatus.INCOMPLETE.value
    )
    computed_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class AnnualGradeSummary(UUIDPKMixin, Base):
    __tablename__ = "annual_grade_summaries"

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    general_average: Mapped[float | None] = mapped_column(Numeric(5, 2))
    averaging_method: Mapped[AveragingMethod] = mapped_column(
        default=AveragingMethod.UNWEIGHTED, server_default=AveragingMethod.UNWEIGHTED.value
    )
    total_units: Mapped[float | None] = mapped_column(Numeric(7, 2))
    lowest_final_grade: Mapped[float | None] = mapped_column(Numeric(5, 2))
    failed_subject_count: Mapped[int | None] = mapped_column(SmallInteger)
    completion_status: Mapped[CompletionStatus] = mapped_column(
        default=CompletionStatus.INCOMPLETE, server_default=CompletionStatus.INCOMPLETE.value
    )
    computed_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class GradeFinalizationRecord(UUIDPKMixin, Base):
    """Finalize/reopen workflow gate — distinct from `audit_logs` because
    it's what the app *checks* to allow/deny edits."""

    __tablename__ = "grade_finalization_records"

    scope_type: Mapped[FinalizationScopeType] = mapped_column(nullable=False)
    term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terms.id", ondelete="RESTRICT")
    )
    section_subject_offering_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("section_subject_offerings.id", ondelete="RESTRICT")
    )
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT")
    )
    finalized_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    finalized_at: Mapped[datetime] = mapped_column(nullable=False)
    reopened_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reopened_at: Mapped[datetime | None]
    reopen_reason: Mapped[str | None] = mapped_column(String)
    status: Mapped[FinalizationRecordStatus] = mapped_column(
        default=FinalizationRecordStatus.FINALIZED,
        server_default=FinalizationRecordStatus.FINALIZED.value,
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
