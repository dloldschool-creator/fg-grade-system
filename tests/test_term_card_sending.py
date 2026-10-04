"""`send_term_cards` end to end against the real database (spec §78.4).

The sender commits after every email, so it can't run in the usual
rolled-back session. Instead the session joins an **outer transaction**
with `join_transaction_mode="create_savepoint"`: each of its commits only
releases a savepoint, and the outer transaction is rolled back at the
end. Nothing reaches the school's data, and the mail server is a fake
that records what it was given — nothing is emailed either.

Learners are given a parent email and consent inside that transaction,
since real ones mostly have neither yet.
"""

import io
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app import parent_notice_service as notices
from app.database import engine
from app.models.academic_structure import Section
from app.models.organization import Term
from app.models.parent_notices import ParentNotification
from app.notice_mailer import MailAuthError, MailSettings
from app.notice_rules import NoticeGroup

SETTINGS = MailSettings(address="school@example.com", app_password="x")
READY = 3


class FakeMailer:
    """Stands in for `notice_mailer.Mailer`. `fail_for` makes sending to
    those addresses raise; `auth_error` fails the login."""

    sent: list = []
    fail_for: set = set()
    auth_error = False

    def __init__(self, settings):
        self.settings = settings

    def __enter__(self):
        if FakeMailer.auth_error:
            raise MailAuthError("refused")
        return self

    def __exit__(self, *exc):
        return False

    def send(self, message):
        if message["To"] in FakeMailer.fail_for:
            raise OSError("mailbox unavailable")
        FakeMailer.sent.append(message)


class AuthFailsOnSend(FakeMailer):
    def __enter__(self):
        return self

    def send(self, message):
        raise MailAuthError("refused")


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeMailer.sent, FakeMailer.fail_for, FakeMailer.auth_error = [], set(), False


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


@pytest.fixture(scope="module")
def release_ids():
    """Found once per run (read-only): a section and term with at least
    three Release learners. Searching per test cost minutes."""
    from app.database import SessionLocal

    search = SessionLocal()
    try:
        for term in search.query(Term).order_by(Term.term_number).all():
            for section in search.query(Section).filter_by(school_year_id=term.school_year_id):
                data = notices.load_section_notices(search, section, term)
                if len(data.in_group(NoticeGroup.RELEASE)) >= 3:
                    return section.id, term.id
    finally:
        search.rollback()
        search.close()
    pytest.skip("no section with three Release learners yet")


@pytest.fixture
def release_section(session, release_ids):
    """That section and term, with **three** of its Release learners given
    a parent email and consent inside the test's transaction. The rest stay
    unconsented, so they also show the sender skipping them — and three
    emails per test keeps the per-message commits from costing minutes."""
    section, term = session.get(Section, release_ids[0]), session.get(Term, release_ids[1])
    for index, row in enumerate(_release(session, section, term)[:READY]):
        row.learner.guardian_email = f"parent{index}@example.com"
        row.learner.notices_consent = True
    session.commit()
    return section, term


def _release(session, section, term):
    data = notices.load_section_notices(session, section, term)
    return data.in_group(NoticeGroup.RELEASE)


def _adviser(session, section):
    from app.models.rbac import User

    adviser = session.get(User, section.adviser_user_id)
    if adviser is None:
        pytest.skip("section has no adviser")
    return adviser


def _send(session, section, term, rows=None, mailer_cls=FakeMailer):
    rows = rows if rows is not None else _release(session, section, term)
    return notices.send_term_cards(
        session, section, term, rows, user_id=None,
        adviser=_adviser(session, section), settings=SETTINGS, mailer_cls=mailer_cls,
    )


def _ready(rows):
    return [r for r in rows if notices.card_email_status(r).sendable]


def _records(session, section, term):
    return [n for row in _release(session, section, term) for n in row.notifications]


def test_every_ready_parent_gets_one_email_with_the_locked_card(session, release_section):
    section, term = release_section
    rows = _release(session, section, term)
    ready = _ready(rows)
    result = _send(session, section, term, rows)

    assert len(ready) == READY
    assert result.sent == READY and not result.failed
    # Everyone without consent is skipped, never emailed.
    assert result.skipped == len(rows) - READY
    assert {m["To"] for m in FakeMailer.sent} == {r.learner.guardian_email for r in ready}
    message = FakeMailer.sent[0]
    assert message["Reply-To"] == _adviser(session, section).email
    assert message["Cc"] is None
    (attachment,) = list(message.iter_attachments())
    pdf = attachment.get_content()
    assert pdf.startswith(b"%PDF") and b"/Encrypt" in pdf
    assert {n.status for n in _records(session, section, term)} == {"SENT"}


def test_pressing_send_again_emails_nobody_twice(session, release_section):
    section, term = release_section
    first = _send(session, section, term)
    FakeMailer.sent = []
    second = _send(session, section, term)
    assert first.sent == READY and second.sent == 0 and FakeMailer.sent == []


def test_a_concurrent_send_loses_at_the_database_not_in_the_page(session, release_section):
    """Rows loaded before someone else claimed a learner still look ready;
    the unique index is what stops the second email."""
    section, term = release_section
    stale = _release(session, section, term)
    taken = _ready(stale)[0]
    session.add(
        ParentNotification(
            enrollment_id=taken.enrollment_id, term_id=term.id, kind="TERM_CARD",
            channel="EMAIL", status="PENDING", recipient="x@example.com",
        )
    )
    session.commit()

    result = _send(session, section, term, stale)
    assert result.sent == READY - 1
    assert taken.learner.guardian_email not in {m["To"] for m in FakeMailer.sent}


def test_one_failure_is_recorded_and_the_rest_still_go_then_it_can_be_retried(
    session, release_section
):
    section, term = release_section
    rows = _release(session, section, term)
    bad = _ready(rows)[1].learner.guardian_email
    FakeMailer.fail_for = {bad}

    result = _send(session, section, term, rows)
    assert result.failed == 1 and result.sent == READY - 1
    failed = [n for n in _records(session, section, term) if n.status == "FAILED"]
    assert len(failed) == 1 and "mailbox unavailable" in failed[0].error

    FakeMailer.fail_for, FakeMailer.sent = set(), []
    retry = _send(session, section, term)
    assert retry.sent == 1 and [m["To"] for m in FakeMailer.sent] == [bad]


def test_a_refused_login_mid_batch_stops_the_batch(session, release_section):
    section, term = release_section
    result = _send(session, section, term, mailer_cls=AuthFailsOnSend)
    assert result.stopped and result.failed == 1 and result.sent == 0
    # Only the first claim was used; the others were never attempted.
    assert len(_records(session, section, term)) == 1


def test_an_outdated_card_is_resent_and_the_old_record_superseded(session, release_section):
    section, term = release_section
    _send(session, section, term)
    # Pretend the card went out before the learner's grades last changed.
    first = next(
        r for r in _release(session, section, term) if r.live_email(notices.TERM_CARD)
    )
    sent = first.live_email(notices.TERM_CARD)
    sent.basis_at = (first.basis_at or datetime(2026, 10, 1)) - timedelta(days=1)
    if first.basis_at is None:
        pytest.skip("no grade or attendance timestamp to compare against")
    session.commit()

    FakeMailer.sent = []
    row = next(r for r in _release(session, section, term) if r.enrollment_id == first.enrollment_id)
    status = notices.card_email_status(row)
    assert status.sendable and status.is_resend
    result = _send(session, section, term, [row])
    assert result.sent == 1
    statuses = sorted(
        n.status
        for r in _release(session, section, term) if r.enrollment_id == first.enrollment_id
        for n in r.notifications
    )
    assert statuses == ["SENT", "SUPERSEDED"]
