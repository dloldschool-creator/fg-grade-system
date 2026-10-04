"""Term-card emails, without a database or a mail server (spec §78.4)."""

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import parent_notice_service as notices
from app.notice_mailer import MailSettings, build_message
from app.notice_messages import (
    NoticeContext,
    attachment_filename,
    birthdate_password,
    prose_name,
    sender_display_name,
    term_card_email,
)
from app.notice_rules import NoticeGroup

CTX = NoticeContext(
    learner_first="JUAN",
    learner_last="DELA CRUZ",
    section="BEZOS",
    grade_level="Grade 11",
    term_name="Term 1",
    school_year="2026-2027",
    adviser_name="MARIA SANTOS",
)
SETTINGS = MailSettings(address="fgnmhs.notices@gmail.com", app_password="x")


# --- Wording -----------------------------------------------------------------


def test_the_subject_names_the_term_learner_and_section():
    subject, _ = term_card_email(CTX)
    assert subject == "Term 1 Temporary Report Card – JUAN DELA CRUZ (11-BEZOS)"


def test_the_body_is_english_then_filipino_and_states_only_the_password_format():
    _, body = term_card_email(CTX)
    english, filipino = body.split("\n---\n")
    assert english.startswith("Good day!") and "Juan Dela Cruz of Grade 11 – BEZOS" in english
    assert "Magandang araw po!" in filipino and "Taong Panuruan 2026–2027" in filipino
    assert "YYYYMMDD" in english and "YYYYMMDD" in filipino
    assert "Maria Santos\nClass Adviser / Tagapayo ng Klase, 11-BEZOS" in body


def test_the_password_is_the_birthdate_and_never_appears_in_the_email():
    password = birthdate_password(date(2009, 3, 5))
    assert password == "20090305"
    # The example in the text is fixed, so check a learner born another day.
    _, body = term_card_email(CTX)
    assert birthdate_password(date(2008, 11, 23)) not in body


def test_names_read_naturally_in_prose_and_as_sender():
    assert prose_name("JUAN  DELA CRUZ") == "Juan Dela Cruz"
    assert sender_display_name("MARIA SANTOS") == "Maria Santos (FGNMHS Adviser)"
    assert attachment_filename(CTX) == "Term1_TermCard_JUAN_DELA_CRUZ.pdf"


# --- The message ---------------------------------------------------------------


def test_the_message_comes_from_the_school_account_replies_to_the_adviser_and_copies_nobody():
    message = build_message(
        SETTINGS, to="parent@example.com", sender_name="Maria Santos (FGNMHS Adviser)",
        reply_to="maria.santos@deped.gov.ph", subject="s", body="b",
        attachment=b"%PDF-1.4", attachment_name="card.pdf",
    )
    # Quoted because of the parentheses; mail clients show it unquoted.
    assert message["From"] == '"Maria Santos (FGNMHS Adviser)" <fgnmhs.notices@gmail.com>'
    assert message["Reply-To"] == "maria.santos@deped.gov.ph"
    assert message["To"] == "parent@example.com"
    assert message["Cc"] is None and message["Bcc"] is None
    (attachment,) = list(message.iter_attachments())
    assert attachment.get_filename() == "card.pdf"
    assert attachment.get_content_type() == "application/pdf"


def test_settings_come_from_the_environment_and_are_absent_until_both_are_set(monkeypatch):
    monkeypatch.delenv("NOTICE_EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("NOTICE_EMAIL_APP_PASSWORD", raising=False)
    assert MailSettings.from_env() is None
    monkeypatch.setenv("NOTICE_EMAIL_ADDRESS", "a@gmail.com")
    assert MailSettings.from_env() is None
    # Google shows app passwords in groups of four; the spaces are dropped.
    monkeypatch.setenv("NOTICE_EMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    settings = MailSettings.from_env()
    assert settings.app_password == "abcdefghijklmnop"
    assert (settings.host, settings.port) == ("smtp.gmail.com", 465)


# --- Who can be emailed ---------------------------------------------------------


def _note(status, *, sent_at=None, basis_at=None, created_at=None, error=None):
    return SimpleNamespace(
        id=uuid.uuid4(), kind="TERM_CARD", channel="EMAIL", status=status,
        recipient="parent@example.com", sent_at=sent_at, basis_at=basis_at,
        created_at=created_at or datetime(2026, 10, 1), error=error, term_id=uuid.uuid4(),
    )


def _row(*, group=NoticeGroup.RELEASE, consent=True, email="parent@example.com",
         notifications=(), basis_at=None):
    learner = SimpleNamespace(notices_consent=consent, guardian_email=email)
    return notices.NoticeRow(
        enrollment_id=uuid.uuid4(), learner=learner, figures=None,
        computed=group, reasons=(), group=group,
        basis_at=basis_at, notifications=list(notifications),
    )


NOW = datetime(2026, 10, 5, 8, 0)


@pytest.mark.parametrize(
    "row, sendable, label",
    [
        (_row(), True, "ready to email"),
        (_row(group=NoticeGroup.CONCERN), False, "not in Release"),
        (_row(consent=False), False, "no consent on file"),
        (_row(email=None), False, "no parent email on file"),
        (_row(notifications=[_note("SENT", sent_at=datetime(2026, 10, 2))]), False,
         "sent Oct 02 to parent@example.com"),
        (_row(notifications=[_note("PENDING", created_at=NOW - timedelta(minutes=2))]), False,
         "sending…"),
        (_row(notifications=[_note("PENDING", created_at=NOW - timedelta(hours=1))]), False,
         "interrupted — may have gone out"),
        (_row(notifications=[_note("FAILED", error="SMTP timeout")]), True,
         "ready — last try failed: SMTP timeout"),
    ],
)
def test_each_release_learner_has_one_plain_status(row, sendable, label):
    status = notices.card_email_status(row, now=NOW)
    assert (status.sendable, status.label) == (sendable, label)


def test_a_card_whose_grades_changed_after_sending_is_offered_again_as_a_resend():
    sent = _note("SENT", sent_at=datetime(2026, 10, 2), basis_at=datetime(2026, 9, 20))
    row = _row(notifications=[sent], basis_at=datetime(2026, 10, 3))
    status = notices.card_email_status(row, now=NOW)
    assert status.sendable and status.is_resend


def test_a_pending_send_blocks_even_without_consent_checks_passing():
    """In flight is in flight: nothing else about the learner matters."""
    row = _row(consent=False, notifications=[_note("PENDING", created_at=NOW)])
    assert notices.card_email_status(row, now=NOW).label == "sending…"


def test_aware_and_naive_timestamps_compare():
    aware = datetime(2026, 10, 3, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    assert notices._naive_utc(aware) == datetime(2026, 10, 3, 0, 0)
