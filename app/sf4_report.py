"""SF4-SHS — Monthly Learners' Movement and Attendance (§77).

Fills `sf-templates/SF4-template.xlsx` and hands back a workbook. **Excel
only** (§77.3) — no PDF path, by decision: this one is submitted as a
file rather than printed, so the conversion would be dead weight.

**Why this form works with three terms when SF5 doesn't.** SF4 reports a
*month*: headcounts on the last class day, movements dated inside the
month, and attendance over the month's class days. None of that depends
on whether the year is divided into quarters, semesters or terms, so the
form is filled honestly without reinterpreting anything. The only
period-shaped field is "Semester", which carries the term the month falls
in.

**Layout** (rows are per Track/Strand, school-wide, not per section):

    rows 12-22  Grade 11, one row per track/strand in use
    row  23     TOTAL FOR GRADE 11
    rows 24-34  Grade 12
    row  35     TOTAL FOR GRADE 12
    row  36     GRAND TOTAL

Every figure is a Male / Female / Total triple.

**Performance.** The whole school is aggregated, so the data access is
deliberately flat: a fixed ~9 queries whether the school has 60 learners
or 6,000. Nothing here queries per learner — at ~85ms to Supabase that
would be minutes rather than seconds. `tests/test_query_cost.py` holds
the shape.
"""

import calendar as _calendar
import os
from dataclasses import dataclass, field
from datetime import date

import openpyxl
from sqlalchemy.orm import Session

from app.attendance_engine import Movement, compute_active_window
from app.attendance_service import class_days_in_month, records_for_month
from app.excel_template import anchor_map, workbook_to_bytes, write, write_ref
from app.models.academic_structure import GradeLevel, Section, Strand, Track
from app.models.attendance import AttendanceRecord
from app.models.enums import AttendanceStatus, EnrollmentStatus, Sex
from app.models.learners import Enrollment, Learner, LearnerMovement
from app.models.organization import School, SchoolYear, Term

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "sf-templates",
    "SF4-template.xlsx",
)
SHEET_NAME = "SHSF-4"

# --- Header ----------------------------------------------------------------
# Each label sits in its own cell with the value in the merged block that
# follows it.
CELL_SCHOOL_NAME = "C4"
CELL_DISTRICT = "P4"
CELL_DIVISION = "AC4"
CELL_REGION = "AS4"
CELL_SCHOOL_ID = "C6"
CELL_SEMESTER = "P6"
CELL_SCHOOL_YEAR = "AF6"
CELL_MONTH = "AW6"

# --- Grid ------------------------------------------------------------------
COL_TRACK = 1   # A
COL_STRAND = 2  # B

# Each of these is the first of a Male / Female / Total triple.
COL_REGISTERED = 3       # C-E, as of the end of the month
COL_DAILY_AVERAGE = 6    # F-H
COL_PERCENTAGE = 9       # I-K

# Each movement block is nine columns: (A) cumulative to the end of last
# month, (B) this month, then (A+B) — three M/F/T triples in that order.
MOVEMENT_COLUMNS = {
    EnrollmentStatus.DROPPED: 12,          # L
    EnrollmentStatus.TRANSFERRED_OUT: 21,  # U
    EnrollmentStatus.TRANSFERRED_IN: 30,   # AD
    EnrollmentStatus.SHIFTED_OUT: 39,      # AM
    EnrollmentStatus.SHIFTED_IN: 48,       # AV
}
MOVEMENT_TYPES = tuple(MOVEMENT_COLUMNS)

GRADE_BLOCKS = {
    11: (12, 22, 23),  # first data row, last data row, total row
    12: (24, 34, 35),
}
ROW_GRAND_TOTAL = 36
MAX_ROWS_PER_GRADE = 11

# LATE and CUTTING still count as days present — the learner was in
# school (§30). Same rule the attendance engine and SF2 use.
PRESENT_STATUSES = {
    AttendanceStatus.PRESENT,
    AttendanceStatus.LATE,
    AttendanceStatus.CUTTING,
}


@dataclass
class Tally:
    """One Male / Female / Total figure.

    Total defaults to male + female, which is right for counts and for a
    daily average (the average number present is the male average plus
    the female average). **It is wrong for a percentage** — 62.5% male
    and 100% female is not 162.5% overall — so a percentage sets
    `total_override` and the combined figure is recomputed from the
    underlying days. SF2 hit exactly this and reported 200%.
    """

    male: float = 0.0
    female: float = 0.0
    total_override: float | None = None

    @property
    def total(self) -> float:
        if self.total_override is not None:
            return self.total_override
        return self.male + self.female

    def add(self, sex, amount: float = 1) -> None:
        if sex == Sex.MALE:
            self.male += amount
        else:
            self.female += amount

    def merge(self, other: "Tally") -> None:
        self.male += other.male
        self.female += other.female


@dataclass
class Sf4Row:
    """One Track/Strand line."""

    grade_number: int
    track: str
    strand: str
    registered: Tally = field(default_factory=Tally)
    present_days: Tally = field(default_factory=Tally)
    eligible_days: Tally = field(default_factory=Tally)
    # movement type -> (before this month, during this month)
    movements: dict = field(default_factory=dict)

    def daily_average(self, class_day_count: int) -> Tally:
        """Average number of learners present on a class day."""
        if not class_day_count:
            return Tally()
        return Tally(
            male=self.present_days.male / class_day_count,
            female=self.present_days.female / class_day_count,
        )

    def percentage(self) -> Tally:
        """Attendance as a percentage of the days learners could attend.

        Measured against *eligible* days, not the section's calendar
        total — a learner who enrolled late or transferred out mid-month
        was never able to attend the rest (§31), and counting those
        against them understates the school's attendance.
        """
        def share(present: float, eligible: float) -> float:
            return (present / eligible * 100) if eligible else 0.0

        return Tally(
            male=share(self.present_days.male, self.eligible_days.male),
            female=share(self.present_days.female, self.eligible_days.female),
            # Recomputed, never summed — see Tally.
            total_override=share(self.present_days.total, self.eligible_days.total),
        )

    def movement(self, movement_type) -> tuple[Tally, Tally]:
        return self.movements.setdefault(movement_type, (Tally(), Tally()))


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, _calendar.monthrange(year, month)[1])


def term_for_month(terms, year: int, month: int):
    """The term a reporting month falls in.

    SF4's only period-shaped field is "Semester". The school runs three
    terms, and the form is not reinterpreted to fit two — the term that
    actually contains the month is named instead.
    """
    first, last = month_bounds(year, month)
    for term in terms:
        if term.start_date and term.end_date and term.start_date <= last and term.end_date >= first:
            return term
    return None


def aggregate(
    *,
    enrollments,
    learners,
    sections,
    tracks,
    strands,
    grade_levels,
    movements,
    class_days,
    attendance,
    school_year_start,
    year: int,
    month: int,
) -> list[Sf4Row]:
    """Builds the Track/Strand rows from already-fetched data.

    Takes plain collections rather than a session so the counting rules
    are testable without a database — the same split the grading and
    attendance engines use.
    """
    first_day, last_day = month_bounds(year, month)
    class_dates = [d.calendar_date for d in class_days]
    day_by_id = {d.id: d.calendar_date for d in class_days}

    movements_by_enrollment: dict = {}
    for movement in movements:
        movements_by_enrollment.setdefault(movement.enrollment_id, []).append(movement)

    rows: dict[tuple, Sf4Row] = {}

    def row_for(enrollment) -> Sf4Row | None:
        section = sections.get(enrollment.section_id)
        if section is None:
            return None
        grade_level = grade_levels.get(enrollment.grade_level_id or section.grade_level_id)
        grade_number = _grade_number(grade_level)
        if grade_number not in GRADE_BLOCKS:
            return None
        track = tracks.get(section.track_id)
        strand = strands.get(section.strand_id)
        key = (grade_number, track.name if track else "", strand.name if strand else "")
        return rows.setdefault(key, Sf4Row(grade_number, key[1], key[2]))

    for enrollment in enrollments:
        learner = learners.get(enrollment.learner_id)
        if learner is None:
            continue
        row = row_for(enrollment)
        if row is None:
            continue

        entries = movements_by_enrollment.get(enrollment.id, [])
        window = compute_active_window(
            [Movement(m.movement_type, m.effective_date) for m in entries],
            default_start=school_year_start,
        )

        # Registered as of the end of the month: still on the roll on the
        # last class day. Using the last *class* day rather than the last
        # calendar day avoids counting someone who left over a weekend.
        if class_dates and window.contains(class_dates[-1]):
            row.registered.add(learner.sex)

        for day in class_days:
            if not window.contains(day_by_id[day.id]):
                continue
            row.eligible_days.add(learner.sex)
            record = attendance.get((enrollment.id, day.id))
            if record is not None and record.status in PRESENT_STATUSES:
                row.present_days.add(learner.sex)

        for movement in entries:
            if movement.movement_type not in MOVEMENT_COLUMNS:
                continue
            if movement.effective_date is None:
                continue
            before, during = row.movement(movement.movement_type)
            if movement.effective_date < first_day:
                before.add(learner.sex)
            elif movement.effective_date <= last_day:
                during.add(learner.sex)

    return sorted(rows.values(), key=lambda r: (r.grade_number, r.track, r.strand))


def _grade_number(grade_level) -> int | None:
    if grade_level is None:
        return None
    digits = "".join(c for c in str(grade_level.code or grade_level.name or "") if c.isdigit())
    return int(digits) if digits else None


# --- Workbook --------------------------------------------------------------


def _write_triple(worksheet, anchors, row: int, first_col: int, tally: Tally, decimals=0) -> None:
    values = (tally.male, tally.female, tally.total)
    for offset, value in enumerate(values):
        write(
            worksheet, anchors, row, first_col + offset,
            round(value, decimals) if decimals else int(round(value)),
        )


def _write_row(worksheet, anchors, row_number: int, entry: Sf4Row, class_day_count: int) -> None:
    write(worksheet, anchors, row_number, COL_TRACK, entry.track or None)
    write(worksheet, anchors, row_number, COL_STRAND, entry.strand or None)
    _write_triple(worksheet, anchors, row_number, COL_REGISTERED, entry.registered)
    _write_triple(worksheet, anchors, row_number, COL_DAILY_AVERAGE,
                  entry.daily_average(class_day_count), decimals=2)
    _write_triple(worksheet, anchors, row_number, COL_PERCENTAGE, entry.percentage(), decimals=2)

    for movement_type, base in MOVEMENT_COLUMNS.items():
        before, during = entry.movement(movement_type)
        cumulative = Tally(before.male + during.male, before.female + during.female)
        _write_triple(worksheet, anchors, row_number, base, before)
        _write_triple(worksheet, anchors, row_number, base + 3, during)
        _write_triple(worksheet, anchors, row_number, base + 6, cumulative)


def _combine(entries: list[Sf4Row], grade_number: int = 0) -> Sf4Row:
    combined = Sf4Row(grade_number, "", "")
    for entry in entries:
        combined.registered.merge(entry.registered)
        combined.present_days.merge(entry.present_days)
        combined.eligible_days.merge(entry.eligible_days)
        for movement_type in MOVEMENT_TYPES:
            before, during = entry.movement(movement_type)
            total_before, total_during = combined.movement(movement_type)
            total_before.merge(before)
            total_during.merge(during)
    return combined


def build_sf4_workbook(session: Session, school_year_id, year: int, month: int):
    """Returns a filled openpyxl Workbook for one reporting month."""
    school_year = session.get(SchoolYear, school_year_id)
    school = session.query(School).one_or_none()
    terms = session.query(Term).filter_by(school_year_id=school_year_id).order_by(Term.term_number).all()

    section_rows = session.query(Section).filter_by(school_year_id=school_year_id).all()
    sections = {s.id: s for s in section_rows}
    tracks = {t.id: t for t in session.query(Track).all()}
    strands = {s.id: s for s in session.query(Strand).all()}
    grade_levels = {g.id: g for g in session.query(GradeLevel).all()}

    enrollments = session.query(Enrollment).filter_by(school_year_id=school_year_id).all()
    enrollment_ids = [e.id for e in enrollments]
    learners = {
        learner.id: learner
        for learner in session.query(Learner)
        .filter(Learner.id.in_({e.learner_id for e in enrollments})).all()
    } if enrollments else {}
    movements = (
        session.query(LearnerMovement)
        .filter(LearnerMovement.enrollment_id.in_(enrollment_ids)).all()
        if enrollment_ids else []
    )

    class_days = class_days_in_month(session, school_year_id, year, month)
    attendance = records_for_month(session, enrollment_ids, [d.id for d in class_days])

    rows = aggregate(
        enrollments=enrollments,
        learners=learners,
        sections=sections,
        tracks=tracks,
        strands=strands,
        grade_levels=grade_levels,
        movements=movements,
        class_days=class_days,
        attendance=attendance,
        school_year_start=school_year.start_date if school_year else None,
        year=year,
        month=month,
    )

    for grade_number, (first_row, last_row, _total_row) in GRADE_BLOCKS.items():
        count = sum(1 for r in rows if r.grade_number == grade_number)
        capacity = last_row - first_row + 1
        if count > capacity:
            raise ValueError(
                f"Grade {grade_number} has {count} track/strand combinations but the "
                f"SF4 template provides {capacity} rows."
            )

    workbook = openpyxl.load_workbook(TEMPLATE_PATH)
    worksheet = workbook[SHEET_NAME]
    anchors = anchor_map(worksheet)

    term = term_for_month(terms, year, month)
    write_ref(worksheet, anchors, CELL_SCHOOL_NAME, school.school_name if school else None)
    write_ref(worksheet, anchors, CELL_SCHOOL_ID, school.deped_school_id if school else None)
    write_ref(worksheet, anchors, CELL_DISTRICT, school.district if school else None)
    write_ref(worksheet, anchors, CELL_DIVISION, school.schools_division if school else None)
    write_ref(worksheet, anchors, CELL_REGION, school.region if school else None)
    write_ref(worksheet, anchors, CELL_SEMESTER, term.name if term else None)
    write_ref(worksheet, anchors, CELL_SCHOOL_YEAR, school_year.name if school_year else None)
    write_ref(worksheet, anchors, CELL_MONTH, f"{_calendar.month_name[month]} {year}")

    class_day_count = len(class_days)
    for grade_number, (first_row, last_row, total_row) in GRADE_BLOCKS.items():
        block = [r for r in rows if r.grade_number == grade_number]
        for index in range(first_row, last_row + 1):
            position = index - first_row
            if position < len(block):
                _write_row(worksheet, anchors, index, block[position], class_day_count)
            else:
                _clear_row(worksheet, anchors, index)
        _write_row(worksheet, anchors, total_row, _combine(block, grade_number), class_day_count)

    _write_row(worksheet, anchors, ROW_GRAND_TOTAL, _combine(rows), class_day_count)
    return workbook


def _clear_row(worksheet, anchors, row_number: int) -> None:
    """An unused row prints blank rather than as a row of zeros — a zero
    is a reported figure, an empty cell is not."""
    write(worksheet, anchors, row_number, COL_TRACK, None)
    write(worksheet, anchors, row_number, COL_STRAND, None)
    for first_col in (COL_REGISTERED, COL_DAILY_AVERAGE, COL_PERCENTAGE):
        for offset in range(3):
            write(worksheet, anchors, row_number, first_col + offset, None)
    for base in MOVEMENT_COLUMNS.values():
        for offset in range(9):
            write(worksheet, anchors, row_number, base + offset, None)


__all__ = [
    "TEMPLATE_PATH",
    "SHEET_NAME",
    "Sf4Row",
    "Tally",
    "aggregate",
    "build_sf4_workbook",
    "month_bounds",
    "term_for_month",
    "workbook_to_bytes",
]
