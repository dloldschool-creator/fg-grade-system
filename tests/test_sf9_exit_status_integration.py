"""End-to-end check that a learner's exit status (§35 amendment,
2026-09-05) actually reaches the generated SF9 — through both the
single-card path and the batched `Sf9BatchContext` path, since
`build_sf9_workbook` resolves the learner's movements differently in each.

Runs against the real database like `tests/test_query_cost.py` — writes a
`LearnerMovement` row, flushes, reads the workbook back, then rolls back so
nothing reaches the school's data.
"""

from datetime import date

import openpyxl
import pytest

from app.database import SessionLocal
from app.models.academic_structure import Section
from app.models.enums import EnrollmentStatus
from app.models.learners import Enrollment, LearnerMovement
from app.models.organization import SchoolYear
from app.sf9_report import COL_REMARKS, LEARNING_AREA_FIRST_ROW, build_sf9_workbook, load_sf9_context


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def roster(session):
    school_year = session.query(SchoolYear).first()
    if school_year is None:
        pytest.skip("no school year in the database")
    section = session.query(Section).filter_by(school_year_id=school_year.id).first()
    if section is None:
        pytest.skip("no section in the database")
    enrollments = (
        session.query(Enrollment)
        .filter_by(section_id=section.id, school_year_id=school_year.id)
        .all()
    )
    if not enrollments:
        pytest.skip("section has no enrolled learners")
    return enrollments


def _remarks_cell(workbook):
    worksheet = workbook["SF9"]
    return worksheet.cell(LEARNING_AREA_FIRST_ROW, COL_REMARKS).value


def test_single_card_path_prints_the_exit_status(session, roster):
    enrollment = roster[0]
    session.add(
        LearnerMovement(
            enrollment_id=enrollment.id,
            movement_type=EnrollmentStatus.DROPPED,
            effective_date=date(2026, 8, 30),
            nls_reason="Child labor, work",
        )
    )
    session.flush()

    workbook = build_sf9_workbook(session, enrollment.id)
    assert _remarks_cell(workbook) == "Dropped as of 08/30/2026 due to Child labor, work"


def test_batched_path_agrees_with_the_single_card_path(session, roster):
    """`context.movements` is a separate data path from the single-card
    query — this is the test that would catch the batch context simply
    not carrying movements through."""
    enrollment = roster[0]
    session.add(
        LearnerMovement(
            enrollment_id=enrollment.id,
            movement_type=EnrollmentStatus.NLS,
            effective_date=date(2026, 9, 1),
            details="Family relocated",
        )
    )
    session.flush()

    single = build_sf9_workbook(session, enrollment.id)
    context = load_sf9_context(session, roster)
    batched = build_sf9_workbook(session, enrollment.id, context)

    assert _remarks_cell(single) == _remarks_cell(batched) == "NLS as of 09/01/2026 due to Family relocated"


def test_a_learner_with_no_exit_movement_keeps_ordinary_per_row_remarks(session, roster):
    """The common case must be untouched: no movement means no merge, and
    each row still prints its own PASSED/FAILED/INCOMPLETE."""
    enrollment = roster[0]
    workbook = build_sf9_workbook(session, enrollment.id)
    worksheet = workbook["SF9"]
    # Not every seeded learner has a computed remark, but the cell must not
    # have been overwritten with an exit-status sentence either.
    value = worksheet.cell(LEARNING_AREA_FIRST_ROW, COL_REMARKS).value
    assert value in (None, "PASSED", "FAILED", "INCOMPLETE")
