"""Bulk teacher assignment from a spreadsheet. Runs on a real session and rolls back."""

import pytest

from app.database import SessionLocal
from app.import_pipeline import read_table
from app.models.academic_structure import Section
from app.models.organization import SchoolYear
from app.teacher_assignment_import import (
    COLUMNS,
    build_plan,
    commit_plan,
    template_bytes,
)
from app.models.subjects import TeacherAssignment
from app.teacher_assignment_service import active_teachers, load_year_subjects


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _target(session):
    """A section with a subject that has no teacher, plus a teacher.

    One batched load per school year (`load_year_subjects`) rather than a
    scan of every section: the helper under test is what makes that cheap.
    """
    teachers = active_teachers(session)
    if not teachers:
        pytest.skip("no teachers in the database")
    for year in session.query(SchoolYear).all():
        sections = {s.id: s for s in session.query(Section).filter_by(school_year_id=year.id)}
        for section_id, rows in load_year_subjects(session, year.id).items():
            for row in rows:
                if row.is_unassigned :
                    return year, sections[section_id], row, teachers[0]
    pytest.skip("no suitable unassigned subject")


def _row(section, subject, teacher, n=2):
    return {"section": section, "subject": subject, "teacher": teacher, "__row__": n}


def test_plan_resolves_and_commit_assigns(session):
    year, section, row, teacher = _target(session)
    plan = build_plan(
        session, [_row(section.name.lower(), row.subject.official_name, teacher.full_name)], year.id
    )
    assert not plan.errors and len(plan.assignments) == 1
    assert commit_plan(session, plan, None) == len(row.offerings)
    session.flush()
    again = build_plan(
        session, [_row(section.name, row.subject.official_name, teacher.full_name)], year.id
    )
    assert again.already == 1 and not again.assignments


def test_unknown_names_are_errors_not_new_rows(session):
    year, section, row, teacher = _target(session)
    rows = [
        _row("NO SUCH SECTION", row.subject.official_name, teacher.full_name, 2),
        _row(section.name, "NO SUCH SUBJECT", teacher.full_name, 3),
        _row(section.name, row.subject.official_name, "NOBODY AT ALL", 4),
    ]
    plan = build_plan(session, rows, year.id)
    assert [e["column"] for e in plan.errors] == ["Section", "Subject", "Teacher"]
    assert not plan.assignments


def test_blank_teacher_is_skipped(session):
    year, section, row, _ = _target(session)
    plan = build_plan(session, [_row(section.name, row.subject.official_name, "")], year.id)
    assert plan.skipped_blank == 1 and not plan.errors


def test_conflict_is_listed_and_only_replaced_on_request(session):
    year, section, row, teacher = _target(session)
    other = next((t for t in active_teachers(session) if t.id != teacher.id), None)
    if other is None:
        pytest.skip("needs two teachers")
    commit_plan(session, build_plan(
        session, [_row(section.name, row.subject.official_name, teacher.full_name)], year.id), None)
    session.flush()
    rows = [_row(section.name, row.subject.official_name, other.full_name)]
    kept = build_plan(session, rows, year.id)
    assert len(kept.conflicts) == 1 and not kept.assignments
    replaced = build_plan(session, rows, year.id, replace=True)
    assert len(replaced.assignments) == 1


def test_template_lists_unassigned_and_reads_back(session):
    year, section, row, _ = _target(session)
    headers, rows = read_table(template_bytes(session, year.id), "t.xlsx")
    assert headers == [c.label for c in COLUMNS]
    assert any(
        r["Section"] == section.name and r["Subject"] == row.subject.official_name for r in rows
    )


def _open_one_term(session):
    """A multi-term subject held by one teacher, with its last term then
    unassigned (inside the rolled-back session): the state a term added
    mid-year leaves behind."""
    teachers = {str(t.id): t for t in active_teachers(session)}
    for year in session.query(SchoolYear).all():
        sections = {s.id: s for s in session.query(Section).filter_by(school_year_id=year.id)}
        for section_id, rows in load_year_subjects(session, year.id).items():
            for row in rows:
                teacher_id = row.sole_teacher_id
                if teacher_id in teachers and len(row.offerings) >= 2:
                    active = session.query(TeacherAssignment).filter_by(
                        section_subject_offering_id=row.offerings[-1].id, is_active=True
                    ).one()
                    active.is_active = False
                    session.flush()
                    return year, sections[section_id], row, teachers[teacher_id]
    pytest.skip("no multi-term subject held by a single teacher")


def test_partly_assigned_to_same_teacher_is_filled_not_a_conflict(session):
    year, section, row, teacher = _open_one_term(session)
    rows = [_row(section.name, row.subject.official_name, teacher.full_name)]
    plan = build_plan(session, rows, year.id)
    assert not plan.conflicts and len(plan.assignments) == 1 and plan.already == 0
    assert commit_plan(session, plan, None) == 1  # only the open term
    session.flush()
    again = build_plan(session, rows, year.id)
    assert again.already == 1 and not again.assignments


def test_template_includes_subject_with_an_open_term(session):
    year, section, row, _teacher = _open_one_term(session)
    _headers, rows = read_table(template_bytes(session, year.id), "t.xlsx")
    assert any(
        r["Section"] == section.name and r["Subject"] == row.subject.official_name for r in rows
    )


def test_punctuation_only_subject_matches_nothing(session):
    year, section, _row_, teacher = _target(session)
    plan = build_plan(session, [_row(section.name, "-", teacher.full_name)], year.id)
    assert [e["column"] for e in plan.errors] == ["Subject"] and not plan.assignments
