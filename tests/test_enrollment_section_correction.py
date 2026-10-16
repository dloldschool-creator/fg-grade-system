"""Correcting a learner enrolled in the wrong section.

Nothing else in the app can fix it: `enrollments` is unique on (learner,
school year), and a Shifted movement would put a typing mistake on SF2 and
SF4. The fix has two opposite ways to go wrong, and this file covers both:

- **Too strict.** Attendance seeding writes a PRESENT row for every learner
  the moment a month is prepared, and Recompute writes summaries for every
  learner. Blocking on either refused nearly every real correction.
- **Too loose.** Grades point at the old section's offerings. Moving them
  silently cuts them off the report card, so they still block — the school
  chose on 2026-10-14 to keep refusing rather than carry grades across.

The database tests write, flush, assert and roll back; the fixture never
commits, and `correct_section` does not commit either.
"""

import ast
import inspect
import textwrap
from datetime import date

import pytest

from app import enrollment_section_correction as svc
from app.admin_pages import enrollment as page
from app.enrollment_section_correction import CorrectionRefused, correct_section


# --- Structure: the page and the rule ---------------------------------------


def test_blocking_is_the_default_for_any_table_pointing_at_enrollments():
    """A table added later must block the move the day it exists; only the
    tables named as following or derived are let through."""
    blocking = {t.name for t, _ in svc._blocking_tables()}
    every = {t.name for t, _ in svc._enrollment_fks()}
    assert blocking == (every - svc.FOLLOWS - svc.DERIVED) | {"learner_awards"}
    for expected in ("term_grades", "enrollment_subject_overrides", "learner_academic_records"):
        assert expected in blocking


def test_attendance_and_movements_follow_the_learner():
    """Attendance has no section column — the rows simply appear on the new
    section's sheet. Blocking on them is what made the first version
    refuse almost every real case."""
    blocking = {t.name for t, _ in svc._blocking_tables()}
    assert "attendance_records" not in blocking
    assert "learner_movements" not in blocking
    from app.models.attendance import AttendanceRecord

    assert "section_id" not in AttendanceRecord.__table__.columns


def test_only_recomputed_outputs_are_treated_as_derived():
    """Deleting a row is only safe for something a recompute rebuilds. Each
    of these is written by grading_service or award_service and by nothing
    a person types."""
    assert svc.DERIVED == {
        "subject_final_grades",
        "combined_learning_area_results",
        "term_grade_summaries",
        "annual_grade_summaries",
        "learner_awards",
    }
    assert "term_grades" not in svc.DERIVED


def test_every_blocking_table_has_a_readable_label():
    for table, _ in svc._blocking_tables():
        assert table.name in svc._LABELS, (
            f"{table.name} blocks a section correction but would be shown to the "
            "user as a raw table name — add it to _LABELS"
        )


def test_the_audit_action_requires_a_reason():
    from app import audit_service

    assert audit_service.ENROLLMENT_SECTION_CORRECTED in audit_service.REASON_REQUIRED


def _calls_under_submit_button(func_source: str, name: str) -> tuple[int, int]:
    """(calls to `name` under an `if st.form_submit_button(...)`, all calls)."""
    tree = ast.parse(func_source)
    total, guarded = 0, 0

    def is_submit(test):
        return any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "form_submit_button"
            for n in ast.walk(test)
        )

    def visit(node, under_submit):
        nonlocal total, guarded
        if isinstance(node, ast.Call):
            callee = node.func
            called = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
            if called == name:
                total += 1
                guarded += under_submit
        if isinstance(node, ast.If) and is_submit(node.test):
            for child in node.body:
                visit(child, True)
            for child in node.orelse:
                visit(child, under_submit)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, under_submit)

    visit(tree, False)
    return guarded, total


def test_the_correction_only_runs_when_its_button_is_pressed():
    """The checks cost round trips. Inside the per-learner loop, unguarded,
    that is a query storm on every Streamlit rerun."""
    source = textwrap.dedent(inspect.getsource(page._roster_tab))
    guarded, total = _calls_under_submit_button(source, "correct_section")
    assert total == 1 and guarded == 1
    for helper in ("blockers", "finalized_attendance_months"):
        assert _calls_under_submit_button(source, helper)[1] == 0


def test_the_page_passes_the_section_it_was_showing():
    source = inspect.getsource(page._roster_tab)
    assert "expected_section_id=section_choice" in source
    assert "allowed_section_ids=" in source


def test_enroll_has_one_tab_and_scopes_an_adviser_to_their_learners():
    source = inspect.getsource(page.render)
    assert '["Enroll", "Section Roster / Movements"]' in source
    assert not hasattr(page, "_enroll_learner_tab")
    enroll = inspect.getsource(page._enroll_tab)
    assert "editable_learner_ids(session, adviser_user_id)" in enroll
    assert "adviser_user_id is not None" in enroll


def test_the_refusal_never_tells_a_registrar_to_ask_the_registrar():
    source = inspect.getsource(svc.correct_section)
    assert source.count("if unrestricted") >= 2


# --- Against the database, rolled back --------------------------------------


@pytest.fixture
def session():
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _two_sections_in_one_year(session):
    from app.models.academic_structure import Section

    sections = session.query(Section).order_by(Section.school_year_id, Section.name).all()
    by_year: dict = {}
    for s in sections:
        by_year.setdefault(s.school_year_id, []).append(s)
    for group in by_year.values():
        if len(group) >= 2:
            return group[0], group[1]
    pytest.skip("need two sections in one school year")


def _enrolled_learner(session, section):
    from app.models.enums import EnrollmentStatus, Sex
    from app.models.learners import Enrollment, Learner

    learner = Learner(
        last_name="ZZ-SECTION-FIX", first_name="Test", sex=Sex.MALE, birthdate=date(2009, 1, 1)
    )
    session.add(learner)
    session.flush()
    enrollment = Enrollment(
        learner_id=learner.id,
        school_year_id=section.school_year_id,
        grade_level_id=section.grade_level_id,
        section_id=section.id,
        enrollment_status=EnrollmentStatus.ENROLLED,
    )
    session.add(enrollment)
    session.flush()
    return enrollment


def _move(session, enrollment, target, **kwargs):
    kwargs.setdefault("reason", "Typo in the section entry")
    kwargs.setdefault("expected_section_id", enrollment.section_id)
    return correct_section(
        session, enrollment=enrollment, new_section_id=target.id, user_id=None, **kwargs
    )


def _class_day(session, school_year_id, *sections):
    """A calendar date in a month neither section has finalized — live
    data already holds finalized months, and a test about something else
    must not trip over them."""
    from app.models.attendance import AcademicCalendarDate, AttendanceMonthStatus
    from app.models.enums import FinalizationState

    finalized = {
        row[0]
        for row in session.query(AttendanceMonthStatus.year_month)
        .filter(
            AttendanceMonthStatus.section_id.in_([s.id for s in sections]),
            AttendanceMonthStatus.status == FinalizationState.FINALIZED,
        )
        .all()
    }
    for day in (
        session.query(AcademicCalendarDate)
        .filter_by(school_year_id=school_year_id)
        .order_by(AcademicCalendarDate.calendar_date)
        .all()
    ):
        if day.calendar_date.replace(day=1) not in finalized:
            return day
    pytest.skip("every calendar month is finalized for these sections")


def test_a_clean_enrollment_moves_and_is_audited(session):
    from app.models.admin import AuditLog

    a, b = _two_sections_in_one_year(session)
    enrollment = _enrolled_learner(session, a)
    version = enrollment.version

    moved = _move(session, enrollment, b)
    session.flush()

    assert moved.id == b.id
    assert enrollment.section_id == b.id
    assert enrollment.grade_level_id == b.grade_level_id
    assert enrollment.version == version + 1
    entry = (
        session.query(AuditLog)
        .filter_by(action="ENROLLMENT_SECTION_CORRECTED", object_id=enrollment.id)
        .one()
    )
    assert entry.reason == "Typo in the section entry"
    assert entry.previous_value["section"] == a.name
    assert entry.new_value["section"] == b.name


def test_a_reason_is_required(session):
    a, b = _two_sections_in_one_year(session)
    enrollment = _enrolled_learner(session, a)
    with pytest.raises(CorrectionRefused):
        _move(session, enrollment, b, reason="  ")
    assert enrollment.section_id == a.id


def test_a_stale_view_is_refused_rather_than_applied_on_top(session):
    """Two people correcting the same learner: the second was looking at a
    section the learner is no longer in."""
    a, b = _two_sections_in_one_year(session)
    enrollment = _enrolled_learner(session, a)
    with pytest.raises(CorrectionRefused, match="Someone else"):
        _move(session, enrollment, b, expected_section_id=b.id)
    assert enrollment.section_id == a.id


def test_the_same_section_is_refused(session):
    a, _ = _two_sections_in_one_year(session)
    enrollment = _enrolled_learner(session, a)
    with pytest.raises(CorrectionRefused):
        _move(session, enrollment, a)


def test_a_section_from_another_year_is_refused(session):
    from app.models.academic_structure import Section

    a, _ = _two_sections_in_one_year(session)
    other = session.query(Section).filter(Section.school_year_id != a.school_year_id).first()
    if other is None:
        pytest.skip("only one school year exists")
    enrollment = _enrolled_learner(session, a)
    with pytest.raises(CorrectionRefused, match="different school year"):
        _move(session, enrollment, other)
    assert enrollment.section_id == a.id


def test_seeded_attendance_moves_with_the_learner(session):
    """The case the first version got wrong: a prepared month gives every
    learner PRESENT rows, and those must not block."""
    from app.models.attendance import AttendanceRecord

    a, b = _two_sections_in_one_year(session)
    enrollment = _enrolled_learner(session, a)
    day = _class_day(session, a.school_year_id, a, b)
    session.add(AttendanceRecord(enrollment_id=enrollment.id, calendar_date_id=day.id))
    session.flush()

    _move(session, enrollment, b)
    assert enrollment.section_id == b.id
    assert session.query(AttendanceRecord).filter_by(enrollment_id=enrollment.id).count() == 1


@pytest.mark.parametrize("side", ["old", "new"])
def test_a_finalized_attendance_month_on_either_side_blocks(session, side):
    from app.models.attendance import AttendanceMonthStatus, AttendanceRecord
    from app.models.enums import FinalizationState

    a, b = _two_sections_in_one_year(session)
    enrollment = _enrolled_learner(session, a)
    day = _class_day(session, a.school_year_id, a, b)
    session.add(AttendanceRecord(enrollment_id=enrollment.id, calendar_date_id=day.id))
    month = day.calendar_date.replace(day=1)
    section = a if side == "old" else b
    status = (
        session.query(AttendanceMonthStatus).filter_by(section_id=section.id, year_month=month).one_or_none()
    )
    if status is None:
        status = AttendanceMonthStatus(
            section_id=section.id, school_year_id=section.school_year_id, year_month=month
        )
        session.add(status)
    status.status = FinalizationState.FINALIZED
    session.flush()

    with pytest.raises(CorrectionRefused, match="finalized"):
        _move(session, enrollment, b)
    assert enrollment.section_id == a.id


def test_a_grade_blocks_the_move(session):
    """Grades point at the old section's offerings, so they block — the
    school decided not to carry them across."""
    from app.models.grades import TermGrade
    from app.models.subjects import SectionSubjectOffering

    a, b = _two_sections_in_one_year(session)
    offering = session.query(SectionSubjectOffering).filter_by(section_id=a.id).first()
    if offering is None:
        pytest.skip("section has no offerings")
    enrollment = _enrolled_learner(session, a)
    columns = TermGrade.__table__.columns
    kwargs = {
        "enrollment_id": enrollment.id,
        "section_subject_offering_id": offering.id,
        "term_id": offering.term_id,
    }
    if "subject_id" in columns:
        kwargs["subject_id"] = offering.subject_id
    if "school_year_id" in columns:
        kwargs["school_year_id"] = a.school_year_id
    session.add(TermGrade(**kwargs))
    session.flush()

    with pytest.raises(CorrectionRefused, match="grades"):
        _move(session, enrollment, b)
    assert enrollment.section_id == a.id


def test_a_registrar_is_not_told_to_ask_the_registrar(session):
    from app.models.subjects import EnrollmentSubjectOverride, SectionSubjectOffering

    a, b = _two_sections_in_one_year(session)
    offerings = session.query(SectionSubjectOffering).filter_by(section_id=a.id).limit(2).all()
    if len(offerings) < 2:
        pytest.skip("section has fewer than two offerings")
    enrollment = _enrolled_learner(session, a)
    columns = EnrollmentSubjectOverride.__table__.columns
    kwargs = {
        "enrollment_id": enrollment.id,
        "original_section_subject_offering_id": offerings[0].id,
        "substitute_section_subject_offering_id": offerings[1].id,
    }
    if "term_id" in columns:
        kwargs["term_id"] = offerings[0].term_id
    if "reason" in columns:
        kwargs["reason"] = "test"
    session.add(EnrollmentSubjectOverride(**{k: v for k, v in kwargs.items() if k in columns}))
    session.flush()

    with pytest.raises(CorrectionRefused) as refused:
        _move(session, enrollment, b)
    assert "ask the registrar" not in str(refused.value).lower()
    with pytest.raises(CorrectionRefused, match="registrar"):
        _move(session, enrollment, b, allowed_section_ids={a.id, b.id})


def test_derived_rows_are_cleared_not_blocking(session):
    """Recompute all writes summaries for every learner, grades or not."""
    from app.models.grades import TermGradeSummary
    from app.models.organization import Term

    a, b = _two_sections_in_one_year(session)
    term = session.query(Term).filter_by(school_year_id=a.school_year_id).first()
    if term is None:
        pytest.skip("no terms")
    enrollment = _enrolled_learner(session, a)
    required = {
        c.name for c in TermGradeSummary.__table__.columns
        if not c.nullable and c.default is None and c.server_default is None and not c.primary_key
    }
    kwargs = {"enrollment_id": enrollment.id, "term_id": term.id}
    if "school_year_id" in required:
        kwargs["school_year_id"] = a.school_year_id
    if required - set(kwargs):
        pytest.skip(f"cannot build a summary row: {sorted(required - set(kwargs))}")
    session.add(TermGradeSummary(**kwargs))
    session.flush()

    _move(session, enrollment, b)
    session.flush()
    assert enrollment.section_id == b.id
    assert session.query(TermGradeSummary).filter_by(enrollment_id=enrollment.id).count() == 0


def test_an_adviser_may_not_move_a_learner_out_of_a_section_that_is_not_theirs(session):
    a, b = _two_sections_in_one_year(session)
    enrollment = _enrolled_learner(session, a)
    with pytest.raises(CorrectionRefused, match="advise"):
        _move(session, enrollment, b, allowed_section_ids={b.id})
    with pytest.raises(CorrectionRefused, match="advise"):
        _move(session, enrollment, b, allowed_section_ids={a.id})
    assert enrollment.section_id == a.id
    _move(session, enrollment, b, allowed_section_ids={a.id, b.id})
    assert enrollment.section_id == b.id


def test_movements_do_not_block_the_move(session):
    from app.models.enums import EnrollmentStatus
    from app.models.learners import LearnerMovement

    a, b = _two_sections_in_one_year(session)
    enrollment = _enrolled_learner(session, a)
    session.add(
        LearnerMovement(
            enrollment_id=enrollment.id,
            movement_type=EnrollmentStatus.TRANSFERRED_IN,
            effective_date=date(2026, 8, 1),
            previous_school="Elsewhere HS",
        )
    )
    session.flush()
    _move(session, enrollment, b)
    assert enrollment.section_id == b.id
