"""Assigning a teacher to a subject (§47), and who is allowed to.

A teaching assignment is what the Gradebook checks to decide who may
encode a section's grades, so these tests are as much about access
control as about data entry.

Everything runs against a real session and rolls back.
"""

import uuid

import pytest
from sqlalchemy import event

from app import audit_service
from app.database import SessionLocal, engine
from app.models.academic_structure import Section
from app.models.admin import AuditLog
from app.models.organization import SchoolYear
from app.models.rbac import Role, User, UserRole
from app.models.subjects import TeacherAssignment
from app.section_access import is_advised_by
from app.teacher_assignment_service import (
    assign_subject,
    load_section_subjects,
    may_assign,
    unassign_subject,
)


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


class FakeUser:
    """Stands in for `AuthUser`, whose `id` is our users.id **as a str** —
    the type the pages actually pass. Passing the ORM's `uuid.UUID` here
    would be testing a call the app never makes."""

    def __init__(self, user_id, *roles):
        self.id = str(user_id)
        self._roles = set(roles)

    def has_role(self, *codes) -> bool:
        return bool(self._roles & set(codes))


def _reload(session, section, subject_id):
    """The row as the database now has it — the page rebuilds it on every
    rerun, so a test that reuses a stale one is testing nothing."""
    return next(
        row
        for row in load_section_subjects(session, section.id, section.school_year_id)
        if row.subject.id == subject_id
    )


@pytest.fixture
def fixture_section(session):
    """A section, one of its subjects, an adviser and a teacher account.

    Picks the subject running in the **most terms**, because covering
    every term of a subject in one action is the behaviour under test,
    and starts it from a known state — unassigned — since the live
    database this runs against already has teachers on nearly every
    Grade 11 offering. Everything rolls back.
    """
    year = session.query(SchoolYear).order_by(SchoolYear.name.desc()).first()
    if year is None:
        pytest.skip("no school year")
    teacher = (
        session.query(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .filter(Role.code == "SUBJECT_TEACHER", User.is_active.is_(True))
        .order_by(User.full_name)
        .first()
    )
    if teacher is None:
        pytest.skip("no subject teacher accounts")

    best = None
    for section in (
        session.query(Section).filter_by(school_year_id=year.id).order_by(Section.name).all()
    ):
        for row in load_section_subjects(session, section.id, year.id):
            if best is None or len(row.offerings) > len(best[1].offerings):
                best = (section, row)
        if best is not None and len(best[1].offerings) >= 3:
            break
    if best is None:
        pytest.skip("no section with offerings")

    section, row = best
    section.adviser_user_id = teacher.id
    unassign_subject(session, row, actor_user_id=str(teacher.id), section=section)
    session.flush()
    return year, section, _reload(session, section, row.subject.id), teacher


# --- Who may assign --------------------------------------------------------


def test_a_super_admin_may_assign_in_any_section(session, fixture_section):
    _year, section, _row, _teacher = fixture_section
    admin = FakeUser(uuid.uuid4(), "SUPER_ADMIN")
    assert may_assign(section, admin)


def test_an_adviser_may_assign_in_their_own_section(session, fixture_section):
    _year, section, _row, teacher = fixture_section
    assert may_assign(section, FakeUser(teacher.id, "ADVISER"))


def test_an_adviser_may_not_assign_in_someone_elses_section(session, fixture_section):
    _year, section, _row, _teacher = fixture_section
    assert not may_assign(section, FakeUser(uuid.uuid4(), "ADVISER"))


def test_a_subject_teacher_may_not_assign_at_all(session, fixture_section):
    """The point of the whole design: a teacher cannot grant themselves a
    roster, even their own adviser's section, because holding only
    SUBJECT_TEACHER is not enough."""
    _year, section, _row, teacher = fixture_section
    assert not may_assign(section, FakeUser(teacher.id, "SUBJECT_TEACHER"))


def test_the_adviser_check_survives_a_str_against_a_uuid(session, fixture_section):
    """`AuthUser.id` is a str and the column is a uuid.UUID; comparing
    them with `==` is always False. This shipped once — see
    app/section_access.py."""
    _year, section, _row, teacher = fixture_section
    assert is_advised_by(section, str(teacher.id))
    assert is_advised_by(section, teacher.id)
    assert not is_advised_by(section, str(uuid.uuid4()))


# --- Writing the assignment ------------------------------------------------


def test_assigning_a_subject_covers_every_term_it_runs(session, fixture_section):
    """A teacher is named per subject; an assignment row lives per term."""
    _year, section, row, teacher = fixture_section
    actor = FakeUser(teacher.id, "SUPER_ADMIN")

    written, replaced = assign_subject(
        session, row, str(teacher.id), actor_user_id=actor.id, section=section
    )
    session.flush()

    assert written == len(row.offerings)
    assert replaced == 0
    active = (
        session.query(TeacherAssignment)
        .filter(
            TeacherAssignment.section_subject_offering_id.in_([o.id for o in row.offerings]),
            TeacherAssignment.is_active.is_(True),
        )
        .all()
    )
    assert len(active) == len(row.offerings)
    assert {str(a.teacher_user_id) for a in active} == {str(teacher.id)}


def test_reassigning_keeps_the_previous_assignment_on_record(session, fixture_section):
    """§47: who taught what is never lost — the old row is deactivated,
    never deleted."""
    _year, section, row, teacher = fixture_section
    others = (
        session.query(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .filter(Role.code == "SUBJECT_TEACHER", User.id != teacher.id)
        .first()
    )
    if others is None:
        pytest.skip("only one subject teacher account")

    actor = FakeUser(teacher.id, "SUPER_ADMIN")
    assign_subject(session, row, str(teacher.id), actor_user_id=actor.id, section=section)
    session.flush()
    written, replaced = assign_subject(
        session, row, str(others.id), actor_user_id=actor.id, section=section
    )
    session.flush()

    assert written == len(row.offerings)
    assert replaced == len(row.offerings)

    all_rows = (
        session.query(TeacherAssignment)
        .filter(
            TeacherAssignment.section_subject_offering_id.in_([o.id for o in row.offerings])
        )
        .all()
    )
    active = [a for a in all_rows if a.is_active]
    inactive = [a for a in all_rows if not a.is_active]
    assert {str(a.teacher_user_id) for a in active} == {str(others.id)}
    assert any(str(a.teacher_user_id) == str(teacher.id) for a in inactive)
    assert all(a.unassigned_at is not None for a in inactive)


def test_assigning_the_same_teacher_twice_writes_nothing(session, fixture_section):
    _year, section, row, teacher = fixture_section
    actor = FakeUser(teacher.id, "SUPER_ADMIN")
    assign_subject(session, row, str(teacher.id), actor_user_id=actor.id, section=section)
    session.flush()

    reloaded = _reload(session, section, row.subject.id)
    written, replaced = assign_subject(
        session, reloaded, str(teacher.id), actor_user_id=actor.id, section=section
    )
    assert (written, replaced) == (0, 0)


def test_unassigning_clears_every_term(session, fixture_section):
    _year, section, row, teacher = fixture_section
    actor = FakeUser(teacher.id, "SUPER_ADMIN")
    assign_subject(session, row, str(teacher.id), actor_user_id=actor.id, section=section)
    session.flush()

    cleared = unassign_subject(session, row, actor_user_id=actor.id, section=section)
    session.flush()
    assert cleared == len(row.offerings)
    assert not (
        session.query(TeacherAssignment)
        .filter(
            TeacherAssignment.section_subject_offering_id.in_([o.id for o in row.offerings]),
            TeacherAssignment.is_active.is_(True),
        )
        .all()
    )


# --- The audit trail (rule 8) ----------------------------------------------


def test_assigning_and_unassigning_are_audit_logged(session, fixture_section):
    """Granting someone a roster is an access change, and it went
    unrecorded until advisers could do it."""
    _year, section, row, teacher = fixture_section
    actor = FakeUser(teacher.id, "SUPER_ADMIN")

    before = session.query(AuditLog).count()
    assign_subject(session, row, str(teacher.id), actor_user_id=actor.id, section=section)
    session.flush()
    unassign_subject(session, row, actor_user_id=actor.id, section=section)
    session.flush()

    entries = (
        session.query(AuditLog)
        .filter(
            AuditLog.action.in_(
                [audit_service.TEACHER_ASSIGNED, audit_service.TEACHER_UNASSIGNED]
            )
        )
        .all()
    )
    assert session.query(AuditLog).count() == before + 2
    actions = [e.action for e in entries]
    assert audit_service.TEACHER_ASSIGNED in actions
    assert audit_service.TEACHER_UNASSIGNED in actions

    assigned = next(e for e in entries if e.action == audit_service.TEACHER_ASSIGNED)
    assert assigned.new_value["subject"] == row.subject.official_name
    assert assigned.new_value["section"] == section.name


def test_one_entry_per_subject_not_per_term(session, fixture_section):
    """Three term rows are one decision. Logging each would bury §50's
    trail under implementation detail."""
    _year, section, row, teacher = fixture_section
    if len(row.offerings) < 2:
        pytest.skip("no multi-term subject in this database")

    before = session.query(AuditLog).count()
    assign_subject(session, row, str(teacher.id), actor_user_id=str(teacher.id), section=section)
    session.flush()
    assert session.query(AuditLog).count() == before + 1


# --- Cost ------------------------------------------------------------------


class QueryCounter:
    def __init__(self):
        self.count = 0

    def __enter__(self):
        event.listen(engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1


def test_loading_a_section_costs_a_fixed_number_of_queries(session, fixture_section):
    """The page renders one control per subject; resolving the subject,
    the terms and the current assignment inside that loop would be ~60
    round trips at 85ms on every rerun. Four queries, flat."""
    year, section, _row, _teacher = fixture_section
    session.flush()
    # Read the ids *before* the counter: attribute access on an expired
    # instance is itself a query, and counting the test's own reloads
    # would make the assertion about the wrong thing.
    section_id, year_id = section.id, year.id
    session.expire_all()

    with QueryCounter() as counter:
        loaded = load_section_subjects(session, section_id, year_id)

    assert loaded
    assert counter.count <= 4, f"{counter.count} queries for {len(loaded)} subjects"
