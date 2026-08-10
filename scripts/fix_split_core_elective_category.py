"""One-off data fix: split the seeded `CORE_ACADEMIC_ELECTIVE` subject
category into `CORE_SUBJECT` (offered/averaged across all 3 terms) and
`OTHER_ACADEMIC_ELECTIVE` (offered in a single term only, not averaged).

Why: the master spec's §9 groups these under one weight profile since they
historically shared the same Written Work/Performance Task/Exam
percentages — but grade entry is direct-only now (Mode B), so that shared
weight profile no longer matters, while the term-offering distinction very
much does (it's what a report/gradebook needs to know to decide whether a
subject's Final Grade is a 3-term average or a single term's grade). This
was flagged by the user, who knows the DepEd distinction firsthand.

Run once, as a module:
    python -m scripts.fix_split_core_elective_category

Idempotent — if CORE_ACADEMIC_ELECTIVE no longer exists (already run),
does nothing.
"""

from app.database import SessionLocal
from app.models.subjects import Subject, SubjectCategory

CORE_SUBJECT_CODES = {
    "G11-EFFCOMM",
    "G11-MABKOM",
    "G11-GENMATH",
    "G11-GENSCI",
    "G11-LCS",
    "G11-PKLP",
}


def main() -> None:
    session = SessionLocal()
    try:
        old_category = (
            session.query(SubjectCategory).filter_by(code="CORE_ACADEMIC_ELECTIVE").one_or_none()
        )
        if old_category is None:
            print("CORE_ACADEMIC_ELECTIVE not found — already split, nothing to do.")
            return

        core_category = (
            session.query(SubjectCategory).filter_by(code="CORE_SUBJECT").one_or_none()
        )
        if core_category is None:
            core_category = SubjectCategory(code="CORE_SUBJECT", name="Core Subject")
            session.add(core_category)
            session.flush()

        elective_category = (
            session.query(SubjectCategory).filter_by(code="OTHER_ACADEMIC_ELECTIVE").one_or_none()
        )
        if elective_category is None:
            elective_category = SubjectCategory(
                code="OTHER_ACADEMIC_ELECTIVE", name="Other Academic Elective"
            )
            session.add(elective_category)
            session.flush()

        affected = session.query(Subject).filter_by(subject_category_id=old_category.id).all()
        core_count = elective_count = 0
        for subject in affected:
            if subject.code in CORE_SUBJECT_CODES:
                subject.subject_category_id = core_category.id
                core_count += 1
            else:
                subject.subject_category_id = elective_category.id
                elective_count += 1

        session.flush()
        session.delete(old_category)  # now unreferenced — ON DELETE RESTRICT would catch a miss
        session.commit()
        print(f"Reassigned {core_count} subjects to CORE_SUBJECT.")
        print(f"Reassigned {elective_count} subjects to OTHER_ACADEMIC_ELECTIVE.")
        print("Deleted the old CORE_ACADEMIC_ELECTIVE category.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
