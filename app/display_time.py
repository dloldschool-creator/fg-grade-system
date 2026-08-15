"""Timestamps are stored in UTC and read by people on Philippine time.

Postgres runs in UTC (`SHOW timezone` → UTC) and `func.now()` writes UTC,
which is right and stays that way — a stored instant should not depend on
where the reader is. But the Audit Log read eight hours behind the clock
on the wall, which makes "who changed this, and when" hard to line up with
anything that actually happened in the school day.

So the conversion lives at the display edge only. Nothing here is ever
written back to the database, and no column changes type.

**Naive means UTC.** Most timestamp columns here are
`timestamp without time zone` and come back from SQLAlchemy with no
tzinfo; the value in them was produced by a UTC database. Aware values
(the few `timestamptz` columns) are converted from whatever they carry, so
both shapes land in the same place.
"""

from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    SCHOOL_TZ = ZoneInfo("Asia/Taipei")
except Exception:  # noqa: BLE001
    # Windows ships no system tz database, so zoneinfo depends on the
    # `tzdata` package being present. Both Asia/Taipei and Asia/Manila have
    # been a flat UTC+8 for decades with no DST, so a fixed offset is the
    # same answer rather than an approximation of it.
    SCHOOL_TZ = timezone(timedelta(hours=8), "Asia/Taipei")

DISPLAY_FORMAT = "%Y-%m-%d %H:%M"
LOG_FORMAT = "%Y-%m-%d %H:%M:%S"


def to_school_time(value: datetime | None) -> datetime | None:
    """A stored timestamp as it read on a clock in the staff room."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SCHOOL_TZ)


def format_time(value: datetime | None, fmt: str = DISPLAY_FORMAT, blank: str = "") -> str:
    """Formats a stored timestamp in school time.

    `blank` is returned for a missing value rather than a placeholder date
    — a NULL timestamp means the thing has not happened yet (rule 2's
    reasoning, applied to dates rather than grades).
    """
    local = to_school_time(value)
    return blank if local is None else local.strftime(fmt)
