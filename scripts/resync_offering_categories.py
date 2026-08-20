r"""Re-point existing offerings at their subject's *current* category.

**Why this is needed at all.** `section_subject_offerings.subject_category_id`
is a snapshot, taken when the offering was created, and Section Subject
Offerings lets a section deliberately confirm a category that differs from
the catalog's (§48 — the offering is the source of truth for what a learner
is graded on). `curriculum_policy.load_offering_units` therefore reads the
*offering's* category, not the subject's.

The consequence, found on 2026-08-20: re-categorising a subject in the
Subject Catalog changes nothing about how existing offerings are weighted.
Splitting the Grade 11 TechPro electives out into `TECHPRO_ELECTIVE_3_TERMS`
(4 units) left all 30 of their offerings still pointing at
`TECHPRO_ELECTIVE`, which had just been set to 12 — so the Grade 11 subjects
were weighted at 12 units while Setup → Subject Units, which reads the
*subject's* category, displayed 4.

**Deliberately not a blanket resync.** A mismatch can be a legitimate
per-section override, and this script cannot tell the two apart. It reports
every mismatch it finds and changes only the subjects you name:

    .venv\Scripts\python.exe -m scripts.resync_offering_categories
    .venv\Scripts\python.exe -m scripts.resync_offering_categories \n        --subject G11-TP-COMPPROG --subject G11-TP-EMS --confirm

Nothing is written without `--confirm`. Every change is audit-logged as
SUBJECT_OFFERING_CHANGED, the same action the Section Subject Offerings page
records when a human edits the field.

**Units are not grades.** This changes how future averages are computed; it
does not rebuild `subject_final_grades`, `term_grade_summaries` or
`annual_grade_summaries`. If any grades are already encoded for an affected
section, run `scripts.apply_do17_units --recompute --confirm` afterwards.
"""

import argparse

from app import audit_service
from app.database import SessionLocal
from app.models.subjects import SectionSubjectOffering, Subject, SubjectCategory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subject",
        action="append",
        default=[],
        metavar="CODE",
        help="Subject code to resync; repeatable. Omit to report only.",
    )
    parser.add_argument("--confirm", action="store_true", help="Actually write.")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        categories = {c.id: c for c in session.query(SubjectCategory).all()}
        subjects = {s.id: s for s in session.query(Subject).all()}
        wanted = {code.strip().upper() for code in args.subject}

        mismatched = [
            offering
            for offering in session.query(SectionSubjectOffering).all()
            if offering.subject_id in subjects
            and offering.subject_category_id
            != subjects[offering.subject_id].subject_category_id
        ]

        if not mismatched:
            print("No offering disagrees with its subject's category. Nothing to do.")
            return

        by_subject: dict = {}
        for offering in mismatched:
            by_subject.setdefault(subjects[offering.subject_id].code, []).append(offering)

        print(f"{len(mismatched)} offering(s) carry a category their subject no longer has:\n")
        unknown = wanted - set(by_subject)
        for code in sorted(by_subject):
            group = by_subject[code]
            subject = subjects[group[0].subject_id]
            now = categories[subject.subject_category_id]
            was = categories[group[0].subject_category_id]
            selected = "RESYNC" if code in wanted else "skip  "
            print(
                f"  [{selected}] {code:24} {len(group):3} offering(s)  "
                f"{was.code} ({was.units_per_term}) -> "
                f"{now.code} ({now.units_per_term})"
            )
        if unknown:
            print(f"\n  --subject not found among the mismatches: {', '.join(sorted(unknown))}")

        targets = [o for code in wanted for o in by_subject.get(code, [])]
        if not targets:
            print("\nNo --subject named. Reported only; nothing written.")
            return

        for offering in targets:
            subject = subjects[offering.subject_id]
            previous = categories[offering.subject_category_id]
            new = categories[subject.subject_category_id]
            audit_service.record(
                session,
                action=audit_service.SUBJECT_OFFERING_CHANGED,
                object_type="section_subject_offerings",
                object_id=offering.id,
                previous={"subject": subject.official_name, "subject_category": previous.name},
                new={"subject_category": new.name},
            )
            offering.subject_category_id = subject.subject_category_id
            offering.version += 1

        if args.confirm:
            session.commit()
            print(f"\nWrote {len(targets)} offering(s).")
        else:
            session.rollback()
            print(f"\nDRY RUN - would write {len(targets)} offering(s). Re-run with --confirm.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
