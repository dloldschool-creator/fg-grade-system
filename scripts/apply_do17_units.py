"""Turn on DepEd Order 017 s. 2026's unit-weighted grading, on a database
that already has a year's worth of data in it.

The migration `c3f1a7d90b42` adds the columns; this writes the values. They
are deliberately separate: units are curriculum data, and a curriculum
decision that lives in a migration can only be undone by another migration.

**What it does**, all idempotent, all skipping anything already set:

  1. Writes the DO 017 Table 19 units onto `subject_categories`.
  2. Writes the per-grade-level TechPro units onto `subjects` — Table 19
     gives a TechPro elective 4 units in Grade 11 and 12 in Grade 12, which
     one category cannot express.
  3. Gives the combined language pair a single core subject's weight (2),
     not the sum of its two components.
  4. Adds an ACTIVE grading policy version scoped to **Grade 11 only**,
     switching that grade level to unit-weighted averaging. Grade 12 is
     untouched: DO 017 ¶7 keeps Grade 12 on the 2016 K to 12 SHS curriculum
     until SY 2027-2028.
  5. Reports every subject whose units DO 017 does not settle, instead of
     guessing one.

**Nothing is written without `--confirm`.** The default run reports what it
would do and touches nothing.

**`--recompute` is a separate, later step.** `subject_final_grades`,
`term_grade_summaries` and `annual_grade_summaries` are caches; until they
are recomputed the screens keep showing averages computed the old way. Run
it after `--confirm`, outside encoding hours — it rewrites every affected
learner's derived rows and is the part that actually changes what a teacher
sees. A FINALIZED year is left alone (CLAUDE.md rule 6: no silent
recalculation of a finalized year).

Run with the project's own Python:

    .venv\\Scripts\\python.exe -m scripts.apply_do17_units
    .venv\\Scripts\\python.exe -m scripts.apply_do17_units --confirm
    .venv\\Scripts\\python.exe -m scripts.apply_do17_units --recompute --confirm
"""

import argparse
from decimal import Decimal

from app.database import SessionLocal
from app.grading_engine import AveragingMethod
from app.models.academic_structure import GradeLevel
from app.models.enums import (
    FinalizationRecordStatus,
    FinalizationScopeType,
    PolicyVersionStatus,
)
from app.models.grades import GradeFinalizationRecord
from app.models.learners import Enrollment
from app.models.organization import SchoolYear
from app.models.subjects import (
    CombinedLearningArea,
    GradingPolicy,
    GradingPolicyVersion,
    SectionSubjectOffering,
    Subject,
    SubjectCategory,
)
from app.seed import SUBJECT_CATEGORIES, TECHPRO_UNITS_BY_GRADE_LEVEL

# The pair is one 160-hour core subject under DO 017 Table 1, so it carries
# one core subject's units however many components it is taught in.
COMBINED_AREA_UNITS_PER_TERM = 2


def _category_units() -> dict:
    return {code: units for code, _name, units in SUBJECT_CATEGORIES if units is not None}


def apply_units(session, confirm: bool) -> list[str]:
    """Steps 1-3. Returns a list of human-readable changes."""
    changes: list[str] = []

    wanted = _category_units()
    for category in session.query(SubjectCategory).all():
        units = wanted.get(category.code)
        if units is None or category.units_per_term is not None:
            continue
        changes.append(f"category {category.code}: units_per_term -> {units}")
        if confirm:
            category.units_per_term = units

    grade_levels = {gl.id: gl.code for gl in session.query(GradeLevel).all()}
    techpro = (
        session.query(SubjectCategory).filter_by(code="TECHPRO_ELECTIVE").one_or_none()
    )
    if techpro is not None:
        subjects = (
            session.query(Subject).filter_by(subject_category_id=techpro.id).all()
        )
        for subject in subjects:
            if subject.units_per_term is not None:
                continue
            units = TECHPRO_UNITS_BY_GRADE_LEVEL.get(grade_levels.get(subject.grade_level_id))
            if units is None:
                continue
            changes.append(f"subject {subject.code}: units_per_term -> {units}")
            if confirm:
                subject.units_per_term = units

    for area in session.query(CombinedLearningArea).all():
        if area.units_per_term is not None:
            continue
        changes.append(
            f"combined area {area.name!r}: units_per_term -> {COMBINED_AREA_UNITS_PER_TERM}"
        )
        if confirm:
            area.units_per_term = COMBINED_AREA_UNITS_PER_TERM

    return changes


def activate_sshs_policy(session, school_year: SchoolYear, confirm: bool) -> list[str]:
    """Step 4 — the SSHS averaging rules, for every grade level.

    FGNMHS is a DO 017 pilot school (DepEd Memorandum 048 s. 2025), so the
    ¶7 exemption that keeps Grade 12 on the 2016 curriculum for SY 2026-2027
    does not apply and both grade levels move together. The version is left
    unscoped by grade level for that reason.
    """
    existing = (
        session.query(GradingPolicyVersion)
        .filter_by(
            effective_school_year_id=school_year.id,
            status=PolicyVersionStatus.ACTIVE,
            averaging_method=AveragingMethod.UNIT_WEIGHTED,
        )
        .first()
    )
    if existing is not None:
        scope = "all grade levels" if existing.effective_grade_level_id is None else "one grade level"
        return [
            f"= a unit-weighted ACTIVE policy version already exists "
            f"(v{existing.version_number}, {scope}) — leaving it"
        ]

    baseline = (
        session.query(GradingPolicyVersion)
        .order_by(GradingPolicyVersion.version_number.desc())
        .first()
    )
    if baseline is None:
        return ["! no grading policy version to base Grade 11's on — run the seed first"]

    policy = session.get(GradingPolicy, baseline.grading_policy_id)
    next_number = baseline.version_number + 1
    change = (
        f"grading policy {policy.name if policy else '?'}: new ACTIVE v{next_number} "
        f"for all grade levels / {school_year.name}, UNIT_WEIGHTED, "
        f"average_from_unrounded_finals=True, combine_language_pair_in_term_average=True"
    )
    if confirm:
        session.add(
            GradingPolicyVersion(
                grading_policy_id=baseline.grading_policy_id,
                version_number=next_number,
                effective_school_year_id=school_year.id,
                # NULL: both grade levels, because the school is a pilot.
                effective_grade_level_id=None,
                passing_grade=baseline.passing_grade,
                min_grade=baseline.min_grade,
                max_grade=baseline.max_grade,
                averaging_method=AveragingMethod.UNIT_WEIGHTED,
                # The arithmetic DO 017's own worked examples perform.
                average_from_unrounded_finals=True,
                # DO 017 Table 1: the language pair is one core subject, so
                # it is counted once. It still prints as a parent row with
                # its components indented beneath.
                combine_language_pair_in_term_average=True,
                status=PolicyVersionStatus.ACTIVE,
            )
        )
    return [change]


def report_undecided(session) -> list[str]:
    """Step 5 — every offering that would still fall through to 1 unit.

    Reported rather than guessed. A wrong unit produces a plausible wrong
    average, which is the failure mode this whole area is prone to, so the
    only safe default is to say so out loud.
    """
    from app.curriculum_policy import load_offering_units

    offerings = session.query(SectionSubjectOffering).all()
    units = load_offering_units(session, offerings)
    subjects = {s.id: s for s in session.query(Subject).all()}

    unresolved: dict = {}
    for offering in offerings:
        if units.get(offering.id) != Decimal(1):
            continue
        subject = subjects.get(offering.subject_id)
        if subject is None:
            continue
        unresolved.setdefault(subject.code, subject.official_name)

    if not unresolved:
        return []
    lines = [
        "",
        f"{len(unresolved)} subject(s) have no units and would each count as 1.",
        "DO 017 does not settle these from their category alone — set",
        "`subjects.units_per_term` for each (80h/term = 3, 160h/term = 6,",
        "320h/term = 12, 160h across 3 terms = 2):",
    ]
    lines += [f"  - {code}  {name}" for code, name in sorted(unresolved.items())]
    return lines


def recompute(session, school_year: SchoolYear, confirm: bool) -> list[str]:
    """Rebuild the derived caches for every non-finalized enrollment.

    Finalized years are skipped: rule 6 says a policy change never rewrites
    a year that has already been closed out. Reopening one is an explicit,
    audited act, and recomputing it happens then.
    """
    from app.grading_service import recompute_enrollment_grades

    enrollments = (
        session.query(Enrollment).filter_by(school_year_id=school_year.id).all()
    )
    # The finalization record is the authority, not
    # `annual_grade_summaries.completion_status` — that says every grade is
    # in, which is a precondition of finalizing, not the same thing.
    finalized = {
        row.enrollment_id
        for row in session.query(GradeFinalizationRecord)
        .filter_by(
            scope_type=FinalizationScopeType.ANNUAL_ENROLLMENT,
            status=FinalizationRecordStatus.FINALIZED,
        )
        .all()
        if row.enrollment_id is not None
    }

    todo = [e for e in enrollments if e.id not in finalized]
    lines = [
        "",
        f"recompute: {len(todo)} enrollment(s) to rebuild, "
        f"{len(enrollments) - len(todo)} finalized and left alone",
    ]
    if not confirm:
        return lines

    for index, enrollment in enumerate(todo, 1):
        recompute_enrollment_grades(session, enrollment.id)
        if index % 50 == 0:
            print(f"  ... {index}/{len(todo)}")
    lines.append(f"recompute: rebuilt {len(todo)} enrollment(s)")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="actually write")
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="also rebuild the derived grade caches (do this outside encoding hours)",
    )
    parser.add_argument(
        "--school-year", help="school year name (default: the ACTIVE one)", default=None
    )
    args = parser.parse_args()

    session = SessionLocal()
    try:
        if args.school_year:
            school_year = (
                session.query(SchoolYear).filter_by(name=args.school_year).one_or_none()
            )
        else:
            school_year = (
                session.query(SchoolYear)
                .filter_by(status="ACTIVE")
                .order_by(SchoolYear.name.desc())
                .first()
            )
        if school_year is None:
            print("No school year found. Pass --school-year.")
            return

        print(f"School year: {school_year.name}")

        # Stage the writes into the session **either way**, so the report
        # below describes the state these changes would produce rather than
        # the one they replace. Read-only against the pre-state it listed
        # every subject as unresolved, including the cores that are about to
        # get 2 units from their category — a report nobody could act on.
        # A dry run rolls the session back at the end instead of committing.
        lines = apply_units(session, confirm=True)
        lines += activate_sshs_policy(session, school_year, confirm=True)

        for line in lines:
            print(line)
        if not lines:
            print("Nothing to change — units and the policy are already set.")

        for line in report_undecided(session):
            print(line)

        if args.confirm:
            session.commit()
        else:
            session.rollback()

        if args.recompute:
            # After the commit/rollback above, so a dry run reports the
            # counts without the staged units still sitting in the session.
            for line in recompute(session, school_year, args.confirm):
                print(line)

        if not args.confirm:
            print("\nDry run — nothing written. Re-run with --confirm.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
