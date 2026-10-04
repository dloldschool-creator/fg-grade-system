"""`parent_notice_service` against the real database (spec §78.2–78.5).

Every write here is flushed, asserted and **rolled back** — the fixture
never commits, so nothing reaches the school's data. Same technique as
the Insights tests.
"""

from datetime import date, time

import pytest
from sqlalchemy import event, func

from app import parent_notice_service as notices
from app.database import SessionLocal, engine
from app.models.academic_structure import Section
from app.models.admin import AuditLog
from app.models.attendance import AcademicCalendarDate
from app.models.learners import Enrollment
from app.models.organization import SchoolYear, Term
from app.models.parent_notices import ParentNoticePolicy
from app.notice_rules import NoticeGroup, may_override


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def section_term(session):
    """The largest section, in the first term that has class days."""
    row = (
        session.query(Section, func.count(Enrollment.id))
        .join(Enrollment, Enrollment.section_id == Section.id)
        .group_by(Section.id)
        .order_by(func.count(Enrollment.id).desc())
        .first()
    )
    if row is None:
        pytest.skip("no section with learners")
    section = row[0]
    term = (
        session.query(Term)
        .join(AcademicCalendarDate, AcademicCalendarDate.term_id == Term.id)
        .filter(Term.school_year_id == section.school_year_id)
        .order_by(Term.term_number)
        .first()
    )
    if term is None:
        pytest.skip("no term with class days")
    return section, term


class _Counter:
    def __init__(self):
        self.count = 0

    def __enter__(self):
        event.listen(engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._hit)

    def _hit(self, *args):
        self.count += 1


def test_every_school_year_has_the_approved_policy(session):
    for school_year in session.query(SchoolYear).all():
        thresholds = notices.resolve_thresholds(session, school_year.id)
        assert thresholds is not None, f"{school_year.name} has no parent-notice policy"
        assert (thresholds.max_absences, thresholds.max_lates, thresholds.max_cuttings) == (2, 2, 0)


def test_the_highest_policy_version_wins(session, section_term):
    _, term = section_term
    session.add(
        ParentNoticePolicy(
            school_year_id=term.school_year_id, version=99,
            max_absences=5, max_lates=5, max_cuttings=1,
        )
    )
    session.flush()
    assert notices.resolve_thresholds(session, term.school_year_id).version == 99


def test_loading_a_section_is_a_fixed_handful_of_queries(session, section_term):
    section, term = section_term
    with _Counter() as counter:
        data = notices.load_section_notices(session, section, term)
    assert data.rows, "expected learners on the roll"
    # Twelve at most whatever the roster size; one per learner would be 40+.
    assert counter.count <= 12, f"{counter.count} queries for {len(data.rows)} learners"


def test_everyone_enrolled_is_grouped_or_counted_as_having_left(session, section_term):
    section, term = section_term
    data = notices.load_section_notices(session, section, term)
    enrolled = (
        session.query(Enrollment)
        .filter_by(section_id=section.id, school_year_id=term.school_year_id)
        .count()
    )
    assert len(data.rows) + data.left_before_term_end == enrolled
    assert {row.group for row in data.rows} <= set(NoticeGroup)


def test_an_override_moves_the_learner_and_is_audited_with_its_reason(session, section_term):
    section, term = section_term
    data = notices.load_section_notices(session, section, term)
    row = next((r for r in data.rows if may_override(r.computed)), None)
    if row is None:
        pytest.skip("nobody in this section is ready to be grouped yet")
    target = NoticeGroup.CONCERN if row.computed is NoticeGroup.RELEASE else NoticeGroup.RELEASE

    notices.set_override(
        session, enrollment_id=row.enrollment_id, term_id=term.id,
        decision=target, reason="Talked with the parent already", user_id=None,
    )
    session.flush()
    reloaded = {r.enrollment_id: r for r in notices.load_section_notices(session, section, term).rows}
    assert reloaded[row.enrollment_id].group is target
    assert reloaded[row.enrollment_id].computed is row.computed
    entry = (
        session.query(AuditLog)
        .filter_by(action="PARENT_NOTICE_OVERRIDDEN", object_id=row.enrollment_id)
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert entry is not None and entry.reason == "Talked with the parent already"

    assert notices.clear_override(
        session, enrollment_id=row.enrollment_id, term_id=term.id, user_id=None
    )
    session.flush()
    reloaded = {r.enrollment_id: r for r in notices.load_section_notices(session, section, term).rows}
    assert reloaded[row.enrollment_id].group is row.computed


@pytest.mark.parametrize("reason", ["", "   "])
def test_an_override_without_a_reason_is_refused(session, section_term, reason):
    _, term = section_term
    with pytest.raises(ValueError):
        notices.set_override(
            session, enrollment_id=None, term_id=term.id,
            decision=NoticeGroup.RELEASE, reason=reason, user_id=None,
        )


def test_a_learner_meeting_overrides_the_sections(session, section_term):
    section, term = section_term
    notices.set_meeting(
        session, section_id=section.id, term_id=term.id,
        meeting_date=date(2026, 10, 12), meeting_time=time(9, 0), user_id=None,
    )
    session.flush()
    data = notices.load_section_notices(session, section, term)
    assert data.section_meeting.meeting_date == date(2026, 10, 12)
    assert all(row.meeting == data.section_meeting for row in data.rows)

    own = data.rows[0]
    notices.set_meeting(
        session, section_id=section.id, term_id=term.id, enrollment_id=own.enrollment_id,
        meeting_date=date(2026, 10, 13), meeting_time=time(14, 30), user_id=None,
    )
    session.flush()
    rows = {r.enrollment_id: r for r in notices.load_section_notices(session, section, term).rows}
    assert rows[own.enrollment_id].meeting.meeting_time == time(14, 30)
    assert rows[own.enrollment_id].meeting.is_learner_specific
    assert all(
        r.meeting.meeting_date == date(2026, 10, 12)
        for key, r in rows.items() if key != own.enrollment_id
    )

    # Re-saving the section's schedule updates the row rather than adding one.
    notices.set_meeting(
        session, section_id=section.id, term_id=term.id,
        meeting_date=date(2026, 10, 14), meeting_time=time(8, 0), user_id=None,
    )
    session.flush()
    assert notices.load_section_notices(session, section, term).section_meeting.meeting_date == date(2026, 10, 14)
