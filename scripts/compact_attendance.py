"""Move every saved P out of `attendance_records` and into the per-month
bits of `attendance_encoded_days` — once, after migration `d7b3e5a19c42`
and the code that reads both forms are live.

**Why.** Checked 2026-10-29: `attendance_records` was 13 MB for 51,159
rows, and 49,202 of them (96%) were plain PRESENT — a row per learner per
class day, about 63 MB a year at ~1,200 learners against a 500 MB
database. The app no longer writes those rows (a saved P is a bit); this
removes the ones written before.

**What it does**, in this order:

  1. **Backfill.** Sets the bit for *every* existing record, P or not, so
     the bits are the complete "this day is encoded" set. Each month's
     `updated_at`/`created_at` is taken from its rows, not from now —
     Parent Notices compares against it, and stamping every month "now"
     would make every notice already sent look out of date.
  2. **Delete** the PRESENT rows that are safe to drop: `version = 1`
     (never changed), no audit entry pointing at them, and their bit
     confirmed set. A P that was ever a correction keeps its row, so the
     audit entries about that day still lead to a record (Insights' and
     Awards' perfect-attendance staleness checks join through it).
  3. **Verify.** Reads every enrollment's attendance through
     `attendance_service.statuses_for_days` — the function every page uses
     — and compares it with what the rows said beforehand. Any difference
     at all rolls the whole run back.

**Nothing is kept without `--confirm`.** The default run does all three
steps inside a transaction, reports, and rolls back — so the dry run
predicts the real one rather than describing it. Re-running after a
confirmed run finds nothing to do.

**`--vacuum-full`** is a separate, optional step. Deleting rows frees space
*inside* the table for reuse, so it stops growing for a long while, but
the Dashboard's Database space card measures the file, which only shrinks
after `VACUUM FULL`. That locks `attendance_records` for the second or two
it takes, so run it outside encoding hours too.

Run with the project's own Python, outside encoding hours, **only after the
new code is running** (check the page footer): code from before this
change reads attendance from rows alone, and would show every compacted
day as not encoded.

    .venv\\Scripts\\python.exe -m scripts.compact_attendance
    .venv\\Scripts\\python.exe -m scripts.compact_attendance --confirm
    .venv\\Scripts\\python.exe -m scripts.compact_attendance --vacuum-full
"""

import argparse

from sqlalchemy import text

from app.attendance_service import statuses_for_days
from app.database import SessionLocal, engine
from app.models.attendance import AcademicCalendarDate

BACKFILL = text(
    """
    INSERT INTO attendance_encoded_days
        (id, enrollment_id, year_month, days, last_saved_by_user_id, created_at, updated_at, version)
    SELECT gen_random_uuid(),
           r.enrollment_id,
           date_trunc('month', d.calendar_date)::date,
           bit_or(1 << (extract(day FROM d.calendar_date)::int - 1)),
           (array_agg(r.encoded_by_user_id ORDER BY r.updated_at DESC))[1],
           min(r.created_at),
           max(r.updated_at),
           1
    FROM attendance_records r
    JOIN academic_calendar_dates d ON d.id = r.calendar_date_id
    GROUP BY r.enrollment_id, date_trunc('month', d.calendar_date)
    ON CONFLICT (enrollment_id, year_month) DO UPDATE
    SET days = attendance_encoded_days.days | excluded.days,
        updated_at = greatest(attendance_encoded_days.updated_at, excluded.updated_at)
    WHERE attendance_encoded_days.days | excluded.days <> attendance_encoded_days.days
    """
)

# The bit check makes the delete safe on its own: a P row whose bit is not
# set is never dropped, whatever ran before.
DELETABLE = """
    FROM attendance_records r
    JOIN academic_calendar_dates d ON d.id = r.calendar_date_id
    JOIN attendance_encoded_days m
      ON m.enrollment_id = r.enrollment_id
     AND m.year_month = date_trunc('month', d.calendar_date)::date
    WHERE r.status = 'PRESENT'
      AND r.version = 1
      AND (m.days & (1 << (extract(day FROM d.calendar_date)::int - 1))) <> 0
      AND NOT EXISTS (SELECT 1 FROM audit_logs a WHERE a.object_id = r.id)
"""

DELETE = text(f"DELETE FROM attendance_records WHERE id IN (SELECT r.id {DELETABLE})")

SIZE = text(
    "SELECT pg_total_relation_size('attendance_records'), "
    "pg_total_relation_size('attendance_encoded_days')"
)


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def _every_status(session) -> dict:
    """`{(enrollment_id, calendar_date_id): status}` as the app reads it,
    for every enrollment that has any attendance, over every calendar date
    (a row can sit on a day the calendar no longer calls a class day)."""
    enrollment_ids = [
        row[0]
        for row in session.execute(
            text(
                "SELECT enrollment_id FROM attendance_records "
                "UNION SELECT enrollment_id FROM attendance_encoded_days"
            )
        )
    ]
    days = session.query(AcademicCalendarDate.id, AcademicCalendarDate.calendar_date).all()
    return statuses_for_days(session, enrollment_ids, days)


def compact(session) -> dict:
    """Steps 1-3 in the caller's transaction. Raises if anything would read
    differently afterwards; the caller decides whether to commit."""
    before_rows = {
        (enrollment_id, day_id): status
        for enrollment_id, day_id, status in session.execute(
            text("SELECT enrollment_id, calendar_date_id, status FROM attendance_records")
        )
    }
    before = _every_status(session)
    counts = dict(
        session.execute(
            text("SELECT status, count(*) FROM attendance_records GROUP BY status")
        ).all()
    )
    kept_with_history = session.execute(
        text(
            "SELECT count(*) FROM attendance_records r WHERE r.status = 'PRESENT' "
            "AND (r.version > 1 OR EXISTS (SELECT 1 FROM audit_logs a WHERE a.object_id = r.id))"
        )
    ).scalar()

    months_written = session.execute(BACKFILL).rowcount
    deleted = session.execute(DELETE).rowcount
    session.flush()

    after = _every_status(session)
    if after != before:
        lost = [k for k in before if k not in after]
        gained = [k for k in after if k not in before]
        changed = [k for k in before if k in after and before[k] != after[k]]
        raise RuntimeError(
            f"Attendance would read differently afterwards — {len(lost)} day(s) lost, "
            f"{len(gained)} gained, {len(changed)} changed. Nothing was kept."
        )
    # Every row the app could read before is still read — rows and bits
    # together say exactly what the rows said alone.
    missing = [k for k in before_rows if before.get(k) != before_rows[k]]
    if missing:
        raise RuntimeError(f"{len(missing)} record(s) did not read back as stored. Nothing was kept.")

    return {
        "rows": sum(counts.values()),
        "by_status": counts,
        "months_written": months_written,
        "deleted": deleted,
        "present_kept_with_history": kept_with_history,
        "days_verified": len(before),
    }


def vacuum_full() -> None:
    before = None
    with engine.connect() as connection:
        before = connection.execute(SIZE).one()
    # VACUUM cannot run inside a transaction block.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM (FULL, ANALYZE) attendance_records"))
        connection.execute(text("VACUUM (ANALYZE) attendance_encoded_days"))
        after = connection.execute(SIZE).one()
    print(f"attendance_records:      {_mb(before[0])} -> {_mb(after[0])}")
    print(f"attendance_encoded_days: {_mb(before[1])} -> {_mb(after[1])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm", action="store_true", help="keep the changes (default: dry run)")
    parser.add_argument(
        "--vacuum-full",
        action="store_true",
        help="only rewrite attendance_records to hand freed space back (locks it briefly)",
    )
    args = parser.parse_args()

    if args.vacuum_full:
        vacuum_full()
        return

    session = SessionLocal()
    try:
        size_before = session.execute(SIZE).one()
        report = compact(session)
        print(f"attendance_records now:   {report['rows']:,} rows, {_mb(size_before[0])}")
        for status, n in sorted(report["by_status"].items(), key=lambda kv: -kv[1]):
            print(f"  {status:<8} {n:,}")
        print(f"Months given bits:        {report['months_written']:,}")
        print(f"PRESENT rows deleted:     {report['deleted']:,}")
        print(f"PRESENT rows kept (have audit history or a change): {report['present_kept_with_history']:,}")
        print(f"Days verified identical:  {report['days_verified']:,}")
        if args.confirm:
            session.commit()
            print("Committed. Run --vacuum-full outside encoding hours to shrink the file.")
        else:
            session.rollback()
            print("Dry run — rolled back. Re-run with --confirm to keep it.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
