"""Administrative backup (§55).

§55 asks for a scheduled database backup, a downloadable authorized
backup/export, restore procedures, a retention policy and an audit trail.
This module covers the parts that belong *in the application*: a complete,
downloadable, restorable snapshot, recorded in the audit log.

**What this is not.** Supabase's own automated backups are the scheduled
half of §55 and the thing to restore from after a real failure — they
capture the entire cluster, including the `auth` schema this dump can't
see. This is the operator-held copy: the one that survives losing access
to the Supabase project, and the one that can be inspected in Excel
without restoring anything. Both matter; neither replaces the other.

The dump is CSV-per-table inside a zip because that survives everything —
it needs no Postgres version match, no `pg_dump` binary on the operator's
machine, and stays readable in twenty years when this application is
gone. `MANIFEST.txt` records what it contains and how to restore it.

Tables are written in `Base.metadata.sorted_tables` order, which SQLAlchemy
sorts by foreign-key dependency — so restoring in file order never
violates a foreign key.
"""

import csv
import io
import zipfile
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.base import Base

# Dumped as-is. NULL is written as an empty *unquoted* field and an empty
# string as `""`, so the two stay distinguishable on restore — which
# matters here more than usual, since rule 2 makes "no grade encoded"
# and "zero" different facts.
NULL_SENTINEL = ""


def _cell(value) -> str:
    if value is None:
        return NULL_SENTINEL
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value)
    return str(value)


def _table_csv(session, table) -> tuple[bytes, int]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    columns = [column.name for column in table.columns]
    writer.writerow(columns)
    rows = session.execute(select(table)).fetchall()
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return buffer.getvalue().encode("utf-8-sig"), len(rows)


def _manifest(taken_at: datetime, taken_by: str, counts: dict[str, int]) -> bytes:
    lines = [
        "FGNMHS Grading System — database backup",
        "",
        f"Taken:    {taken_at:%Y-%m-%d %H:%M:%S} UTC",
        f"Taken by: {taken_by}",
        f"Tables:   {len(counts)}",
        f"Rows:     {sum(counts.values())}",
        "",
        "CONTENTS",
        "One CSV per table, listed below in foreign-key dependency order.",
        "Restoring in this order never violates a foreign key; restoring in",
        "reverse order is the safe order to delete.",
        "",
    ]
    width = max((len(name) for name in counts), default=0)
    lines += [f"  {index:>3}. {name:<{width}}  {count:>7} row(s)"
              for index, (name, count) in enumerate(counts.items(), start=1)]
    lines += [
        "",
        "WHAT IS NOT IN HERE",
        "  - Supabase Auth accounts (the auth schema). Passwords and login",
        "    identities live there, not in this database's `users` table,",
        "    which only links to them by id. Restoring this dump into a",
        "    fresh project gives you the school's records but not its",
        "    logins; those are re-provisioned from Users & Roles.",
        "  - Generated PDFs. Every one of them is reproducible from this",
        "    data, which is the point of §36.4's permanent academic record.",
        "",
        "RESTORE",
        "  1. Create the schema first: `alembic upgrade head` against the",
        "     target database. This dump carries data, not DDL, so the",
        "     tables must already exist and match this revision.",
        "  2. Load each CSV in the order listed above (psql `\\copy",
        "     <table> from '<file>' with (format csv, header true)`).",
        "  3. Re-provision Supabase Auth accounts and re-link them, then",
        "     verify a learner's Grade Summary against a known-good report.",
        "",
        "RETENTION",
        "  Treat this file as confidential: it contains every learner's LRN,",
        "  birthdate, grades and attendance (§54). Store it encrypted, keep",
        "  it only as long as the school's records policy requires, and",
        "  delete it securely rather than leaving copies on shared drives.",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def generate_backup(session, taken_by: str = "unknown") -> tuple[bytes, dict[str, int]]:
    """Returns (zip bytes, {table name: row count})."""
    taken_at = datetime.now(timezone.utc)
    counts: dict[str, int] = {}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, table in enumerate(Base.metadata.sorted_tables, start=1):
            data, count = _table_csv(session, table)
            counts[table.name] = count
            # The numeric prefix keeps dependency order visible in a file
            # listing, where alphabetical sorting would otherwise hide it.
            archive.writestr(f"data/{index:03d}_{table.name}.csv", data)
        archive.writestr("MANIFEST.txt", _manifest(taken_at, taken_by, counts))
    return buffer.getvalue(), counts


def backup_filename(taken_at: datetime | None = None) -> str:
    taken_at = taken_at or datetime.now(timezone.utc)
    return f"fgnmhs-backup-{taken_at:%Y%m%d-%H%M}.zip"
