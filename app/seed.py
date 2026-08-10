"""Phase 2 seed data: school identity, grade levels, tracks/strands,
subject catalog, roles, grading policy, and SY 2026-2027 with its terms.

Idempotent — safe to re-run. Looks up each row by its natural unique key
(code/name) and only inserts if missing; never overwrites an existing row,
so admin edits made later through the app are not clobbered by a re-run.

Scope note: this seeds *reference/catalog* data only. Sections, subject
profiles, and section subject offerings are Phase 3/4 (they depend on
teacher/adviser user accounts and enrollment that don't exist yet) — see
CLAUDE.md's development phases.

Subject `subject_category_id` assignments below are best-effort based on
the master spec's descriptions (`docs/master-spec.md` Sections 7, 9,
12-13) — the spec names the six category weight-profiles but does not
explicitly map every subject to one. Verify these against the official
DepEd curriculum guide before relying on them for reporting; they're a
plain FK column, safe to correct later.

Term dates are the real SY 2026-2027 school calendar as provided, not
hardcoded into any calculation — they're ordinary editable rows, exactly
so DepEd calendar revisions don't require a code change (see CLAUDE.md
rule: "Do not permanently hardcode Term 1/2/3 dates").
"""

from datetime import date

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.academic_structure import GradeLevel, Strand, Track
from app.models.awards import AwardPolicy, AwardPolicyVersion
from app.models.enums import AwardScope, PolicyVersionStatus, SchoolYearStatus
from app.models.organization import School, SchoolYear, Term
from app.models.rbac import Role
from app.models.subjects import (
    CombinedLearningArea,
    CombinedLearningAreaComponent,
    GradingPolicy,
    GradingPolicyVersion,
    Subject,
    SubjectCategory,
)


def get_or_create(session: Session, model, defaults: dict | None = None, **lookup):
    instance = session.query(model).filter_by(**lookup).one_or_none()
    if instance is not None:
        return instance, False
    instance = model(**lookup, **(defaults or {}))
    session.add(instance)
    session.flush()
    return instance, True


ROLES = [
    ("SUPER_ADMIN", "Super Administrator / ICT Administrator"),
    ("REGISTRAR", "School Administrator / Registrar"),
    ("ADVISER", "Class Adviser"),
    ("SUBJECT_TEACHER", "Subject Teacher"),
    ("ATTENDANCE_ENCODER", "Attendance Encoder"),
    ("SCHOOL_HEAD", "School Head / Read-Only Viewer"),
]

GRADE_LEVELS = [("G11", "Grade 11", 1), ("G12", "Grade 12", 2)]

TRACKS = [("ACADEMIC", "Academic", 1), ("TECHPRO", "TechPro", 2)]

STRANDS = [
    # (track_code, strand_code, name, display_order)
    ("ACADEMIC", "ASSH", "Arts, Social Sciences, and Humanities", 1),
    ("ACADEMIC", "BE", "Business and Entrepreneurship", 2),
    ("ACADEMIC", "STEM", "Science, Technology, Engineering, and Mathematics", 3),
    ("TECHPRO", "CP", "ICT Support and Computer Programming Technologies", 1),
    ("TECHPRO", "EMS", "Hospitality and Tourism (Events Management Services)", 2),
    ("TECHPRO", "ICT", "ICT Support and Computer Programming Technologies", 3),
    ("TECHPRO", "HE", "Hospitality and Tourism (Home Economics)", 4),
]

SUBJECT_CATEGORIES = [
    # Split from the spec's single "Core / Other Academic Elective" weight
    # profile (§9) — that grouping no longer matters now that grade entry
    # is direct-only (Mode B), but the term-offering distinction it
    # papered over does: CORE_SUBJECT is offered/averaged across all 3
    # terms, OTHER_ACADEMIC_ELECTIVE is offered in a single term only.
    ("CORE_SUBJECT", "Core Subject"),
    ("OTHER_ACADEMIC_ELECTIVE", "Other Academic Elective"),
    ("FIELD_EXPOSURE_ARTS_CPI", "Field Exposure / Arts Apprenticeship / Creative Production & Innovation"),
    ("ARTS_SPORTS_HEALTH", "Arts / Sports / Health and Wellness"),
    ("RESEARCH_DESIGN_INNOVATION", "Research / Design and Innovation"),
    ("TECHPRO_ELECTIVE", "TechPro Elective"),
    ("WORK_IMMERSION", "Work Immersion"),
]

# (code, official_name, short_name, grade_level, category, track_restriction)
SUBJECTS = [
    # G11 core — common to every G11 profile, Academic and TechPro alike;
    # offered and averaged across all 3 terms
    ("G11-EFFCOMM", "Effective Communication", "Eff. Comm.", "G11", "CORE_SUBJECT", None),
    ("G11-MABKOM", "Mabisang Komunikasyon", "Mab. Kom.", "G11", "CORE_SUBJECT", None),
    ("G11-GENMATH", "General Mathematics", "Gen. Math", "G11", "CORE_SUBJECT", None),
    ("G11-GENSCI", "General Science", "Gen. Science", "G11", "CORE_SUBJECT", None),
    ("G11-LCS", "Life and Career Skills", "LCS", "G11", "CORE_SUBJECT", None),
    ("G11-PKLP", "Pag-aaral ng Kasaysayan at Lipunang Pilipino", "PKLP", "G11", "CORE_SUBJECT", None),
    # G11 ASSH electives — single term only
    ("G11-CC1", "Creative Composition 1", "Creative Comp. 1", "G11", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G11-CC2", "Creative Composition 2", "Creative Comp. 2", "G11", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G11-WKAF", "Wika at Komunikasyon sa Akademikong Filipino", "WKAF", "G11", "OTHER_ACADEMIC_ELECTIVE", None),
    # G11 BE electives — single term only
    ("G11-BACCT", "Basic Accounting", "Basic Acctg.", "G11", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G11-IOM", "Introduction to Organization and Management", "Intro to Org. & Mgmt.", "G11", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G11-BFIT", "Business Finance and Income Taxation", "Bus. Finance & Income Tax", "G11", "OTHER_ACADEMIC_ELECTIVE", None),
    # G11 STEM electives — single term only
    ("G11-BIO1", "Biology 1", "Biology 1", "G11", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G11-BIO2", "Biology 2", "Biology 2", "G11", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G11-CHEM1", "Chemistry 1", "Chemistry 1", "G11", "OTHER_ACADEMIC_ELECTIVE", None),
    # G11 TechPro
    ("G11-TP-COMPPROG", "Computer Programming (.NET Technology) NC III", "Comp. Programming NC III", "G11", "TECHPRO_ELECTIVE", "TECHPRO"),
    ("G11-TP-EMS", "Events Management Services NC III", "Events Mgmt. Services NC III", "G11", "TECHPRO_ELECTIVE", "TECHPRO"),
    # G12 ASSH / BE / STEM Term 1 (Terms 2-3 configurable per section, §13) — single term only
    ("G12-PHILO", "Introduction to Philosophy of the Human Person", "Intro to Philo.", "G12", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G12-PPG", "Philippine Politics and Governance", "Phil. Politics & Governance", "G12", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G12-CONTLIT1", "Contemporary Literature 1", "Contemporary Lit. 1", "G12", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G12-FILSPORT", "Filipino sa Isport", "Filipino sa Isport", "G12", "ARTS_SPORTS_HEALTH", None),
    ("G12-MALIKHAING", "Malikhaing Pagsulat", "Malikhaing Pagsulat", "G12", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G12-BFBE", "Business Finance and Business Economics", "Bus. Finance & Econ.", "G12", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G12-PRECALC", "Pre-Calculus", "Pre-Calculus", "G12", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G12-BIO3", "Biology 3", "Biology 3", "G12", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G12-GENPHYS1", "General Physics 1", "Gen. Physics 1", "G12", "OTHER_ACADEMIC_ELECTIVE", None),
    ("G12-GENCHEM1", "General Chemistry 1", "Gen. Chemistry 1", "G12", "OTHER_ACADEMIC_ELECTIVE", None),
    # G12 TechPro
    ("G12-TP-CSS", "Computer Systems Servicing NC II", "Comp. Systems Servicing NC II", "G12", "TECHPRO_ELECTIVE", "TECHPRO"),
    ("G12-WORKIMM", "Work Immersion", "Work Immersion", "G12", "WORK_IMMERSION", None),
    ("G12-TP-CCS", "Contact Center Services", "Contact Center Services", "G12", "TECHPRO_ELECTIVE", "TECHPRO"),
    ("G12-TP-KITCHEN", "Kitchen Operations", "Kitchen Operations", "G12", "TECHPRO_ELECTIVE", "TECHPRO"),
    ("G12-TP-TOURISM", "Tourism Services", "Tourism Services", "G12", "TECHPRO_ELECTIVE", "TECHPRO"),
]


def seed() -> None:
    session = SessionLocal()
    try:
        school, _ = get_or_create(
            session,
            School,
            deped_school_id="301040",
            defaults=dict(
                school_name="Francisco G. Nepomuceno Memorial High School",
                region="Region III",
                schools_division="Schools Division of Angeles City",
                district="1st District",
                address="Angeles City, Pampanga",
                school_head_name="Hermes P. Vargas",
                school_head_position="Principal IV",
            ),
        )

        roles_by_code = {}
        for code, name in ROLES:
            role, _ = get_or_create(session, Role, code=code, defaults=dict(name=name))
            roles_by_code[code] = role

        grade_levels_by_code = {}
        for code, name, order in GRADE_LEVELS:
            gl, _ = get_or_create(
                session, GradeLevel, code=code, defaults=dict(name=name, display_order=order)
            )
            grade_levels_by_code[code] = gl

        tracks_by_code = {}
        for code, name, order in TRACKS:
            track, _ = get_or_create(
                session, Track, code=code, defaults=dict(name=name, display_order=order)
            )
            tracks_by_code[code] = track

        for track_code, strand_code, name, order in STRANDS:
            get_or_create(
                session,
                Strand,
                track_id=tracks_by_code[track_code].id,
                code=strand_code,
                defaults=dict(name=name, display_order=order),
            )

        categories_by_code = {}
        for code, name in SUBJECT_CATEGORIES:
            cat, _ = get_or_create(session, SubjectCategory, code=code, defaults=dict(name=name))
            categories_by_code[code] = cat

        school_year, _ = get_or_create(
            session,
            SchoolYear,
            name="2026-2027",
            defaults=dict(
                school_id=school.id,
                start_date=date(2026, 6, 8),
                end_date=date(2027, 4, 8),
                status=SchoolYearStatus.ACTIVE,
            ),
        )

        TERMS = [
            (1, "Term 1", date(2026, 6, 8), date(2026, 9, 15)),
            (2, "Term 2", date(2026, 9, 16), date(2026, 12, 18)),
            (3, "Term 3", date(2027, 1, 4), date(2027, 4, 8)),
        ]
        for number, name, start, end in TERMS:
            get_or_create(
                session,
                Term,
                school_year_id=school_year.id,
                term_number=number,
                defaults=dict(name=name, start_date=start, end_date=end),
            )

        grading_policy, _ = get_or_create(
            session,
            GradingPolicy,
            name="Standard SHS Grading",
            defaults=dict(description="Passing-grade policy for direct term-grade entry (Mode B)."),
        )
        get_or_create(
            session,
            GradingPolicyVersion,
            grading_policy_id=grading_policy.id,
            version_number=1,
            defaults=dict(
                effective_school_year_id=school_year.id,
                passing_grade=75,
                min_grade=60,
                max_grade=100,
                status=PolicyVersionStatus.ACTIVE,
            ),
        )

        subjects_by_code = {}
        for code, official_name, short_name, gl_code, cat_code, track_code in SUBJECTS:
            subject, _ = get_or_create(
                session,
                Subject,
                code=code,
                defaults=dict(
                    official_name=official_name,
                    short_name=short_name,
                    grade_level_id=grade_levels_by_code[gl_code].id,
                    subject_category_id=categories_by_code[cat_code].id,
                    track_restriction_id=(
                        tracks_by_code[track_code].id if track_code else None
                    ),
                ),
            )
            subjects_by_code[code] = subject

        combined_area, _ = get_or_create(
            session,
            CombinedLearningArea,
            name="Effective Communication / Mabisang Komunikasyon",
            grade_level_id=grade_levels_by_code["G11"].id,
            defaults=dict(display_order=1),
        )
        for i, code in enumerate(["G11-EFFCOMM", "G11-MABKOM"]):
            get_or_create(
                session,
                CombinedLearningAreaComponent,
                subject_id=subjects_by_code[code].id,
                defaults=dict(combined_learning_area_id=combined_area.id, display_order=i),
            )

        # Two selectable award policies (§24) — never permanently merged.
        # Academic Excellence is the current DepEd DO 15, s. 2026 policy;
        # Legacy Tiered Honors is the older workbook's honors mode, kept
        # available as a separate selectable policy version, not a
        # default. Both effective for SY 2026-2027; a school admin picks
        # which to apply when computing awards.
        academic_excellence_policy, _ = get_or_create(
            session,
            AwardPolicy,
            name="Academic Excellence Award (DO 15, s. 2026)",
            defaults=dict(
                description="Complete record, no derogatory record, General Average >= 90, "
                "lowest Final Grade >= 80."
            ),
        )
        get_or_create(
            session,
            AwardPolicyVersion,
            award_policy_id=academic_excellence_policy.id,
            version_number=1,
            defaults=dict(
                effective_school_year_id=school_year.id,
                # Judged once a year on the General Average across all
                # three terms (§24).
                scope=AwardScope.ANNUAL,
                require_complete_record=True,
                require_no_derogatory_record=True,
                min_general_average=90,
                min_lowest_final_grade=80,
                require_no_failed_subject=False,
                status=PolicyVersionStatus.ACTIVE,
            ),
        )

        legacy_honors_policy, _ = get_or_create(
            session,
            AwardPolicy,
            name="Legacy Tiered Honors",
            defaults=dict(
                description="Awarded per term on the Term Average: >= 98 With Highest "
                "Honors; >= 95 With High Honors; >= 90 With Honors; no failed subject."
            ),
        )
        get_or_create(
            session,
            AwardPolicyVersion,
            award_policy_id=legacy_honors_policy.id,
            version_number=1,
            defaults=dict(
                effective_school_year_id=school_year.id,
                # Judged separately for each term against that term's
                # Term Average (§17), so a learner can make Honors in one
                # term and miss it in another.
                scope=AwardScope.TERM,
                require_complete_record=True,
                require_no_derogatory_record=True,
                require_no_failed_subject=True,
                tier_thresholds=[
                    {"label": "WITH HIGHEST HONORS", "min_general_average": 98},
                    {"label": "WITH HIGH HONORS", "min_general_average": 95},
                    {"label": "WITH HONORS", "min_general_average": 90},
                ],
                status=PolicyVersionStatus.ACTIVE,
            ),
        )

        session.commit()
        print("Seed complete.")
    finally:
        session.close()


if __name__ == "__main__":
    seed()
