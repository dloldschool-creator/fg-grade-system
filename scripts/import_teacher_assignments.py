"""Assign teachers to subject offerings in bulk, from a tab-separated file.

**Why a script and not a page.** The Teacher Assignments page assigns one
offering at a time, which is the right shape for a mid-year reassignment
and the wrong shape for setting up a school year: 16 sections x 7 subjects
x 3 terms is over 300 clicks. This is the once-a-year path, run by
whoever sets the year up, next to `bootstrap_admin.py` and
`purge_test_data.py`.

The file has three columns and a header row:

    SECTION	SUBJECT	TEACHER
    BEZOS	General Science	JULIEN S. TORRES

**Names must match the database exactly** (case and spacing aside).
Nothing is created here — no sections, no subjects, no accounts — and a
name that doesn't resolve is an error, never a new row. That is the whole
safety property: the worst this can do is refuse.

One file row covers **every term the subject is offered** to that
section, because a teacher is named per subject, not per term. A
three-term core subject therefore writes three assignment rows, and a
one-term elective writes one.

**Nothing is written without `--confirm`.** The default run reports what
it would do. `--replace` additionally reassigns offerings that already
have a different teacher; without it those rows are reported and skipped,
so an existing assignment is never silently overwritten. A replaced
assignment is deactivated rather than deleted (§47) — who taught what is
never lost.

Run with the project's own Python:

    .venv\\Scripts\\python.exe -m scripts.import_teacher_assignments roster.tsv
    .venv\\Scripts\\python.exe -m scripts.import_teacher_assignments roster.tsv --confirm
"""

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.academic_structure import Section
from app.models.organization import SchoolYear, Term
from app.models.rbac import Role, User, UserRole
from app.models.subjects import SectionSubjectOffering, Subject, TeacherAssignment


def key(value: str) -> str:
    """Match on letters and digits only, so 'Ñ', punctuation and double
    spaces can't be the reason a real name fails to resolve."""
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper().replace("Ñ", "N"))


def load(session, school_year_name):
    years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
    if not years:
        sys.exit("No school years exist.")
    if school_year_name:
        year = next((y for y in years if y.name == school_year_name), None)
        if year is None:
            sys.exit(f"No school year named {school_year_name!r}. Have: "
                     + ", ".join(y.name for y in years))
    else:
        year = years[0]

    sections = defaultdict(list)
    for section in session.query(Section).filter_by(school_year_id=year.id).all():
        sections[key(section.name)].append(section)

    teachers = defaultdict(list)
    for user in (
        session.query(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .filter(Role.code == "SUBJECT_TEACHER", User.is_active.is_(True))
        .all()
    ):
        teachers[key(user.full_name)].append(user)

    subjects = {s.id: s for s in session.query(Subject).all()}
    terms = {t.id: t for t in session.query(Term).filter_by(school_year_id=year.id).all()}

    # (section_id, subject key) -> offerings, one per term offered.
    offerings = defaultdict(list)
    for offering in (
        session.query(SectionSubjectOffering).filter_by(school_year_id=year.id).all()
    ):
        subject = subjects[offering.subject_id]
        offerings[(offering.section_id, key(subject.official_name))].append(offering)

    active = {
        a.section_subject_offering_id: a
        for a in session.query(TeacherAssignment).filter_by(is_active=True).all()
    }
    return year, sections, teachers, subjects, terms, offerings, active


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="tab-separated SECTION/SUBJECT/TEACHER file")
    parser.add_argument("--confirm", action="store_true", help="actually write")
    parser.add_argument("--replace", action="store_true",
                        help="also reassign offerings that already have a different teacher")
    parser.add_argument("--school-year", help="defaults to the newest")
    parser.add_argument("--by", help="email of the account recorded as assigned_by")
    args = parser.parse_args()

    with open(args.file, encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        sys.exit("That file has no rows.")

    session = SessionLocal()
    year, sections, teachers, subjects, terms, offerings, active = load(
        session, args.school_year
    )

    assigned_by = None
    if args.by:
        assigned_by = session.query(User).filter(User.email.ilike(args.by)).one_or_none()
        if assigned_by is None:
            sys.exit(f"No user with email {args.by!r}.")

    to_write, already, conflicts, problems = [], [], [], []

    for number, row in enumerate(rows, start=2):
        section_name = (row.get("SECTION") or "").strip()
        subject_name = (row.get("SUBJECT") or "").strip()
        teacher_name = (row.get("TEACHER") or "").strip()

        found = sections.get(key(section_name), [])
        if len(found) != 1:
            problems.append((number, "SECTION", section_name,
                             "no such section this year" if not found
                             else f"{len(found)} sections share that name"))
            continue
        section = found[0]

        matched = teachers.get(key(teacher_name), [])
        if len(matched) != 1:
            problems.append((number, "TEACHER", teacher_name,
                             "no active SUBJECT_TEACHER account with that name" if not matched
                             else f"{len(matched)} accounts share that name"))
            continue
        teacher = matched[0]

        for_subject = offerings.get((section.id, key(subject_name)), [])
        if not for_subject:
            problems.append((number, "SUBJECT", subject_name,
                             f"not offered to {section.name} this year"))
            continue

        for offering in sorted(for_subject, key=lambda o: terms[o.term_id].term_number):
            label = (f"{section.name} / {subjects[offering.subject_id].official_name}"
                     f" / T{terms[offering.term_id].term_number}")
            current = active.get(offering.id)
            if current is None:
                to_write.append((offering, teacher, label))
            elif str(current.teacher_user_id) == str(teacher.id):
                already.append(label)
            else:
                conflicts.append((offering, teacher, label, current))

    print(f"School year: {year.name}")
    print(f"File rows: {len(rows)}\n")
    print(f"  to assign          {len(to_write)}")
    print(f"  already correct    {len(already)}")
    print(f"  conflicts          {len(conflicts)}"
          + ("  (will be replaced)" if args.replace else "  (skipped)"))
    print(f"  unresolved rows    {len(problems)}")

    if problems:
        print("\nUNRESOLVED — nothing will be written until these are fixed:")
        for number, column, value, why in problems:
            print(f"  row {number:>4}  {column:<8} {value!r}: {why}")
    if conflicts:
        print("\nALREADY ASSIGNED TO SOMEONE ELSE:")
        for _offering, teacher, label, _current in conflicts:
            print(f"  {label}  → file says {teacher.full_name}")

    if not args.confirm:
        print("\nDry run. Nothing was written. Re-run with --confirm to apply.")
        session.close()
        return
    if problems:
        sys.exit("\nRefusing to write while any row is unresolved.")

    now = datetime.now(timezone.utc)
    written = 0
    batch = list(to_write)
    if args.replace:
        for offering, teacher, label, current in conflicts:
            current.is_active = False
            current.unassigned_at = now
            batch.append((offering, teacher, label))
    # The partial unique index allows only one active assignment per
    # offering, so a deactivation must reach the database before its
    # replacement is inserted.
    session.flush()
    for offering, teacher, _label in batch:
        session.add(
            TeacherAssignment(
                section_subject_offering_id=offering.id,
                teacher_user_id=teacher.id,
                assigned_by_user_id=assigned_by.id if assigned_by else None,
                assigned_at=now,
            )
        )
        written += 1

    session.commit()
    print(f"\nWrote {written} assignment(s).")
    session.close()


if __name__ == "__main__":
    main()
