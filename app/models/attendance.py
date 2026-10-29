import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Integer, SmallInteger, String, UniqueConstraint
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
    """One day that says something other than a plain save of P: an X,
    T-L or T-C, or a day corrected back to P (kept so its audit entries
    still point at a row). A saved P needs no row — see
    `AttendanceEncodedDays`. Rows written as P before 2026-10-29 are still
    valid and read the same."""

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


class AttendanceEncodedDays(UUIDPKMixin, TimestampMixin, VersionMixin, Base):
    """Which days of one month have been encoded for one enrollment.

    `days` is a bitmask: bit (d - 1) is set when day d of `year_month` has
    been saved. A set bit with no `AttendanceRecord` is a **P**; a clear
    bit with no record is "not yet encoded" (§33, rule 2). This replaced
    one PRESENT row per learner per day, which was 96% of
    `attendance_records` — one row here stands for a whole month.

    Read through `attendance_service.statuses_for_days`, never directly:
    a day is encoded if its bit is set *or* it has a record, and the
    status is the record's when there is one. Written only with bitwise
    `|` and `& ~` in SQL, so two saves of one month cannot overwrite each
    other's bits (rule 9) — `version` still counts the changes.
    """

    __tablename__ = "attendance_encoded_days"
    __table_args__ = (UniqueConstraint("enrollment_id", "year_month"),)

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    # First of the month, like attendance_month_status.year_month.
    year_month: Mapped[date] = mapped_column(Date, nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_saved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
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
    # §33: someone confirmed the SF2/SF4 was reprinted after a reopen. The
    # "reprint" flag is pending while reopened_at is later than this, so a
    # second reopen revives it with nothing to reset.
    reprint_acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reprint_acknowledged_at: Mapped[datetime | None]
    # §33: something outside the attendance grid changed this month's
    # SF2/SF4 after it was finalized (a calendar day flipped, a movement
    # logged or removed) — app/finalized_month_changes.py. Counts toward the
    # reprint flag alongside reopened_at; kept apart from it so reopen_reason
    # only ever describes a reopen.
    changed_after_finalize_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    changed_after_finalize_at: Mapped[datetime | None]
    change_after_finalize_note: Mapped[str | None] = mapped_column(String)
    # Stamped by _save_grid whenever a save actually changes something —
    # not just "the month was touched" (updated_at already covers that,
    # ambiguously, across Finalize/Reopen too).
    # The finalization panel compares this against the audit log's most
    # recent ATTENDANCE_CHANGED entry to warn about an override that
    # landed after the last save, from any adviser or tab.
    last_saved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    last_saved_at: Mapped[datetime | None]
