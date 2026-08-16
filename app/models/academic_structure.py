import uuid

from sqlalchemy import Boolean, ForeignKey, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, VersionMixin


class GradeLevel(UUIDPKMixin, Base):
    __tablename__ = "grade_levels"

    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class Track(UUIDPKMixin, Base):
    __tablename__ = "tracks"

    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Strand(UUIDPKMixin, Base):
    """Replaces both `programs` and `strands` — see docs/schema.md §3."""

    __tablename__ = "strands"
    __table_args__ = (UniqueConstraint("track_id", "code"),)

    track_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tracks.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Section(UUIDPKMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "sections"
    # **An adviser may hold more than one section in a school year**, and
    # there is deliberately no constraint saying otherwise. There was one
    # (`uq_sections_adviser_per_school_year`, dropped 2026-08-16 in
    # revision `f8a3d05c1b27`) and it had no source: §3C says an adviser
    # sees learners in assigned *sections*, plural, and the SNED sections
    # are one adviser over two of them — same strand, same room, 5 and 7
    # learners. Every adviser lookup here returns a list already.
    #
    # The Sections page warns when an adviser already holds another
    # section this year, because catching the wrong name in a dropdown
    # was the one thing the index was genuinely good for.
    __table_args__ = (UniqueConstraint("school_year_id", "grade_level_id", "name"),)

    school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    grade_level_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grade_levels.id", ondelete="RESTRICT"), nullable=False
    )
    track_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tracks.id", ondelete="RESTRICT"), nullable=False
    )
    strand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strands.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    adviser_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    room: Mapped[str | None] = mapped_column(String)
    capacity: Mapped[int | None] = mapped_column(SmallInteger)
    display_order: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
