"""Moving a subject to another category must move its offerings too.

**The trap.** `section_subject_offerings.subject_category_id` is a snapshot
taken when the offering is created — deliberately, since §48 makes the
offering the source of truth for what a learner is graded on and a section
may confirm a category the catalog no longer has. The consequence is that
`curriculum_policy.load_offering_units` reads the *offering's* category
while Setup -> Subject Units reads the *subject's*, so re-categorising a
subject in the catalog changes what every screen displays and nothing about
what the grading engine computes.

That shipped on 2026-08-20. The two Grade 11 TechPro electives were split
into a 4-unit category of their own; all 30 of their offerings stayed on
`TECHPRO_ELECTIVE`, which had just been set to 12 for Grade 12's sake. The
subjects were weighted at 12 units while every page said 4 — a couple of
marks on every affected learner's Term and General Average, reported
nowhere.

These run against a real Postgres session and roll back.
"""

from decimal import Decimal

import pytest

from app.admin_pages.subject_catalog import (
    _offerings_by_subject,
    _recategorise_offerings,
)
from app.curriculum_policy import load_offering_units
from app.database import SessionLocal
from app.models.academic_structure import GradeLevel, Section
from app.models.organization import SchoolYear, Term
from app.models.subjects import SectionSubjectOffering, Subject, SubjectCategory


@pytest.fixture
def session():
    """Always rolled back — these tests mutate catalog rows and offerings
    and must leave the live database exactly as they found it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def scenario(session):
    """One subject at 4 units/term, one offering, plus a second category at
    12 — the shape of the real bug, built from whatever the database has.
    """
    section = session.query(Section).first()
    term = session.query(Term).first()
    school_year = session.query(SchoolYear).first()
    grade_level = session.get(GradeLevel, section.grade_level_id)
    if not all((section, term, school_year, grade_level)):
        pytest.skip("needs a seeded section, term and school year")

    light = SubjectCategory(code="ZZ_TEST_LIGHT", name="Test Light", units_per_term=4)
    heavy = SubjectCategory(code="ZZ_TEST_HEAVY", name="Test Heavy", units_per_term=12)
    session.add_all([light, heavy])
    session.flush()

    subject = Subject(
        code="ZZ-TEST-RECAT",
        official_name="Test Recategorisation Subject",
        short_name="Test Recat",
        grade_level_id=grade_level.id,
        subject_category_id=light.id,
    )
    session.add(subject)
    session.flush()

    offering = SectionSubjectOffering(
        school_year_id=school_year.id,
        section_id=section.id,
        subject_id=subject.id,
        term_id=term.id,
        subject_category_id=light.id,
    )
    session.add(offering)
    session.flush()
    return subject, offering, light, heavy


def _units(session, offering):
    return load_offering_units(session, [offering])[offering.id]


def test_the_engine_reads_the_offerings_category_not_the_subjects(session, scenario):
    """The trap itself, asserted rather than described.

    Move the subject and leave the offering behind, and every screen reads
    12 while the grading engine keeps applying 4.
    """
    subject, offering, _light, heavy = scenario

    subject.subject_category_id = heavy.id  # what the catalog's Save does
    session.flush()
    session.expire_all()

    assert _units(session, offering) == Decimal("4")


def test_recategorising_the_offerings_moves_the_weight(session, scenario):
    subject, offering, light, heavy = scenario
    assert _units(session, offering) == Decimal("4")

    subject.subject_category_id = heavy.id
    moved = _recategorise_offerings(session, subject, [offering], light, heavy)
    session.flush()
    session.expire_all()

    assert moved == 1
    assert _units(session, offering) == Decimal("12")


def test_recategorising_is_idempotent(session, scenario):
    subject, offering, light, heavy = scenario
    subject.subject_category_id = heavy.id
    assert _recategorise_offerings(session, subject, [offering], light, heavy) == 1
    assert _recategorise_offerings(session, subject, [offering], heavy, heavy) == 0


def test_recategorising_bumps_the_version(session, scenario):
    """Offerings carry `VersionMixin`; a concurrent editor must not have
    this change silently overwritten (CLAUDE.md rule 9)."""
    subject, offering, light, heavy = scenario
    before = offering.version

    subject.subject_category_id = heavy.id
    _recategorise_offerings(session, subject, [offering], light, heavy)
    session.flush()

    assert offering.version == before + 1


def test_recategorising_audits_every_offering(session, scenario):
    from app.models.admin import AuditLog

    subject, offering, light, heavy = scenario
    subject.subject_category_id = heavy.id
    _recategorise_offerings(session, subject, [offering], light, heavy)
    session.flush()

    entry = (
        session.query(AuditLog)
        .filter_by(object_type="section_subject_offerings", object_id=offering.id)
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert entry is not None
    assert entry.action == "SUBJECT_OFFERING_CHANGED"
    assert entry.previous_value["subject_category"] == light.name
    assert entry.new_value["subject_category"] == heavy.name


def test_offerings_by_subject_sees_the_subject(session, scenario):
    """The slice drives both the count the tick box quotes and the rows the
    Save branch re-points, so a subject with offerings must never read as
    having none."""
    subject, offering, _light, _heavy = scenario
    session.flush()
    assert [o.id for o in _offerings_by_subject(session).get(subject.id, [])] == [
        offering.id
    ]
