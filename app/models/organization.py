import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin
from app.models.enums import FinalizationState, GradeEncodingStatus, SchoolYearStatus


class School(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "schools"

    school_name: Mapped[str] = mapped_column(String, nullable=False)
    deped_school_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    region: Mapped[str] = mapped_column(String, nullable=False)
    schools_division: Mapped[str] = mapped_column(String, nullable=False)
    district: Mapped[str] = mapped_column(String, nullable=False)
    address: Mapped[str] = mapped_column(String, nullable=False)
    school_head_name: Mapped[str] = mapped_column(String, nullable=False)
    school_head_position: Mapped[str] = mapped_column(String, nullable=False)

    school_years: Mapped[list["SchoolYear"]] = relationship(back_populates="school")


class SchoolYear(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "school_years"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    recognition_date: Mapped[date | None] = mapped_column(Date)
    recognition_venue: Mapped[str | None] = mapped_column(String)
    status: Mapped[SchoolYearStatus] = mapped_column(
        default=SchoolYearStatus.DRAFT, server_default=SchoolYearStatus.DRAFT.value
    )

    school: Mapped["School"] = relationship(back_populates="school_years")
    terms: Mapped[list["Term"]] = relationship(back_populates="school_year")


class Term(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "terms"
    __table_args__ = (UniqueConstraint("school_year_id", "term_number"),)

    school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    term_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    attendance_period_start: Mapped[date | None] = mapped_column(Date)
    attendance_period_end: Mapped[date | None] = mapped_column(Date)
    submission_deadline: Mapped[date | None] = mapped_column(Date)
    grade_encoding_status: Mapped[GradeEncodingStatus] = mapped_column(
        default=GradeEncodingStatus.CLOSED, server_default=GradeEncodingStatus.CLOSED.value
    )
    finalization_state: Mapped[FinalizationState] = mapped_column(
        default=FinalizationState.NOT_STARTED,
        server_default=FinalizationState.NOT_STARTED.value,
    )

    school_year: Mapped["SchoolYear"] = relationship(back_populates="terms")
