import uuid

from sqlalchemy import Boolean, ForeignKey, Index, SmallInteger, String, UniqueConstraint, text
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
    __table_args__ = (
        UniqueConstraint("school_year_id", "grade_level_id", "name"),
        # An adviser is assigned to at most one section per school year —
        # scoped by year (not a bare global uniqueness on adviser_user_id)
        # so the same person can legitimately advise a different section
        # in a later year. Partial (WHERE ... IS NOT NULL) so an
        # unassigned section (adviser_user_id NULL) never collides.
        Index(
            "uq_sections_adviser_per_school_year",
            "school_year_id",
            "adviser_user_id",
            unique=True,
            postgresql_where=text("adviser_user_id IS NOT NULL"),
        ),
    )

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
