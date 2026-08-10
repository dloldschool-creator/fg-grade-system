import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, VersionMixin
from app.models.enums import EnrollmentStatus, Sex


class Learner(UUIDPKMixin, TimestampMixin, Base):
    """Stable identity, independent of any single year's enrollment."""

    __tablename__ = "learners"
    __table_args__ = (
        CheckConstraint("lrn IS NULL OR lrn ~ '^[0-9]{12}$'", name="lrn_format"),
        Index("uq_learners_lrn", "lrn", unique=True, postgresql_where=text("lrn IS NOT NULL")),
    )

    lrn: Mapped[str | None] = mapped_column(String(12))
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String)
    extension_name: Mapped[str | None] = mapped_column(String)
    sex: Mapped[Sex] = mapped_column(nullable=False)
    birthdate: Mapped[date] = mapped_column(Date, nullable=False)


class LearnerAdmissionRecord(UUIDPKMixin, TimestampMixin, Base):
    """SHS-entry eligibility fields (§25) — one per learner, describing a
    single admission event rather than a per-year fact."""

    __tablename__ = "learner_admission_records"

    learner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learners.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    date_of_shs_admission: Mapped[date | None] = mapped_column(Date)
    high_school_completer: Mapped[bool | None] = mapped_column(Boolean)
    high_school_general_average: Mapped[float | None] = mapped_column(Numeric(5, 2))
    high_school_completion_date: Mapped[date | None] = mapped_column(Date)
    junior_high_school_completer: Mapped[bool | None] = mapped_column(Boolean)
    junior_high_school_general_average: Mapped[float | None] = mapped_column(Numeric(5, 2))
    previous_school_name: Mapped[str | None] = mapped_column(String)
    previous_school_address: Mapped[str | None] = mapped_column(String)
    pept_passer: Mapped[bool | None] = mapped_column(Boolean)
    pept_rating: Mapped[float | None] = mapped_column(Numeric(5, 2))
    pept_examination_date: Mapped[date | None] = mapped_column(Date)
    als_ae_passer: Mapped[bool | None] = mapped_column(Boolean)
    als_ae_rating: Mapped[float | None] = mapped_column(Numeric(5, 2))
    als_ae_examination_date: Mapped[date | None] = mapped_column(Date)
    clc_name: Mapped[str | None] = mapped_column(String)
    clc_address: Mapped[str | None] = mapped_column(String)
    other_eligibility_notes: Mapped[str | None] = mapped_column(String)


class Enrollment(UUIDPKMixin, TimestampMixin, VersionMixin, Base):
    """One row per learner per school year. `track_id`/`strand_id` are not
    duplicated here — read via `section_id -> sections.track_id/strand_id`."""

    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("learner_id", "school_year_id"),)

    learner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learners.id", ondelete="RESTRICT"), nullable=False
    )
    school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    grade_level_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grade_levels.id", ondelete="RESTRICT"), nullable=False
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="RESTRICT"), nullable=False
    )
    enrollment_status: Mapped[EnrollmentStatus] = mapped_column(
        default=EnrollmentStatus.ENROLLED, server_default=EnrollmentStatus.ENROLLED.value
    )
    derogatory_record: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    general_remarks: Mapped[str | None] = mapped_column(String)
    term1_adviser_comment: Mapped[str | None] = mapped_column(String)
    term2_adviser_comment: Mapped[str | None] = mapped_column(String)
    term3_adviser_comment: Mapped[str | None] = mapped_column(String)


class LearnerMovement(UUIDPKMixin, Base):
    """**Merged table** — see docs/schema.md §5 for why
    `learner_status_history` and `learner_movements` are one table here."""

    __tablename__ = "learner_movements"

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    movement_type: Mapped[EnrollmentStatus] = mapped_column(nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    details: Mapped[str | None] = mapped_column(String)
    previous_school: Mapped[str | None] = mapped_column(String)
    receiving_school: Mapped[str | None] = mapped_column(String)
    nls_reason: Mapped[str | None] = mapped_column(String)
    remarks: Mapped[str | None] = mapped_column(String)
    encoded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default="now()")
