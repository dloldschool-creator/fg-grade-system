"""Stored timestamps are UTC; the people reading them are on +8.

The Audit Log answers "who changed this, and when". Eight hours out, it
answers the first half and makes the second half something you have to do
arithmetic on — and an audit trail nobody can line up against the school
day is not doing its job.

The conversion is display-only. Nothing here is written back, which is
what keeps a stored instant independent of who is looking at it.
"""

from datetime import datetime, timedelta, timezone

from app.display_time import DISPLAY_FORMAT, LOG_FORMAT, format_time, to_school_time

UTC = timezone.utc


def test_a_naive_timestamp_is_read_as_utc():
    """Most columns here are `timestamp without time zone` and come back
    with no tzinfo, holding a value a UTC database produced. Reading them
    as local time would land them eight hours early."""
    assert format_time(datetime(2026, 8, 14, 12, 46)) == "2026-08-14 20:46"


def test_an_aware_timestamp_is_converted_not_relabelled():
    assert format_time(datetime(2026, 8, 14, 12, 46, tzinfo=UTC)) == "2026-08-14 20:46"


def test_a_timestamp_already_on_school_time_is_left_where_it_is():
    school = timezone(timedelta(hours=8))
    assert format_time(datetime(2026, 8, 14, 20, 46, tzinfo=school)) == "2026-08-14 20:46"


def test_the_evening_rolls_the_date_forward():
    """17:30 UTC is half past one the next morning here. A conversion that
    only moved the clock and not the date would misfile the entry by a
    day, which is worse than being eight hours out."""
    assert format_time(datetime(2026, 8, 14, 17, 30)) == "2026-08-15 01:30"


def test_a_missing_timestamp_stays_blank():
    """NULL means it has not happened yet — rule 2's reasoning applied to
    dates. Never a placeholder date."""
    assert format_time(None) == ""
    assert format_time(None, blank="—") == "—"
    assert to_school_time(None) is None


def test_the_audit_log_keeps_its_seconds():
    stamp = datetime(2026, 8, 14, 12, 46, 43)
    assert format_time(stamp, LOG_FORMAT) == "2026-08-14 20:46:43"
    assert format_time(stamp, DISPLAY_FORMAT) == "2026-08-14 20:46"


def test_school_time_is_a_flat_plus_eight_all_year():
    """Neither Taipei nor Manila observes DST, so a July timestamp and a
    January one shift by the same amount. If that ever stops being true,
    the offsets below diverge and this fails."""
    january = to_school_time(datetime(2026, 1, 15, 4, 0))
    july = to_school_time(datetime(2026, 7, 15, 4, 0))

    assert january.utcoffset() == july.utcoffset() == timedelta(hours=8)
