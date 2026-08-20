r"""Put the Grade 11 TechPro electives back on a subject-level unit override.

**The problem this undoes.** DO 017 Table 19 gives a TechPro elective 4
units per term in Grade 11 and 12 in Grade 12, and both are the same
category. On 2026-08-20 that was modelled instead by splitting the category
in two -- `TECHPRO_ELECTIVE_3_TERMS` at 4 units -- and dropping the subject
overrides. It did not take effect, because
`section_subject_offerings.subject_category_id` is a snapshot written when
the offering is created: all 30 Grade 11 TechPro offerings still pointed at
`TECHPRO_ELECTIVE`, which had just been set to 12. Setup -> Subject Units
reads the *subject's* category and so displayed 4 while
`curriculum_policy.load_offering_units` used 12.

**Why the override rather than resyncing the offerings.** Both fix the
number. The override touches no offerings, leaves nothing to resync after,
keeps the permanent academic record carrying DepEd's single category name
(`academic_record_service` freezes it as text, SS38), and fails *loudly* if
someone forgets it on a future subject -- Subject Units shows the inherited
12 in the Grade 11 row, because that page reads the same resolution chain
the engine does. A split category fails silently, which is the trap above.

Idempotent, dry-run by default, audited as SUBJECT_UNITS_CHANGED:

    .venv\Scripts\python.exe -m scripts.fix_g11_techpro_units
    .venv\Scripts\python.exe -m scripts.fix_g11_techpro_units --confirm

**Units are not grades.** If any grades are already encoded, follow with
`scripts.apply_do17_units --recompute --confirm`.
"""

import argparse

from app import audit_service
from app.database import SessionLocal
from app.models.academic_structure import GradeLevel
from app.models.subjects import SectionSubjectOffering, Subject, SubjectCategory
from app.seed import TECHPRO_UNITS_BY_GRADE_LEVEL

TARGET_CATEGORY = "TECHPRO_ELECTIVE"
SPLIT_CATEGORY = "TECHPRO_ELECTIVE_3_TERMS"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Actually write.")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        target = session.query(SubjectCategory).filter_by(code=TARGET_CATEGORY).one()
        split = (
            session.query(SubjectCategory).filter_by(code=SPLIT_CATEGORY).one_or_none()
        )
        if split is None:
            print(f"No {SPLIT_CATEGORY} category. Nothing to undo.")
            return

        grade_levels = {gl.id: gl.code for gl in session.query(GradeLevel).all()}
        subjects = session.query(Subject).filter_by(subject_category_id=split.id).all()
        if not subjects:
            print(f"No subject sits in {SPLIT_CATEGORY}. Nothing to undo.")
            return

        changes = 0
        for subject in subjects:
            units = TECHPRO_UNITS_BY_GRADE_LEVEL.get(grade_levels.get(subject.grade_level_id))
            if units is None:
                print(f"  SKIP {subject.code}: no Table 19 units for its grade level")
                continue
            print(
                f"  {subject.code:24} category {split.code} -> {target.code}, "
                f"units_per_term {subject.units_per_term} -> {units}"
            )
            audit_service.record(
                session,
                action=audit_service.SUBJECT_UNITS_CHANGED,
                object_type="subjects",
                object_id=subject.id,
                previous={
                    "subject_category": split.name,
                    "units_per_term": subject.units_per_term,
                },
                new={"subject_category": target.name, "units_per_term": units},
            )
            subject.subject_category_id = target.id
            subject.units_per_term = units
            changes += 1

        if not changes:
            session.rollback()
            return

        session.flush()
        session.expire_all()
        stale = [
            o
            for o in session.query(SectionSubjectOffering).all()
            if o.subject_category_id
            != session.get(Subject, o.subject_id).subject_category_id
        ]
        print(f"\n  offerings left disagreeing with their subject's category: {len(stale)}")

        if args.confirm:
            session.commit()
            print(f"Wrote {changes} subject(s).")
        else:
            session.rollback()
            print(f"DRY RUN - would write {changes} subject(s). Re-run with --confirm.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
