"""Concern notices — email, SMS and printed letter — without a database
or a mail server (spec §78.5)."""

import re
from datetime import date, time
from types import SimpleNamespace

import pytest

from app.concern_letter import LetterData, generate_concern_letters
from app.notice_messages import (
    NoticeContext,
    concern_email,
    date_long_fil,
    date_short_fil,
    gsm_safe,
    letter_paragraphs,
    sms_link,
    sms_text,
    time_fil,
)

CTX = NoticeContext(
    learner_first="JUAN",
    learner_last="DELA CRUZ",
    section="BEZOS",
    grade_level="Grade 11",
    term_name="Term 1",
    school_year="2026-2027",
    adviser_name="MARIA SANTOS",
)
MEETING = (date(2026, 10, 12), time(9, 0))  # a Monday

# What a concern notice must never carry (§78.5): it stays general.
FORBIDDEN = re.compile(r"\b(fail\w*|grade[s]? of|absen\w*|late|cutting|average)\b", re.I)


# --- Dates and times -------------------------------------------------------------


def test_filipino_dates_and_times():
    assert date_long_fil(MEETING[0]) == "Lunes, Oktubre 12, 2026"
    assert date_short_fil(*MEETING) == "Lun, Okt 12, 9:00 AM"
    assert time_fil(time(9, 0)) == "ika-9:00 ng umaga"
    assert time_fil(time(12, 0)) == "ika-12:00 ng tanghali"
    assert time_fil(time(14, 30)) == "ika-2:30 ng hapon"
    assert time_fil(time(18, 0)) == "ika-6:00 ng gabi"


# --- Email ---------------------------------------------------------------------------


def test_the_concern_email_names_the_meeting_in_both_languages():
    subject, body = concern_email(CTX, MEETING)
    assert "Juan Dela Cruz" in subject
    english, filipino = body.split("\n---\n")
    assert "Monday, October 12, 2026 at 9:00 AM" in english
    assert "Lunes, Oktubre 12, 2026, ika-9:00 ng umaga" in filipino
    assert "progreso" in filipino and "pag-unlad" not in filipino


def test_without_a_meeting_it_asks_the_parent_to_reply_or_visit():
    _, body = concern_email(CTX, None)
    assert "at your earliest convenience" in body
    assert "lalong madaling panahon" in body
    assert "If you cannot come" not in body


@pytest.mark.parametrize("meeting", [MEETING, None])
def test_the_concern_email_stays_general(meeting):
    subject, body = concern_email(CTX, meeting)
    assert not FORBIDDEN.search(subject + body)


# --- SMS -----------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["FIL", "EN"])
@pytest.mark.parametrize("meeting", [MEETING, None])
def test_every_sms_is_plain_characters_general_and_two_texts_at_most(language, meeting):
    text = sms_text(CTX, meeting, language=language)
    assert gsm_safe(text) == text
    assert not FORBIDDEN.search(text)
    assert len(text) <= 2 * 153, len(text)


def test_filipino_is_the_default_and_english_is_on_request():
    assert sms_text(CTX, MEETING).startswith("Magandang araw po!")
    assert sms_text(CTX, MEETING, language="EN").startswith("Good day!")
    assert "Lun, Okt 12, 9:00 AM" in sms_text(CTX, MEETING)
    assert "Mon, Oct 12, 9:00 AM" in sms_text(CTX, MEETING, language="EN")


def test_names_with_accents_survive_as_plain_text():
    ctx = NoticeContext("JOSÉ", "PEÑA", "BEZOS", "Grade 11", "Term 1", "2026-2027", "ANA MARÍA REYES")
    text = sms_text(ctx, MEETING)
    assert gsm_safe(text) == text
    # Ñ and É are in the plain set; í is not and becomes i.
    assert "Peña" in text and "Ana Maria Reyes" in text


def test_gsm_safe_swaps_the_characters_that_cost_a_whole_text():
    assert gsm_safe("Term 1 – “OK” it’s") == 'Term 1 - "OK" it\'s'


def test_the_sms_link_carries_number_and_message():
    link = sms_link("+639171234567", "Salamat po! 9:00 AM")
    assert link == "sms:+639171234567?&body=Salamat%20po%21%209%3A00%20AM"


# --- Letter -------------------------------------------------------------------------


def test_the_letter_names_the_meeting_and_says_progreso():
    words = letter_paragraphs(CTX, MEETING)
    assert "Monday, October 12, 2026 at 9:00 AM" in words["body_en"]
    assert "Lunes, Oktubre 12, 2026, ika-9:00 ng umaga" in words["body_fil"]
    assert "progreso" in words["body_fil"]
    assert words["signatory"] == "MARIA SANTOS"
    assert not any(FORBIDDEN.search(v) for v in words.values())


def test_one_page_per_letter():
    words = letter_paragraphs(CTX, MEETING)
    letter = LetterData("FGNMHS", "Address", "October 5, 2026", words)
    pdf = generate_concern_letters([letter, letter, letter])
    assert pdf.startswith(b"%PDF")
    assert len(re.findall(rb"/Type /Page\b", pdf)) == 3


# --- Who can be texted ------------------------------------------------------------------


def test_sms_needs_concern_consent_and_a_mobile():
    from app import parent_notice_service as notices
    from app.notice_rules import NoticeGroup

    def row(group=NoticeGroup.CONCERN, consent=True, mobile="+639171234567"):
        learner = SimpleNamespace(notices_consent=consent, guardian_mobile=mobile)
        return notices.NoticeRow(
            enrollment_id=None, learner=learner, figures=None,
            computed=group, reasons=(), group=group,
        )

    assert notices.sms_status(row()) is None
    assert notices.sms_status(row(consent=False)) == "no consent on file"
    assert notices.sms_status(row(mobile=None)) == "no parent mobile on file"
    assert notices.sms_status(row(group=NoticeGroup.RELEASE)) == "not in Concern"


# --- Review fixes (2026-10-04) ---------------------------------------------------


def test_english_dates_do_not_depend_on_the_host_locale(monkeypatch):
    """Names are spelt out by hand, so a host in another locale still
    prints English in the English paragraph."""
    import locale

    from app.notice_messages import date_long_en, date_plain_en, date_short_en

    for name in ("fil_PH.UTF-8", "de_DE.UTF-8", "German_Germany.1252"):
        try:
            locale.setlocale(locale.LC_TIME, name)
            break
        except locale.Error:
            continue
    try:
        assert date_plain_en(date(2026, 10, 5)) == "October 5, 2026"
        assert date_long_en(MEETING[0]) == "Monday, October 12, 2026"
        assert date_short_en(*MEETING) == "Mon, Oct 12, 9:00 AM"
    finally:
        locale.setlocale(locale.LC_TIME, "")


def test_every_learner_picker_forgets_a_stale_choice_before_it_is_built():
    """A keyed picker whose stored learner has left the list raises. Each
    one clears the stale pick first (CLAUDE.md: _forget_stale)."""
    import inspect

    from app.admin_pages import parent_notices as page

    for function, key in (
        (page._override_form, '_forget_stale(f"{form}_learner"'),
        (page._learner_meeting_form, '_forget_stale(f"{form}_learner"'),
        (page._sms_panel, '_forget_stale(f"{tag}_learner"'),
        (page._letters_panel, '_forget_stale(f"{tag}_choice"'),
    ):
        source = inspect.getsource(function)
        assert key in source, function.__name__
        assert source.index(key) < source.index("st.selectbox("), function.__name__


def _held_row(*, attendance: bool, encoding_open: bool):
    from app import parent_notice_service as notices
    from app.notice_rules import NoticeGroup

    learner = SimpleNamespace(
        notices_consent=True, guardian_mobile="+639171234567",
        guardian_email="parent@example.com",
    )
    return notices.NoticeRow(
        enrollment_id=None, learner=learner, figures=None,
        computed=NoticeGroup.CONCERN, reasons=(), group=NoticeGroup.CONCERN,
        attendance_concern=attendance, encoding_open=encoding_open,
        meeting=notices.Meeting(MEETING[0], MEETING[1]),
    )


@pytest.mark.parametrize("attendance, encoding_open, held", [
    (True, True, False),    # attendance: contact at once, mid-term
    (False, True, True),    # failing grade: waits while grades can change
    (False, False, False),  # failing grade, encoding closed
    (True, False, False),
])
def test_only_attendance_concerns_go_out_while_encoding_is_open(attendance, encoding_open, held):
    """§78.5 as amended 2026-10-04. Email, text and letter all read the
    same rule, so no channel can be used for a learner another refuses."""
    from app import parent_notice_service as notices

    row = _held_row(attendance=attendance, encoding_open=encoding_open)
    assert bool(row.concern_held) is held
    assert notices.concern_email_status(row).sendable is not held
    assert (notices.sms_status(row) is None) is not held
    if held:
        assert notices.build_concern_letters(
            None, None, None, [row], adviser=None, user_id=None, today=MEETING[0]
        ) == (None, [])


def test_a_held_learner_already_emailed_still_reads_as_emailed():
    """A re-send can wait, but the table must not hide that the parent
    was already contacted."""
    from datetime import datetime

    from app import parent_notice_service as notices

    row = _held_row(attendance=False, encoding_open=True)
    row.notifications = [SimpleNamespace(
        kind=notices.CONCERN, channel="EMAIL", status="SENT", recipient="parent@example.com",
        sent_at=datetime(2026, 10, 5, 1, 0), basis_at=None,  # announced no meeting
    )]
    status = notices.concern_email_status(row)
    assert not status.sendable
    assert status.label.startswith("sent Oct 05")


def test_a_term_card_never_goes_out_on_an_incomplete_record():
    """An override can put an early attendance concern in Release; the card
    would carry blanks, so the email waits for the record."""
    from app import parent_notice_service as notices
    from app.notice_rules import NoticeGroup

    learner = SimpleNamespace(notices_consent=True, guardian_email="parent@example.com")
    row = notices.NoticeRow(
        enrollment_id=None, learner=learner, figures=None,
        computed=NoticeGroup.CONCERN, reasons=("3 absences",), group=NoticeGroup.RELEASE,
        incomplete=("2 attendance days not encoded",), override_decision=NoticeGroup.RELEASE,
    )
    status = notices.card_email_status(row)
    assert not status.sendable
    assert status.label == "record incomplete: 2 attendance days not encoded"


def test_the_page_no_longer_gates_every_concern_channel_on_encoding():
    """The one page-level gate became the per-learner `concern_held`; a
    page gate back in front of the panels would hide attendance concerns."""
    import inspect

    from app.admin_pages import parent_notices as page

    source = inspect.getsource(page.render)
    assert "term_encoding_open" not in source
