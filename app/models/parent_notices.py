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


class ParentNotification(UUIDPKMixin, TimestampMixin, Base):
    """Every notice that went to a parent, or was attempted (§78.6).

    **The double-send guard is the partial unique index**, not page logic:
    at most one PENDING-or-SENT term-card email per learner and term. The
    sender inserts a PENDING row and commits it *before* talking to the
    mail server, so a second press, a second user or a rerun after a crash
    loses the race at the database and skips that learner. A row stuck in
    PENDING means the process died mid-send; it is shown as "may have
    gone out" rather than retried silently.

    `basis_at` is when the grades or attendance the card rests on last
    changed, as of sending. A later change makes the sent card outdated
    (§78.4); re-sending marks the old row SUPERSEDED, freeing the slot.
    """

    __tablename__ = "parent_notifications"
    __table_args__ = (
        CheckConstraint("kind IN ('TERM_CARD', 'CONCERN')", name="kind_valid"),
        CheckConstraint("channel IN ('EMAIL', 'SMS', 'LETTER')", name="channel_valid"),
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED', 'SUPERSEDED')", name="status_valid"
        ),
        Index(
            "uq_parent_notifications_one_live_email",
            "enrollment_id",
            "term_id",
            "kind",
            unique=True,
            postgresql_where=text("channel = 'EMAIL' AND status IN ('PENDING', 'SENT')"),
        ),
        Index("ix_parent_notifications_enrollment_term", "enrollment_id", "term_id"),
    )

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terms.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    recipient: Mapped[str | None] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(String)
    basis_at: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]
    sent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
