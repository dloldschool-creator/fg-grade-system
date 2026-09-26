"""The Gradebook's submission deadline.

Since 2026-09-26 a set deadline closes encoding: teachers may encode up to
and including the deadline date, and the day after the class is read-only
even while the term's OPEN/CLOSED switch says OPEN. The boundary is the
part worth pinning — a day late is different from on time, and a term with
no deadline set must stay governed by the switch alone.
"""

from datetime import date

from app.admin_pages.gradebook import days_past_deadline, encoding_is_open

DEADLINE = date(2026, 9, 15)


def test_nothing_is_late_on_the_deadline_itself():
    """A deadline is a date, not an instant — grades submitted on the day
    are on time."""
    assert days_past_deadline(DEADLINE, today=DEADLINE) is None


def test_the_day_before_is_not_late():
    assert days_past_deadline(DEADLINE, today=date(2026, 9, 14)) is None


def test_the_day_after_is_one_day_late():
    assert days_past_deadline(DEADLINE, today=date(2026, 9, 16)) == 1


def test_lateness_counts_in_days():
    assert days_past_deadline(DEADLINE, today=date(2026, 10, 1)) == 16


def test_a_term_with_no_deadline_never_warns():
    """The column is nullable and the seeded terms may leave it unset —
    an absent deadline is not an overdue one."""
    assert days_past_deadline(None, today=date(2027, 1, 1)) is None


def test_encoding_stays_open_through_the_deadline_day():
    assert encoding_is_open(True, DEADLINE, today=DEADLINE)


def test_encoding_closes_the_day_after_the_deadline():
    assert not encoding_is_open(True, DEADLINE, today=date(2026, 9, 16))


def test_no_deadline_leaves_the_switch_in_charge():
    assert encoding_is_open(True, None, today=date(2027, 1, 1))
    assert not encoding_is_open(False, None, today=date(2027, 1, 1))


def test_a_closed_term_stays_closed_before_its_deadline():
    assert not encoding_is_open(False, DEADLINE, today=date(2026, 9, 1))


def test_the_gradebook_uses_the_school_date_not_the_hosts():
    """The host runs on UTC; `date.today()` there would close encoding at
    8 a.m. Manila time on the deadline day."""
    import inspect

    from app.admin_pages import gradebook

    source = inspect.getsource(gradebook.render)
    assert "datetime.now(SCHOOL_TZ).date()" in source
    assert "encoding_is_open(" in source
