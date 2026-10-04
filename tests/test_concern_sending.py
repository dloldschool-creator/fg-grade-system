"""Concern notices end to end against the real database (spec §78.5).

Same technique as `test_term_card_sending.py`: the session joins an outer
transaction with `join_transaction_mode="create_savepoint"`, so the
sender's per-message commits are savepoints and everything is rolled back
at the end. The mail server is a fake; nothing is emailed.
"""

import re
from datetime import date, time

import pytest
from sqlalchemy.orm import Session

from app import parent_notice_service as notices
from app.database import SessionLocal, engine
from app.models.academic_structure import Section
from app.models.organization import Term
from app.models.rbac import User
from app.notice_mailer import MailSettings
from app.notice_rules import NoticeGroup

SETTINGS = MailSettings(address="school@example.com", app_password="x")
READY = 2


class FakeMailer:
    sent: list = []

    def __init__(self, settings):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send(self, message):
        FakeMailer.sent.append(message)


@pytest.fixture(autouse=True)
def _reset():
    FakeMailer.sent = []


@pytest.fixture(scope="module")
def concern_ids():
    search = SessionLocal()
    try:
        for term in search.query(Term).order_by(Term.term_number).all():
            for section in search.query(Section).filter_by(school_year_id=term.school_year_id):
                if not section.adviser_user_id:
                    continue
                data = notices.load_section_notices(search, section, term)
                if len(data.in_group(NoticeGroup.CONCERN)) >= READY:
                    return section.id, term.id
    finally:
        search.rollback()
        search.close()
    pytest.skip("no section with enough Concern learners yet")


@pytest.fixture
def session():
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


def _concern(session, section, term):
    return notices.load_section_notices(session, section, term).in_group(NoticeGroup.CONCERN)


@pytest.fixture
def setup(session, concern_ids):
    """Two Concern learners given an email, mobile and consent, and a
    section meeting, all inside the test's transaction."""
    section, term = session.get(Section, concern_ids[0]), session.get(Term, concern_ids[1])
    for index, row in enumerate(_concern(session, section, term)[:READY]):
        row.learner.guardian_email = f"concern{index}@example.com"
        row.learner.guardian_mobile = f"+63917000000{index}"
        row.learner.notices_consent = True
    notices.set_meeting(
        session, section_id=section.id, term_id=term.id,
        meeting_date=date(2026, 10, 12), meeting_time=time(9, 0), user_id=None,
    )
    session.commit()
    return section, term, session.get(User, section.adviser_user_id)


def test_concern_emails_go_once_with_the_meeting_and_no_attachment(session, setup):
    section, term, adviser = setup
    rows = _concern(session, section, term)
    result = notices.send_concern_emails(
        session, section, term, rows, user_id=None, adviser=adviser,
        settings=SETTINGS, mailer_cls=FakeMailer,
    )
    assert result.sent == READY
    message = FakeMailer.sent[0]
    assert list(message.iter_attachments()) == []
    body = message.get_content()
    assert "Monday, October 12, 2026 at 9:00 AM" in body
    assert "Lunes, Oktubre 12, 2026" in body

    FakeMailer.sent = []
    again = notices.send_concern_emails(
        session, section, term, _concern(session, section, term), user_id=None,
        adviser=adviser, settings=SETTINGS, mailer_cls=FakeMailer,
    )
    assert again.sent == 0 and FakeMailer.sent == []


def test_a_term_card_is_never_sent_to_a_concern_learner(session, setup):
    section, term, adviser = setup
    rows = _concern(session, section, term)
    result = notices.send_term_cards(
        session, section, term, rows, user_id=None, adviser=adviser,
        settings=SETTINGS, mailer_cls=FakeMailer,
    )
    assert result.sent == 0 and FakeMailer.sent == []


def test_marking_a_text_records_it_and_repeats_are_kept(session, setup):
    section, term, adviser = setup
    row = next(r for r in _concern(session, section, term) if notices.sms_status(r) is None)
    text = notices.sms_message(session, section, term, row, adviser=adviser)
    assert "Lun, Okt 12, 9:00 AM" in text

    for _ in range(2):
        notices.record_sms(session, row, term_id=term.id, user_id=None)
        session.commit()
    row = next(r for r in _concern(session, section, term) if r.enrollment_id == row.enrollment_id)
    texts = [n for n in row.notifications if n.channel == "SMS"]
    assert len(texts) == 2 and texts[0].recipient == row.learner.guardian_mobile


def test_letters_print_for_every_concern_learner_with_a_meeting(session, setup):
    section, term, adviser = setup
    rows = _concern(session, section, term)
    pdf, printed = notices.build_concern_letters(
        session, section, term, rows, adviser=adviser, user_id=None, today=date(2026, 10, 5),
    )
    session.commit()
    # Letters need no consent: everyone in Concern gets one.
    assert len(printed) == len(rows)
    assert len(re.findall(rb"/Type /Page\b", pdf)) == len(rows)
    recorded = [
        n for r in _concern(session, section, term) for n in r.notifications
        if n.channel == "LETTER"
    ]
    assert len(recorded) == len(rows)


def test_without_a_meeting_no_letter_is_built(session, setup):
    section, term, adviser = setup
    notices.clear_meeting(session, section_id=section.id, term_id=term.id, user_id=None)
    session.commit()
    pdf, printed = notices.build_concern_letters(
        session, section, term, _concern(session, section, term),
        adviser=adviser, user_id=None, today=date(2026, 10, 5),
    )
    assert pdf is None and printed == []


# --- Review fixes (2026-10-04) ---------------------------------------------------


def test_a_changed_meeting_makes_a_sent_concern_email_resendable(session, setup):
    section, term, adviser = setup
    notices.send_concern_emails(
        session, section, term, _concern(session, section, term), user_id=None,
        adviser=adviser, settings=SETTINGS, mailer_cls=FakeMailer,
    )
    notices.set_meeting(
        session, section_id=section.id, term_id=term.id,
        meeting_date=date(2026, 10, 14), meeting_time=time(13, 0), user_id=None,
    )
    session.commit()
    rows = _concern(session, section, term)
    changed = [r for r in rows if notices.concern_email_status(r).is_resend]
    assert len(changed) == READY
    assert notices.concern_email_status(changed[0]).label == "meeting changed since it was emailed"

    FakeMailer.sent = []
    result = notices.send_concern_emails(
        session, section, term, changed, user_id=None, adviser=adviser,
        settings=SETTINGS, mailer_cls=FakeMailer,
    )
    assert result.sent == READY
    assert "Wednesday, October 14, 2026 at 1:00 PM" in FakeMailer.sent[0].get_content()
    statuses = sorted(
        n.status for r in _concern(session, section, term) for n in r.notifications
        if n.channel == "EMAIL"
    )
    assert statuses == ["SENT"] * READY + ["SUPERSEDED"] * READY


def test_rebuilding_the_same_letters_records_them_once(session, setup):
    section, term, adviser = setup

    def build():
        notices.build_concern_letters(
            session, section, term, _concern(session, section, term),
            adviser=adviser, user_id=None, today=date(2026, 10, 5),
        )
        session.commit()
        return [
            n for r in _concern(session, section, term) for n in r.notifications
            if n.channel == "LETTER"
        ]

    first = build()
    assert len(build()) == len(first)
    # A different meeting is a different letter, and is recorded.
    notices.set_meeting(
        session, section_id=section.id, term_id=term.id,
        meeting_date=date(2026, 10, 19), meeting_time=time(9, 0), user_id=None,
    )
    session.commit()
    assert len(build()) == 2 * len(first)


def test_a_text_cannot_be_recorded_without_consent(session, setup):
    section, term, _ = setup
    row = next(r for r in _concern(session, section, term) if notices.sms_status(r) is None)
    row.learner.notices_consent = False
    with pytest.raises(ValueError, match="no consent"):
        notices.record_sms(session, row, term_id=term.id, user_id=None)
