import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, VersionMixin
from app.models.enums import AttendanceStatus, FinalizationState


class AcademicCalendarDate(UUIDPKMixin, TimestampMixin, Base):
    """Per-date `term_id`, not inferred from month — required for the
    September term split (§29)."""

    __tablename__ = "academic_calendar_dates"
    __table_args__ = (UniqueConstraint("school_year_id", "calendar_date"),)

    school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terms.id", ondelete="RESTRICT")
    )
    calendar_date: Mapped[date] = mapped_column(Date, nullable=False)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_default_class_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_override: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_final_class_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    note: Mapped[str | None] = mapped_column(String)
    class_day_sequence: Mapped[int | None]
    overridden_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class AttendanceRecord(UUIDPKMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "attendance_records"
    __table_args__ = (UniqueConstraint("enrollment_id", "calendar_date_id"),)

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    calendar_date_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_calendar_dates.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[AttendanceStatus] = mapped_column(
        default=AttendanceStatus.PRESENT, server_default=AttendanceStatus.PRESENT.value
    )
    encoded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class AttendanceMonthStatus(UUIDPKMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "attendance_month_status"
    __table_args__ = (UniqueConstraint("section_id", "year_month"),)

    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="RESTRICT"), nullable=False
    )
    school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    year_month: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[FinalizationState] = mapped_column(
        default=FinalizationState.NOT_STARTED, server_default=FinalizationState.NOT_STARTED.value
    )
    finalized_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    finalized_at: Mapped[datetime | None]
    reopened_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reopened_at: Mapped[datetime | None]
    reopen_reason: Mapped[str | None] = mapped_column(String)
