"""Parent notices (spec §78): the release policy, adviser overrides, and
the parent-meeting schedule. What was actually sent is step 3's table."""

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, VersionMixin


class ParentNoticePolicy(UUIDPKMixin, Base):
    """The most a learner may have and still have their term card released
    (§78.2), per school year. **Versioned, never edited in place**: a change
    is a new row with a higher `version`, and the highest one for the school
    year applies (rule 6) — the same shape as `grading_policy_versions`.
    """

    __tablename__ = "parent_notice_policies"
    __table_args__ = (
        UniqueConstraint("school_year_id", "version"),
        CheckConstraint(
            "max_absences >= 0 AND max_lates >= 0 AND max_cuttings >= 0",
            name="thresholds_not_negative",
        ),
    )

    school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    max_absences: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    max_lates: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    max_cuttings: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ParentNoticeOverride(UUIDPKMixin, TimestampMixin, VersionMixin, Base):
    """An adviser moving a learner between Release and Concern for one term,
    with the reason required (§78.3). One row per enrollment and term;
    clearing the override deletes the row, and both are audit-logged."""

    __tablename__ = "parent_notice_overrides"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "term_id"),
        CheckConstraint("decision IN ('RELEASE', 'CONCERN')", name="decision_valid"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_required"),
    )

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terms.id", ondelete="RESTRICT"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class ParentMeetingSchedule(UUIDPKMixin, TimestampMixin, VersionMixin, Base):
    """When a Concern learner's parent is asked to come in (§78.5).

    `enrollment_id` NULL is the section's schedule for the term, which every
    learner uses; a row with an enrollment is one learner's own. Date and
    time are stored **separately and naive**: they are a wall-clock
    appointment at the school, printed on a letter, and a timezone-aware
    timestamp would only invite a UTC host to move it by eight hours.
    """

    __tablename__ = "parent_meeting_schedules"
    __table_args__ = (
        Index(
            "uq_parent_meeting_section_term",
            "section_id",
            "term_id",
            unique=True,
            postgresql_where=text("enrollment_id IS NULL"),
        ),
        Index(
            "uq_parent_meeting_enrollment_term",
            "enrollment_id",
            "term_id",
            unique=True,
            postgresql_where=text("enrollment_id IS NOT NULL"),
        ),
    )

    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="RESTRICT"), nullable=False
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terms.id", ondelete="RESTRICT"), nullable=False
    )
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT")
    )
    meeting_date: Mapped[date] = mapped_column(Date, nullable=False)
    meeting_time: Mapped[time] = mapped_column(Time, nullable=False)
    set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
