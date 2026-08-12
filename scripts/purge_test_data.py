"""Remove test learners, sections and users, with their dependent rows.

**Why this is a script and not a button.** Every foreign key in the
schema is `ON DELETE RESTRICT` by design: a learner cannot be deleted
while grades, attendance or an academic record still point at them. That
is the correct behaviour for a real learner — "delete this person and
everything about them" is not an action a school information system
should offer an adviser mid-year, and the refusal you see in the UI is
that protection working.

Clearing out test data is a different job: deliberate, one-off, and done
by whoever is setting the system up. So it lives here, next to
`bootstrap_admin.py`, and never appears in the app.

**Nothing is deleted without `--confirm`.** The default run only reports
what it would remove.

Run with the project's own Python — a bare `python` is the system one
and has none of the project's packages installed:

    .venv\\Scripts\\python.exe -m scripts.purge_test_data --learner 107041140016
    .venv\\Scripts\\python.exe -m scripts.purge_test_data --section "STEM - A"
    .venv\\Scripts\\python.exe -m scripts.purge_test_data --user teacher@example.com
    .venv\\Scripts\\python.exe -m scripts.purge_test_data --all-learners
    .venv\\Scripts\\python.exe -m scripts.purge_test_data --all-learners --confirm

Users are mostly *not* blocked: every historical reference to a user
(who submitted a grade, who finalized a month) is `ON DELETE SET NULL`,
so the record survives the person leaving — §50 wants the history to
outlive them. Only their roles, teaching assignments and adviser slots
block the delete, and this clears those.
"""

import argparse
import sys

from sqlalchemy import text

from app.database import SessionLocal

# Deleted in this order. Children first, and within that the order the
# foreign keys demand — subject_final_grades and the summaries before
# enrollments, enrollments before learners.
LEARNER_DEPENDENTS = [
    ("attendance_records", "enrollment_id in (select id from enrollments where learner_id = :id)"),
    ("term_grades", "enrollment_id in (select id from enrollments where learner_id = :id)"),
    ("subject_final_grades", "enrollment_id in (select id from enrollments where learner_id = :id)"),
    ("combined_learning_area_results", "enrollment_id in (select id from enrollments where learner_id = :id)"),
    ("term_grade_summaries", "enrollment_id in (select id from enrollments where learner_id = :id)"),
    ("annual_grade_summaries", "enrollment_id in (select id from enrollments where learner_id = :id)"),
    ("learner_awards", "enrollment_id in (select id from enrollments where learner_id = :id)"),
    ("learner_movements", "enrollment_id in (select id from enrollments where learner_id = :id)"),
    ("grade_finalization_records", "enrollment_id in (select id from enrollments where learner_id = :id)"),
    # Academic records reference both the enrollment and the learner, and
    # their own children reference them.
    ("learner_academic_record_terms", "learner_academic_record_id in (select id from learner_academic_records where learner_id = :id)"),
    ("learner_academic_record_subjects", "learner_academic_record_id in (select id from learner_academic_records where learner_id = :id)"),
    ("learner_academic_records", "learner_id = :id"),
    ("enrollments", "learner_id = :id"),
    ("learner_admission_records", "learner_id = :id"),
    ("learners", "id = :id"),
]

SECTION_DEPENDENTS = [
    ("attendance_month_status", "section_id = :id"),
    # Teacher assignments hang off the offerings, not the section.
    ("teacher_assignments", "section_subject_offering_id in (select id from section_subject_offerings where section_id = :id)"),
    ("section_subject_offerings", "section_id = :id"),
    ("sections", "id = :id"),
]

# Everything else pointing at a user is ON DELETE SET NULL, so the audit
# trail and grade history survive the account being removed.
USER_DEPENDENTS = [
    ("user_roles", "user_id = :id"),
    ("teacher_assignments", "teacher_user_id = :id"),
    ("sections", "adviser_user_id = :id"),  # reported, never auto-deleted
]

# A guard against this being run by accident after the real migration.
REAL_DATA_THRESHOLD = 50


def _count(session, table: str, where: str, ident) -> int:
    return session.execute(
        text(f"select count(*) from {table} where {where}"), {"id": ident}
    ).scalar()


def _purge(session, plan, ident, *, confirm: bool) -> int:
    total = 0
    for table, where in plan:
        count = _count(session, table, where, ident)
        if not count:
            continue
        total += count
        print("    %-40s %d row(s)" % (table, count))
        if confirm:
            session.execute(text(f"delete from {table} where {where}"), {"id": ident})
    return total


def _resolve_learner(session, needle):
    row = session.execute(
        text(
            "select id, lrn, last_name, first_name from learners "
            "where lrn = :n or upper(last_name) = upper(:n) "
            "order by last_name limit 2"
        ),
        {"n": needle},
    ).fetchall()
    if not row:
        sys.exit(f"No learner matches {needle!r} (try the LRN or exact last name).")
    if len(row) > 1:
        sys.exit(f"{needle!r} matches more than one learner — use the LRN instead.")
    return row[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--learner", help="LRN or exact last name")
    group.add_argument("--section", help="exact section name")
    group.add_argument("--user", help="email address")
    group.add_argument("--all-learners", action="store_true",
                       help="every learner and enrollment (leaves sections and users alone)")
    parser.add_argument("--confirm", action="store_true",
                        help="actually delete; without this the run only reports")
    parser.add_argument("--force", action="store_true",
                        help="override the safety guard on a database that looks live")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        learner_count = session.execute(text("select count(*) from learners")).scalar()
        if learner_count > REAL_DATA_THRESHOLD and not args.force:
            sys.exit(
                f"Refusing to run: this database holds {learner_count} learners, which "
                f"looks like real data rather than a handful of test rows.\n"
                f"If you are certain, re-run with --force."
            )

        print("Dry run — nothing will be deleted.\n" if not args.confirm
              else "DELETING.\n")

        if args.learner:
            learner = _resolve_learner(session, args.learner)
            print("Learner: %s, %s (LRN %s)" % (learner[2], learner[3], learner[1] or "none"))
            total = _purge(session, LEARNER_DEPENDENTS, learner[0], confirm=args.confirm)

        elif args.section:
            row = session.execute(
                text("select id, name from sections where name = :n"), {"n": args.section}
            ).fetchone()
            if not row:
                sys.exit(f"No section named {args.section!r}.")
            print("Section: %s" % row[1])
            enrolled = _count(session, "enrollments", "section_id = :id", row[0])
            if enrolled:
                sys.exit(
                    f"{enrolled} learner(s) are still enrolled in this section. Remove or "
                    f"move them first — deleting a section under its learners would orphan "
                    f"their grades."
                )
            total = _purge(session, SECTION_DEPENDENTS, row[0], confirm=args.confirm)

        elif args.user:
            row = session.execute(
                text("select id, email, full_name from users where email = :e"), {"e": args.user}
            ).fetchone()
            if not row:
                sys.exit(f"No user with email {args.user!r}.")
            print("User: %s <%s>" % (row[2], row[1]))
            advises = _count(session, "sections", "adviser_user_id = :id", row[0])
            if advises:
                sys.exit(
                    f"This user advises {advises} section(s). Reassign the adviser on the "
                    f"Sections page first — clearing it here would leave a section without one."
                )
            total = _purge(session, USER_DEPENDENTS[:2], row[0], confirm=args.confirm)
            print("    %-40s 1 row" % "users")
            total += 1
            if args.confirm:
                session.execute(text("delete from users where id = :id"), {"id": row[0]})

        else:  # --all-learners
            ids = [r[0] for r in session.execute(text("select id from learners")).fetchall()]
            print("Every learner: %d" % len(ids))
            total = 0
            for learner_id in ids:
                total += _purge(session, LEARNER_DEPENDENTS, learner_id, confirm=args.confirm)

        print("\n%d row(s) %s." % (total, "deleted" if args.confirm else "would be deleted"))
        if args.confirm:
            session.commit()
            print("Committed.")
        else:
            session.rollback()
            print("Re-run with --confirm to actually delete.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
