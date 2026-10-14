r"""Everything in the database that hangs off one learner. Read-only.

    .venv\Scripts\python.exe -m scripts.learner_footprint "SANTOS, JUDE" "YALUNG, JEMMUEL"

Each argument is "LAST, FIRST" — the first name matches as a prefix, both
case-insensitively, so "SANTOS, JUDE" finds "JUDE OWWEL".

This is the command-line face of Enrollment → Section Roster → **Check
record**: both print `app.enrollment_withdrawal.load_record`, so they
cannot disagree about what a learner has. That covers the section,
adviser, movements, attendance, grades per term, every table pointing at
the enrollment (found from the model metadata, so a new table shows up
without editing anything) tagged deleted / derived / blocks, the SF2
months the learner appears on, and the anomaly flags.

**Safe by construction**: the transaction is `SET TRANSACTION READ ONLY`,
so Postgres itself refuses a write, and it is rolled back at the end.
LRNs print masked to the last four digits.
"""

import argparse

from sqlalchemy import text

from app.database import SessionLocal
from app.enrollment_withdrawal import load_record, mask_lrn
from app.models.learners import Enrollment, Learner


def _print_record(record) -> None:
    months = ", ".join(
        m.strftime("%b %Y") + (" (finalized)" if m in record.finalized_months else "")
        for m in record.affected_months
    )
    print(
        f"\n  Enrollment {record.enrollment_id} · {record.school_year}\n"
        f"    {record.section} · {record.grade_level} · {record.track} — {record.strand}\n"
        f"    adviser {record.adviser or 'none'} · status {record.status}"
        f" · enrolled {record.enrolled_on}"
    )
    for kind, when, why in record.movements:
        print(f"    movement: {kind} effective {when}" + (f" — {why}" if why else ""))
    print(
        f"    attendance: {record.attendance or 'none'}"
        f" · first marked {record.first_marked} · last present {record.last_present}"
    )
    for term, counts in record.grades_by_term.items():
        print(f"    {term}: {counts}")
    for t in record.tables:
        detail = f"  {t.by_status}" if t.by_status else ""
        print(f"    [{t.group:<7}] {t.label}: {t.total}{detail}")
    print(f"    on SF2/SF4 for: {months or 'none'}")
    print(f"    blockers: {', '.join(record.blockers) or 'none'}")
    for anomaly in record.anomalies:
        print(f"    ! {anomaly}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("names", nargs="+", help='"LAST, FIRST"')
    args = parser.parse_args()

    session = SessionLocal()
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        for name in args.names:
            last, _, first = (part.strip() for part in name.partition(","))
            matches = (
                session.query(Learner)
                .filter(Learner.last_name.ilike(last), Learner.first_name.ilike(f"{first}%"))
                .order_by(Learner.last_name, Learner.first_name)
                .all()
            )
            if not matches:
                print(f"\n{'=' * 72}\nNo learner matches {name!r}.")
            for learner in matches:
                print(
                    f"\n{'=' * 72}\n{learner.last_name}, {learner.first_name} "
                    f"{learner.middle_name or ''}".rstrip()
                    + f"\n  LRN {mask_lrn(learner.lrn)} · {learner.sex.value} · learner id {learner.id}"
                )
                enrollments = session.query(Enrollment).filter_by(learner_id=learner.id).all()
                if not enrollments:
                    print("  No enrollments.")
                for enrollment in enrollments:
                    _print_record(load_record(session, enrollment.id))
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    main()
