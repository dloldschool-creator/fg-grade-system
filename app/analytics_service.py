"""Aggregate queries behind Overview → Insights.

**Deliberately free of Streamlit and of any grade arithmetic.** Two
reasons, both structural:

1. Nothing here computes a grade. Rule 1 and §65 give one implementation
   of every official formula, and a second one living in an analytics
   page would drift from the report card without anything looking wrong.
   Anything that averages must read the already-computed summary tables
   (`term_grade_summaries`, `annual_grade_summaries`,
   `subject_final_grades`); this module counts rows and nothing more.
2. No `import streamlit` means these functions are unit-testable and the
   page owns caching. `st.cache_data` needs picklable arguments and
   returns, which is why every function here takes scalars and returns
   frozen dataclasses of primitives.

**The cost model is different from the rest of the app.** Elsewhere the
only thing that matters is round-trip count (~85ms each). Here row
*volume* matters too: ~1,200 learners × ~9 subjects × 3 terms is ~32,000
`term_grades`, and none of them may reach Python. Every function below
aggregates in SQL and returns one row per section per term — tens of
rows, whatever the roster does. `tests/test_analytics_service.py` asserts
the query count stays flat.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timezone

from sqlalchemy import and_, case, func, or_

from app.models.academic_structure import GradeLevel, Section, Strand, Track
from app.models.enums import (
    CompletionStatus,
    EnrollmentStatus,
    GradeWorkflowStatus,
    OfferingStatus,
)
from app.models.grades import TermGrade, TermGradeSummary
from app.models.learners import Enrollment, Learner
from app.models.organization import Term
from app.models.subjects import SectionSubjectOffering, Subject

# A learner still on the roll for grading purposes. The same four the
# Gradebook and the Dashboard use — headcounts have to agree across the
# app, or this page's encoding percentage disagrees with the gradebook a
# teacher is looking at while reading it.
ACTIVE_ENROLLMENT_STATUSES = frozenset(
    {
        EnrollmentStatus.ENROLLED,
        EnrollmentStatus.LATE_ENROLLMENT,
        EnrollmentStatus.TRANSFERRED_IN,
        EnrollmentStatus.SHIFTED_IN,
    }
)

# A grade that has left the teacher's hands. Rule 7 runs
# DRAFT → SUBMITTED → VERIFIED → FINALIZED, so everything past DRAFT
# counts as handed in — a finalized grade was submitted once, and a
# teacher reading "12 encoded, 0 submitted" on a finalized class would
# be told to do something already done.
SUBMITTED_OR_BEYOND = frozenset(
    {
        GradeWorkflowStatus.SUBMITTED,
        GradeWorkflowStatus.VERIFIED,
        GradeWorkflowStatus.FINALIZED,
    }
)


def advised_section_ids(session, school_year_id, adviser_user_id) -> tuple:
    """The sections one adviser holds, as a tuple of ids.

    **Compared in SQL, never in Python.** `AuthUser.id` is a `str` and
    `sections.adviser_user_id` is a `uuid.UUID`; Postgres coerces between
    them so this filter matches, while the same two values compared in
    Python never would. That mismatch has already shipped once here. When
    you hold a Section object rather than a query, use
    `app.section_access.is_advised_by` instead — it exists for exactly
    this and coerces both sides.

    An adviser may hold **more than one** section: there is deliberately
    no constraint against it, and one of this school's advisers holds
    two. Nothing downstream may assume a single section.

    Returned as a tuple so it can go straight into a `st.cache_data` key,
    which is how the page keeps one adviser from being served another's
    cached rows.
    """
    if adviser_user_id is None:
        return ()
    rows = (
        session.query(Section.id)
        .filter(
            Section.school_year_id == school_year_id,
            Section.adviser_user_id == adviser_user_id,
        )
        .order_by(Section.name)
        .all()
    )
    return tuple(row[0] for row in rows)


def taught_offering_ids(session, school_year_id, teacher_user_id) -> tuple:
    """The offerings one subject teacher actively holds.

    **A subject teacher is scoped by offering, not by section, and the
    difference is a privacy boundary rather than a convenience.** An
    adviser owns whole sections; a subject teacher owns one subject
    inside sections whose other subjects are none of their business.
    Handing them their sections' ids instead would show them, through
    the distribution and the difficulty ranking, every other teacher's
    grades for that class — and through `at_risk_learners`, which reads
    whole-term averages, a learner's standing in subjects they do not
    teach. Anything a subject teacher sees has to come from
    `term_grades` rows on offerings in this tuple.

    Active assignments only, the same rule the Gradebook uses, so a
    reassigned teacher stops seeing the class as soon as it moves.
    """
    if teacher_user_id is None:
        return ()

    from app.models.subjects import TeacherAssignment

    rows = (
        session.query(SectionSubjectOffering.id)
        .join(
            TeacherAssignment,
            TeacherAssignment.section_subject_offering_id == SectionSubjectOffering.id,
        )
        .filter(
            SectionSubjectOffering.school_year_id == school_year_id,
            TeacherAssignment.teacher_user_id == teacher_user_id,
            TeacherAssignment.is_active.is_(True),
        )
        .all()
    )
    return tuple(row[0] for row in rows)


def _sections_in_scope(session, school_year_id, section_ids):
    """Sections for the year, narrowed to `section_ids` when given.

    `None` means the whole school — the admin, registrar and school-head
    view. An **empty tuple is not the same as None**: it means a scoped
    viewer who holds no sections, and must return nothing rather than
    everything. Getting that backwards is how a scoped page shows the
    whole school to someone entitled to none of it.
    """
    query = session.query(Section).filter_by(school_year_id=school_year_id)
    if section_ids is not None:
        if not section_ids:
            return {}
        query = query.filter(Section.id.in_(section_ids))
    return {s.id: s for s in query.all()}


@dataclass(frozen=True)
class EncodingRow:
    """One section × term. Carries its own dimensions so the page can
    filter without going back to the database — see `encoding_progress`."""

    section_id: uuid.UUID
    section_name: str
    grade_level_id: uuid.UUID | None
    grade_level_name: str
    grade_level_order: int
    track_id: uuid.UUID | None
    track_name: str
    strand_id: uuid.UUID | None
    strand_name: str
    term_id: uuid.UUID
    term_name: str
    term_number: int
    # Carried so the page can say *why* a term looks unfinished without a
    # second query: 40% encoded reads as alarming until you notice the
    # term is CLOSED and nobody is meant to be encoding yet.
    encoding_status: str
    submission_deadline: date | None
    active_learners: int
    offerings: int
    placeholder_offerings: int
    encoded: int

    @property
    def expected(self) -> int:
        """Every learner on the roll is graded on every offering the
        section runs that term — offerings are per section (§48), not
        chosen per learner."""
        return self.active_learners * self.offerings

    @property
    def missing(self) -> int:
        return max(self.expected - self.encoded, 0)

    @property
    def percent(self) -> float | None:
        """None, never 0.0, when nothing is expected.

        A section with no offerings yet is not 0% encoded — it is not yet
        askable, and reporting 0% would sort it to the top of a "furthest
        behind" list ahead of sections where teachers are genuinely late.
        Same reasoning as rule 2: absent is not zero.
        """
        if self.expected == 0:
            return None
        return 100.0 * self.encoded / self.expected


def encoding_progress(session, school_year_id, section_ids=None) -> list[EncodingRow]:
    """How far grade encoding has got, per section per term.

    Returns every section × term the viewer may see — the whole year when
    `section_ids` is None, otherwise just those sections. The caller then
    filters the returned rows in Python, and that split is on purpose:
    **access scoping belongs in SQL, display filtering does not.** The
    result is one row per section per term — 30 × 3 today — so the
    dropdowns can slice a cached list for free, while a viewer never has
    rows loaded that they are not entitled to. Pushing the
    grade-level/strand/section *display* filters into SQL as well would
    put an 85ms round trip behind every dropdown for no gain.

    Rows come back ordered by grade level, track, strand, section, term.

    **`expected` counts PLACEHOLDER offerings too**, even though §48 says
    a placeholder is not yet a usable offering. The Gradebook does not
    filter on offering status, so a teacher can and does encode against
    one; excluding placeholders from the denominator while their grades
    landed in the numerator is how a section comes to report 104%
    encoded. The count is reported separately instead, as the
    data-readiness warning it actually is.
    """
    terms = (
        session.query(Term)
        .filter_by(school_year_id=school_year_id)
        .order_by(Term.term_number)
        .all()
    )
    sections = list(_sections_in_scope(session, school_year_id, section_ids).values())
    if not terms or not sections:
        return []

    grade_levels = {g.id: g for g in session.query(GradeLevel).all()}
    tracks = {t.id: t for t in session.query(Track).all()}
    strands = {s.id: s for s in session.query(Strand).all()}

    active = _active_learners_by_section(session, school_year_id)
    offerings, placeholders = _offerings_by_section_term(session, school_year_id)
    encoded = _encoded_by_section_term(session, school_year_id)

    rows = []
    for section in sections:
        grade_level = grade_levels.get(section.grade_level_id)
        track = tracks.get(section.track_id)
        strand = strands.get(section.strand_id)
        for term in terms:
            key = (section.id, term.id)
            rows.append(
                EncodingRow(
                    section_id=section.id,
                    section_name=section.name,
                    grade_level_id=section.grade_level_id,
                    grade_level_name=grade_level.name if grade_level else "",
                    grade_level_order=grade_level.display_order if grade_level else 0,
                    track_id=section.track_id,
                    track_name=track.name if track else "",
                    strand_id=section.strand_id,
                    strand_name=strand.name if strand else "",
                    term_id=term.id,
                    term_name=term.name,
                    term_number=term.term_number,
                    encoding_status=(
                        term.grade_encoding_status.value
                        if term.grade_encoding_status
                        else ""
                    ),
                    submission_deadline=term.submission_deadline,
                    active_learners=active.get(section.id, 0),
                    offerings=offerings.get(key, 0),
                    placeholder_offerings=placeholders.get(key, 0),
                    encoded=encoded.get(key, 0),
                )
            )

    rows.sort(
        key=lambda r: (
            r.grade_level_order,
            r.track_name,
            r.strand_name,
            r.section_name,
            r.term_number,
        )
    )
    return rows


@dataclass(frozen=True)
class OfferingProgressRow:
    """One section × subject × term — the grain a class adviser needs.

    Section × term answers "am I behind"; this answers "on what, and who
    do I ask". The adviser does not encode these grades — the subject
    teacher does — but the adviser owns the report card, so chasing is
    their job and the teacher's name is the actionable column.
    """

    section_id: uuid.UUID
    section_name: str
    subject_id: uuid.UUID
    subject_name: str
    subject_code: str
    term_id: uuid.UUID
    term_name: str
    term_number: int
    # Empty when the offering carries no active assignment. That is a real
    # state worth seeing, not a gap to hide: an unassigned offering is one
    # nobody has been asked to encode.
    teacher_name: str
    display_order: int
    active_learners: int
    encoded: int
    # Grades the teacher has actually handed in. Encoding and submitting
    # are separate steps (rule 7), so a class can be fully typed up and
    # still not submitted — the state a teacher most needs to be told.
    submitted: int
    term_encoding_status: str
    submission_deadline: date | None

    @property
    def expected(self) -> int:
        return self.active_learners

    @property
    def missing(self) -> int:
        return max(self.expected - self.encoded, 0)

    @property
    def percent(self) -> float | None:
        if self.expected == 0:
            return None
        return 100.0 * self.encoded / self.expected


def offering_progress(
    session, school_year_id, section_ids=None, offering_ids=None
) -> list[OfferingProgressRow]:
    """Encoding progress per subject per term, for a named set of classes.

    Takes **either** scope and never neither: `section_ids` for an
    adviser or an admin looking at one section, `offering_ids` for a
    subject teacher, who owns particular classes rather than whole
    sections. Passing nothing returns nothing — running this school-wide
    would be ~810 rows of detail no page asks for, and a scoped caller
    who resolved to an empty set must get an empty answer rather than
    everything.

    Teacher names come from the **active** assignment on each offering
    (`teacher_assignments.is_active`), the same source Teacher
    Assignments writes, so a reassignment shows here immediately rather
    than through a copied column that would need syncing.
    """
    if not section_ids and not offering_ids:
        return []

    from app.models.rbac import User
    from app.models.subjects import TeacherAssignment

    query = session.query(SectionSubjectOffering).filter(
        SectionSubjectOffering.school_year_id == school_year_id
    )
    if offering_ids:
        query = query.filter(SectionSubjectOffering.id.in_(offering_ids))
    if section_ids:
        query = query.filter(SectionSubjectOffering.section_id.in_(section_ids))
    offerings = query.all()
    if not offerings:
        return []

    # Scoped by the offerings that survived, so a teacher gets the
    # sections their classes are in without being handed the section
    # scope itself.
    sections = {
        s.id: s
        for s in session.query(Section)
        .filter(Section.id.in_({o.section_id for o in offerings}))
        .all()
    }
    terms = {
        t.id: t for t in session.query(Term).filter_by(school_year_id=school_year_id).all()
    }
    subjects = {
        s.id: s
        for s in session.query(Subject)
        .filter(Subject.id.in_({o.subject_id for o in offerings}))
        .all()
    }
    active_learners = _active_learners_by_section(session, school_year_id)

    # Named apart from the `offering_ids` parameter on purpose: these are
    # the offerings that actually survived both scopes, which is not the
    # same list, and shadowing the parameter here would silently widen
    # the scope for anything added below.
    ids_for_counts = [o.id for o in offerings]
    counted = (
        session.query(
            TermGrade.section_subject_offering_id,
            func.count(TermGrade.id),
            func.count(TermGrade.id).filter(
                TermGrade.status.in_(SUBMITTED_OR_BEYOND)
            ),
        )
        .select_from(TermGrade)
        .join(Enrollment, TermGrade.enrollment_id == Enrollment.id)
        .filter(
            TermGrade.section_subject_offering_id.in_(ids_for_counts),
            TermGrade.official_grade.isnot(None),
            Enrollment.enrollment_status.in_(ACTIVE_ENROLLMENT_STATUSES),
        )
        .group_by(TermGrade.section_subject_offering_id)
        .all()
    )
    encoded = {row[0]: row[1] for row in counted}
    submitted = {row[0]: row[2] for row in counted}
    assignments = {
        a.section_subject_offering_id: a
        for a in session.query(TeacherAssignment)
        .filter(
            TeacherAssignment.section_subject_offering_id.in_(ids_for_counts),
            TeacherAssignment.is_active.is_(True),
        )
        .all()
    }
    teachers = {}
    if assignments:
        teachers = {
            u.id: u
            for u in session.query(User)
            .filter(User.id.in_({a.teacher_user_id for a in assignments.values()}))
            .all()
        }

    rows = []
    for offering in offerings:
        section = sections.get(offering.section_id)
        term = terms.get(offering.term_id)
        if section is None or term is None:
            continue
        subject = subjects.get(offering.subject_id)
        assignment = assignments.get(offering.id)
        teacher = teachers.get(assignment.teacher_user_id) if assignment else None
        rows.append(
            OfferingProgressRow(
                section_id=section.id,
                section_name=section.name,
                subject_id=offering.subject_id,
                subject_name=subject.official_name if subject else "",
                subject_code=subject.code if subject else "",
                term_id=term.id,
                term_name=term.name,
                term_number=term.term_number,
                teacher_name=(teacher.full_name if teacher else ""),
                display_order=offering.display_order or 0,
                active_learners=active_learners.get(section.id, 0),
                encoded=encoded.get(offering.id, 0),
                submitted=submitted.get(offering.id, 0),
                term_encoding_status=(
                    term.grade_encoding_status.value if term.grade_encoding_status else ""
                ),
                submission_deadline=term.submission_deadline,
            )
        )

    # Least done first, so the subject to chase is at the top. Ties go to
    # the section's own display order, then the subject name.
    rows.sort(
        key=lambda r: (
            r.percent if r.percent is not None else 1e9,
            r.section_name,
            r.term_number,
            r.display_order,
            r.subject_name,
        )
    )
    return rows


def _active_learners_by_section(session, school_year_id) -> dict:
    return dict(
        session.query(Enrollment.section_id, func.count(Enrollment.id))
        .filter(
            Enrollment.school_year_id == school_year_id,
            Enrollment.enrollment_status.in_(ACTIVE_ENROLLMENT_STATUSES),
        )
        .group_by(Enrollment.section_id)
        .all()
    )


def _offerings_by_section_term(session, school_year_id) -> tuple[dict, dict]:
    """`(all offerings, placeholders only)`, both keyed by (section, term)."""
    rows = (
        session.query(
            SectionSubjectOffering.section_id,
            SectionSubjectOffering.term_id,
            SectionSubjectOffering.status,
            func.count(SectionSubjectOffering.id),
        )
        .filter(SectionSubjectOffering.school_year_id == school_year_id)
        .group_by(
            SectionSubjectOffering.section_id,
            SectionSubjectOffering.term_id,
            SectionSubjectOffering.status,
        )
        .all()
    )
    total: dict = {}
    placeholder: dict = {}
    for section_id, term_id, status, count in rows:
        key = (section_id, term_id)
        total[key] = total.get(key, 0) + count
        if status == OfferingStatus.PLACEHOLDER:
            placeholder[key] = placeholder.get(key, 0) + count
    return total, placeholder


def _encoded_by_section_term(session, school_year_id) -> dict:
    """Grade rows that actually carry a number.

    `official_grade IS NOT NULL` is the whole definition (rule 2): the
    Gradebook creates a DRAFT row the moment it draws the grid, so a row
    existing means the class was opened, not that anyone was graded.

    Joined back to `enrollments` and filtered to the same four statuses
    the denominator uses. Without it, a grade encoded before a learner
    transferred out stays in the numerator while the learner has left the
    denominator, and the section reports more than 100%.
    """
    rows = (
        session.query(
            SectionSubjectOffering.section_id,
            SectionSubjectOffering.term_id,
            func.count(TermGrade.id),
        )
        .select_from(TermGrade)
        .join(
            SectionSubjectOffering,
            TermGrade.section_subject_offering_id == SectionSubjectOffering.id,
        )
        .join(Enrollment, TermGrade.enrollment_id == Enrollment.id)
        .filter(
            SectionSubjectOffering.school_year_id == school_year_id,
            TermGrade.official_grade.isnot(None),
            Enrollment.enrollment_status.in_(ACTIVE_ENROLLMENT_STATUSES),
        )
        .group_by(SectionSubjectOffering.section_id, SectionSubjectOffering.term_id)
        .all()
    )
    return {(section_id, term_id): count for section_id, term_id, count in rows}


def roll_up(rows) -> tuple[int, int, float | None]:
    """`(encoded, expected, percent)` over a set of rows.

    **The percentage is recomputed from the totals, never averaged from
    the rows' own.** Averaging per-section percentages weights a
    5-learner SNED section the same as a 45-learner one, and the same
    mistake in its other direction — summing a percentage column — has
    already shipped twice here, in SF2 and again in SF4. A caller that
    wants a total has to come through this function.
    """
    encoded = sum(r.encoded for r in rows)
    expected = sum(r.expected for r in rows)
    percent = 100.0 * encoded / expected if expected else None
    return encoded, expected, percent


# --------------------------------------------------------------------------
# Grade distribution and subject difficulty
# --------------------------------------------------------------------------
#
# **Nothing below is an official grade.** These are descriptive statistics
# over the term grades teachers have encoded — how they are spread, and
# which subjects sit lowest. No Term Average, General Average or subject
# Final Grade is computed here, and none of it is unit-weighted. Anything
# official comes from `grading_service` and is read out of the summary
# tables (rule 1, §65).
#
# Averaging *within* one subject is what makes a mean defensible at all:
# every learner taking a subject carries the same units for it, so unit
# weighting — which exists to combine *different* subjects — has nothing
# to change. A mean across subjects would be a different claim entirely,
# and is deliberately not offered here.


@dataclass(frozen=True)
class Band:
    """One grade band. `lower` is inclusive and `upper` exclusive; either
    may be None for the open end."""

    label: str
    lower: float | None
    upper: float | None


def grade_bands(passing_grade: float) -> tuple[Band, ...]:
    """Five bands, anchored to the passing mark rather than to 75.

    With the DepEd default of 75 these come out as the familiar
    Below 75 / 75-79 / 80-84 / 85-89 / 90 and above. They are deliberately
    **numeric and unnamed**: the descriptor scale everyone knows belongs
    to DO 8 s. 2015, and DO 017 s. 2026 defers the whole assessment,
    grading and awards policy to a forthcoming order. Printing descriptors
    now would assert a scheme that is mid-revision, so the page shows the
    numbers and says nothing DepEd has not.

    Anchoring to `passing_grade` matters because that threshold is a
    versioned policy value, not a constant. If a year is ever graded on a
    different mark, the below-passing band has to move with it or the
    chart quietly mislabels who failed.
    """
    step = 5.0
    edges = [passing_grade + step * i for i in range(4)]
    return (
        Band(f"Below {passing_grade:g}", None, passing_grade),
        Band(f"{edges[0]:g}–{edges[1] - 1:g}", edges[0], edges[1]),
        Band(f"{edges[1]:g}–{edges[2] - 1:g}", edges[1], edges[2]),
        Band(f"{edges[2]:g}–{edges[3] - 1:g}", edges[2], edges[3]),
        Band(f"{edges[3]:g} and above", edges[3], None),
    )


@dataclass(frozen=True)
class SubjectGradeRow:
    """Encoded grades for one section × term × subject.

    The finest grain both the distribution and the difficulty ranking
    need, so one cached fetch serves both and the page slices it — the
    same arrangement as `encoding_progress`. Around 810 rows at this
    school's size, once grades exist.
    """

    section_id: uuid.UUID
    section_name: str
    grade_level_id: uuid.UUID | None
    grade_level_name: str
    grade_level_order: int
    track_name: str
    strand_id: uuid.UUID | None
    strand_name: str
    term_id: uuid.UUID
    term_name: str
    term_number: int
    subject_id: uuid.UUID
    subject_name: str
    subject_code: str
    # Counts per band, positionally aligned with `GradeStats.bands`.
    band_counts: tuple[int, ...]
    graded: int
    # The sum of the encoded grades, kept instead of a mean so that rows
    # can be added together without averaging averages.
    total: float
    lowest: float
    highest: float

    @property
    def average(self) -> float | None:
        return self.total / self.graded if self.graded else None

    @property
    def below_passing(self) -> int:
        """The first band is the below-passing one, by construction."""
        return self.band_counts[0] if self.band_counts else 0


@dataclass(frozen=True)
class GradeStats:
    """What one school year's encoded grades look like.

    Carries `passing_grade` and `bands` alongside the rows because the
    band positions are derived from a policy value — handing back rows
    whose positions mean nothing without it is how an off-by-one band
    label happens.
    """

    passing_grade: float
    bands: tuple[Band, ...]
    rows: tuple[SubjectGradeRow, ...]

    @property
    def any_grades(self) -> bool:
        return any(row.graded for row in self.rows)


def subject_grade_stats(
    session, school_year_id, section_ids=None, offering_ids=None
) -> GradeStats:
    """Encoded term grades for a school year, per section × term × subject.

    Returns **only combinations that have at least one encoded grade** —
    unlike `encoding_progress`, which has to report the empty ones because
    emptiness is the thing it measures. Here an ungraded subject has no
    distribution to show, and a zero-count row would draw a bar at zero
    that reads as "everybody failed".

    One aggregate query does the bucketing, in Postgres. The alternative —
    fetching the grades and bucketing in Python — reads identically on the
    seeded three-learner section and moves ~32,000 rows on the real one.
    """
    from app.grading_service import resolve_passing_grade

    passing_grade = float(resolve_passing_grade(session, school_year_id))
    bands = grade_bands(passing_grade)

    # A subject teacher passes `offering_ids` and no `section_ids`: they
    # are entitled to the classes they teach, inside sections whose other
    # subjects are not theirs to see. The section lookup is then derived
    # from the offerings that survive, never used as the scope itself.
    if offering_ids is not None and not offering_ids:
        return GradeStats(passing_grade=passing_grade, bands=bands, rows=())
    sections = _sections_in_scope(session, school_year_id, section_ids)
    terms = {
        t.id: t for t in session.query(Term).filter_by(school_year_id=school_year_id).all()
    }
    if not sections or not terms:
        return GradeStats(passing_grade=passing_grade, bands=bands, rows=())

    grade_levels = {g.id: g for g in session.query(GradeLevel).all()}
    tracks = {t.id: t for t in session.query(Track).all()}
    strands = {s.id: s for s in session.query(Strand).all()}
    subjects = {s.id: s for s in session.query(Subject).all()}

    aggregated = _grades_by_section_term_subject(
        session, school_year_id, bands, offering_ids
    )

    rows = []
    for (section_id, term_id, subject_id), stats in aggregated.items():
        section = sections.get(section_id)
        term = terms.get(term_id)
        if section is None or term is None:
            continue
        subject = subjects.get(subject_id)
        grade_level = grade_levels.get(section.grade_level_id)
        track = tracks.get(section.track_id)
        strand = strands.get(section.strand_id)
        rows.append(
            SubjectGradeRow(
                section_id=section.id,
                section_name=section.name,
                grade_level_id=section.grade_level_id,
                grade_level_name=grade_level.name if grade_level else "",
                grade_level_order=grade_level.display_order if grade_level else 0,
                track_name=track.name if track else "",
                strand_id=section.strand_id,
                strand_name=strand.name if strand else "",
                term_id=term.id,
                term_name=term.name,
                term_number=term.term_number,
                subject_id=subject_id,
                subject_name=subject.official_name if subject else "",
                subject_code=subject.code if subject else "",
                band_counts=tuple(stats["bands"]),
                graded=stats["graded"],
                total=stats["total"],
                lowest=stats["lowest"],
                highest=stats["highest"],
            )
        )

    rows.sort(
        key=lambda r: (
            r.grade_level_order,
            r.strand_name,
            r.section_name,
            r.term_number,
            r.subject_name,
        )
    )
    return GradeStats(passing_grade=passing_grade, bands=bands, rows=tuple(rows))


def _band_expression(bands: tuple[Band, ...]):
    """A SQL CASE mapping a grade to its band index.

    Built from the same `bands` tuple the labels come from, so a boundary
    and its caption cannot disagree — which is exactly what would happen
    if the CASE were written out by hand beside a separate list of names.
    """
    whens = [
        (TermGrade.official_grade < band.upper, index)
        for index, band in enumerate(bands)
        if band.upper is not None
    ]
    return case(*whens, else_=len(bands) - 1)


def _grades_by_section_term_subject(
    session, school_year_id, bands, offering_ids=None
) -> dict:
    """One query. `{(section, term, subject): counts and totals}`.

    Filtered to the same four enrollment statuses as everything else on
    this page, so a section's distribution describes the same class its
    encoding percentage described.
    """
    band_expr = _band_expression(bands)
    query = (
        session.query(
            SectionSubjectOffering.section_id,
            SectionSubjectOffering.term_id,
            SectionSubjectOffering.subject_id,
            band_expr,
            func.count(TermGrade.id),
            func.sum(TermGrade.official_grade),
            func.min(TermGrade.official_grade),
            func.max(TermGrade.official_grade),
        )
        .select_from(TermGrade)
        .join(
            SectionSubjectOffering,
            TermGrade.section_subject_offering_id == SectionSubjectOffering.id,
        )
        .join(Enrollment, TermGrade.enrollment_id == Enrollment.id)
        .filter(
            SectionSubjectOffering.school_year_id == school_year_id,
            TermGrade.official_grade.isnot(None),
            Enrollment.enrollment_status.in_(ACTIVE_ENROLLMENT_STATUSES),
        )
        .group_by(
            SectionSubjectOffering.section_id,
            SectionSubjectOffering.term_id,
            SectionSubjectOffering.subject_id,
            band_expr,
        )
    )
    if offering_ids is not None:
        query = query.filter(SectionSubjectOffering.id.in_(offering_ids))
    results = query.all()

    folded: dict = {}
    for section_id, term_id, subject_id, band_index, count, total, lowest, highest in results:
        key = (section_id, term_id, subject_id)
        entry = folded.setdefault(
            key,
            {
                "bands": [0] * len(bands),
                "graded": 0,
                "total": 0.0,
                "lowest": float(lowest),
                "highest": float(highest),
            },
        )
        entry["bands"][band_index] += count
        entry["graded"] += count
        entry["total"] += float(total)
        entry["lowest"] = min(entry["lowest"], float(lowest))
        entry["highest"] = max(entry["highest"], float(highest))
    return folded


def distribution(rows, bands) -> list[tuple[Band, int]]:
    """Total count per band over a set of rows, lowest band first."""
    totals = [0] * len(bands)
    for row in rows:
        for index, count in enumerate(row.band_counts):
            totals[index] += count
    return list(zip(bands, totals))


@dataclass(frozen=True)
class SubjectDifficulty:
    subject_id: uuid.UUID
    subject_name: str
    subject_code: str
    graded: int
    below_passing: int
    average: float | None
    lowest: float | None
    highest: float | None

    @property
    def percent_below_passing(self) -> float | None:
        if not self.graded:
            return None
        return 100.0 * self.below_passing / self.graded


def subject_difficulty(rows) -> list[SubjectDifficulty]:
    """One entry per subject, hardest first.

    "Hardest" is the share of encoded grades below the passing mark, and
    the tie-break is the lower average. Ranking on the average alone
    hides the case a department head most needs to see: a subject whose
    class mostly sits at 88 but where six learners are failing.

    **Both figures are recomputed from the summed grades, never averaged
    from the sections' own averages** — a 45-learner section and a
    5-learner one do not each contribute half of a subject's mean.
    """
    grouped: dict = {}
    for row in rows:
        entry = grouped.setdefault(
            row.subject_id,
            {
                "name": row.subject_name,
                "code": row.subject_code,
                "graded": 0,
                "below": 0,
                "total": 0.0,
                "lowest": row.lowest,
                "highest": row.highest,
            },
        )
        entry["graded"] += row.graded
        entry["below"] += row.below_passing
        entry["total"] += row.total
        entry["lowest"] = min(entry["lowest"], row.lowest)
        entry["highest"] = max(entry["highest"], row.highest)

    result = [
        SubjectDifficulty(
            subject_id=subject_id,
            subject_name=entry["name"],
            subject_code=entry["code"],
            graded=entry["graded"],
            below_passing=entry["below"],
            average=entry["total"] / entry["graded"] if entry["graded"] else None,
            lowest=entry["lowest"] if entry["graded"] else None,
            highest=entry["highest"] if entry["graded"] else None,
        )
        for subject_id, entry in grouped.items()
    ]
    result.sort(
        key=lambda s: (
            -(s.percent_below_passing or 0.0),
            s.average if s.average is not None else 1e9,
            s.subject_name,
        )
    )
    return result


# --------------------------------------------------------------------------
# Learners at risk
# --------------------------------------------------------------------------
#
# The one part of this module that names individual people, which makes
# two things matter that did not before.
#
# **Nothing is recomputed.** `failed_subject_count` and
# `lowest_term_grade` are read straight out of `term_grade_summaries`,
# where `grading_service` wrote them against the policy in force at the
# time. Re-deriving "who is failing" from the raw grades and today's
# passing mark would quietly restate a finalized term under a threshold
# it was never graded on, which is exactly what rule 6 forbids.
#
# **A flag is not a verdict while a term is still being encoded.** A
# learner with two of nine subjects encoded and one of them below the
# line is genuinely failing that subject, and their term *average* means
# nothing yet. The two travel together on every row so the page can say
# which is which, rather than printing a provisional average as though
# the term were over.


@dataclass(frozen=True)
class AtRiskRow:
    """One learner in one term, flagged by the stored summary."""

    enrollment_id: uuid.UUID
    learner_name: str
    section_id: uuid.UUID
    section_name: str
    grade_level_id: uuid.UUID | None
    grade_level_name: str
    grade_level_order: int
    strand_id: uuid.UUID | None
    strand_name: str
    term_id: uuid.UUID
    term_name: str
    term_number: int
    term_average: float | None
    lowest_grade: float | None
    failed_subjects: int
    # Whether the term's record is complete (§22). False means the
    # average above is built from part of the subject list.
    complete: bool

    @property
    def provisional(self) -> bool:
        return not self.complete


@dataclass(frozen=True)
class AtRiskReport:
    passing_grade: float
    rows: tuple[AtRiskRow, ...]


def at_risk_learners(session, school_year_id, section_ids=None) -> AtRiskReport:
    """Learners whose stored term summary shows a failing subject or a
    failing term average.

    A learner appears **once per term they are flagged in**, so somebody
    struggling in all three terms is three rows. That is deliberate — the
    page is a work list, and a learner who recovered in Term 2 should not
    be presented as currently at risk. Use `at_risk_headline` for a
    people count; adding the rows up counts the same learner repeatedly.

    A NULL average or a NULL failed count never matches: SQL comparisons
    against NULL are not true, which is the behaviour rule 2 wants here —
    a learner nobody has graded is not a learner who is failing.
    """
    from app.grading_service import resolve_passing_grade

    passing_grade = float(resolve_passing_grade(session, school_year_id))

    summaries = (
        session.query(TermGradeSummary)
        .filter(
            TermGradeSummary.school_year_id == school_year_id,
            or_(
                TermGradeSummary.failed_subject_count > 0,
                and_(
                    TermGradeSummary.term_average.isnot(None),
                    TermGradeSummary.term_average < passing_grade,
                ),
            ),
        )
        .all()
    )
    if not summaries:
        return AtRiskReport(passing_grade=passing_grade, rows=())

    enrollment_query = session.query(Enrollment).filter(
        Enrollment.id.in_({s.enrollment_id for s in summaries}),
        Enrollment.enrollment_status.in_(ACTIVE_ENROLLMENT_STATUSES),
    )
    # Narrowed here rather than by dropping rows later: a scoped viewer
    # must not have another adviser's learners loaded at all, even to
    # discard them.
    if section_ids is not None:
        if not section_ids:
            return AtRiskReport(passing_grade=passing_grade, rows=())
        enrollment_query = enrollment_query.filter(Enrollment.section_id.in_(section_ids))
    enrollments = {e.id: e for e in enrollment_query.all()}
    if not enrollments:
        return AtRiskReport(passing_grade=passing_grade, rows=())

    learners = {
        learner.id: learner
        for learner in session.query(Learner)
        .filter(Learner.id.in_({e.learner_id for e in enrollments.values()}))
        .all()
    }
    sections = _sections_in_scope(session, school_year_id, section_ids)
    terms = {
        t.id: t for t in session.query(Term).filter_by(school_year_id=school_year_id).all()
    }
    grade_levels = {g.id: g for g in session.query(GradeLevel).all()}
    strands = {s.id: s for s in session.query(Strand).all()}

    rows = []
    for summary in summaries:
        enrollment = enrollments.get(summary.enrollment_id)
        if enrollment is None:
            continue
        learner = learners.get(enrollment.learner_id)
        section = sections.get(enrollment.section_id)
        term = terms.get(summary.term_id)
        if learner is None or section is None or term is None:
            continue
        grade_level = grade_levels.get(section.grade_level_id)
        strand = strands.get(section.strand_id)
        rows.append(
            AtRiskRow(
                enrollment_id=enrollment.id,
                learner_name=f"{learner.last_name}, {learner.first_name}",
                section_id=section.id,
                section_name=section.name,
                grade_level_id=section.grade_level_id,
                grade_level_name=grade_level.name if grade_level else "",
                grade_level_order=grade_level.display_order if grade_level else 0,
                strand_id=section.strand_id,
                strand_name=strand.name if strand else "",
                term_id=term.id,
                term_name=term.name,
                term_number=term.term_number,
                term_average=(
                    float(summary.term_average) if summary.term_average is not None else None
                ),
                lowest_grade=(
                    float(summary.lowest_term_grade)
                    if summary.lowest_term_grade is not None
                    else None
                ),
                failed_subjects=summary.failed_subject_count or 0,
                complete=summary.completion_status == CompletionStatus.COMPLETE,
            )
        )

    # Ranked by need, not roster order — deliberately **not** through
    # `app/roster_order.py`. That module puts males first because that is
    # how the DepEd forms and the teachers' workbook are laid out, and it
    # governs every roster in the app. This is not a roster: it is a work
    # list, and ordering it by anything but severity would bury the
    # learner most at risk somewhere in the middle.
    rows.sort(
        key=lambda r: (
            -r.failed_subjects,
            r.term_average if r.term_average is not None else 1e9,
            r.learner_name,
            r.term_number,
        )
    )
    return AtRiskReport(passing_grade=passing_grade, rows=tuple(rows))


# --------------------------------------------------------------------------
# Learners at risk in one teacher's own subjects
# --------------------------------------------------------------------------
#
# **This exists because `at_risk_learners` must never be shown to a
# subject teacher.** That function reads `term_grade_summaries`, whose
# `term_average` and `failed_subject_count` describe a learner across
# *every* subject — a teacher seeing it would learn how their learners
# are doing in colleagues' classes. So a subject teacher's at-risk list
# is built from the only grades that are theirs: `term_grades` on
# offerings they actively hold.
#
# It is a different question, not a narrower view of the same one. "Who
# is failing my subject" and "who is failing the term" have different
# answers, and only the first is a subject teacher's to ask.


@dataclass(frozen=True)
class SubjectRiskRow:
    """One learner below the passing mark in one of a teacher's classes."""

    enrollment_id: uuid.UUID
    learner_name: str
    section_id: uuid.UUID
    section_name: str
    offering_id: uuid.UUID
    subject_id: uuid.UUID
    subject_name: str
    subject_code: str
    term_id: uuid.UUID
    term_name: str
    term_number: int
    grade: float
    passing_grade: float
    # Whether this grade has been handed in yet — a failing DRAFT is
    # still the teacher's to change, a submitted one is a conversation.
    status: str

    @property
    def shortfall(self) -> float:
        return self.passing_grade - self.grade


@dataclass(frozen=True)
class SubjectRiskReport:
    passing_grade: float
    rows: tuple[SubjectRiskRow, ...]

    @property
    def learners(self) -> int:
        """Distinct people, not rows — a learner failing two of this
        teacher's subjects is two rows and one learner."""
        return len({row.enrollment_id for row in self.rows})


def subject_learners_at_risk(
    session, school_year_id, offering_ids
) -> SubjectRiskReport:
    """Learners below the passing mark in the given classes.

    `offering_ids` is required and empty means empty: this is only ever
    called with a subject teacher's own classes, and a teacher who holds
    none must see nothing rather than everything.

    **The threshold is resolved per offering.** `section_subject_offerings`
    may carry its own `grading_policy_version_id`, and `grading_service`
    honours it, so assuming one school-wide passing mark would quietly
    mis-flag a class graded on a different one. No offering uses that
    today; the column exists, so this reads it.
    """
    if not offering_ids:
        from app.grading_service import resolve_passing_grade as _fallback

        return SubjectRiskReport(
            passing_grade=float(_fallback(session, school_year_id)), rows=()
        )

    from app.grading_service import resolve_passing_grade
    from app.models.subjects import GradingPolicyVersion

    default_passing = float(resolve_passing_grade(session, school_year_id))

    offerings = (
        session.query(SectionSubjectOffering)
        .filter(
            SectionSubjectOffering.school_year_id == school_year_id,
            SectionSubjectOffering.id.in_(offering_ids),
        )
        .all()
    )
    if not offerings:
        return SubjectRiskReport(passing_grade=default_passing, rows=())

    # One query for every override in play, rather than a `session.get`
    # per offering.
    override_ids = {
        o.grading_policy_version_id
        for o in offerings
        if o.grading_policy_version_id is not None
    }
    overrides = {}
    if override_ids:
        overrides = {
            v.id: float(v.passing_grade)
            for v in session.query(GradingPolicyVersion)
            .filter(GradingPolicyVersion.id.in_(override_ids))
            .all()
        }
    passing_by_offering = {
        o.id: overrides.get(o.grading_policy_version_id, default_passing)
        for o in offerings
    }

    # Filtered in SQL to the highest threshold in play so only low grades
    # cross the wire, then each offering's own threshold applied exactly.
    ceiling = max(passing_by_offering.values())
    grades = (
        session.query(TermGrade)
        .join(Enrollment, TermGrade.enrollment_id == Enrollment.id)
        .filter(
            TermGrade.section_subject_offering_id.in_([o.id for o in offerings]),
            TermGrade.official_grade.isnot(None),
            TermGrade.official_grade < ceiling,
            Enrollment.enrollment_status.in_(ACTIVE_ENROLLMENT_STATUSES),
        )
        .all()
    )
    if not grades:
        return SubjectRiskReport(passing_grade=default_passing, rows=())

    offering_by_id = {o.id: o for o in offerings}
    enrollments = {
        e.id: e
        for e in session.query(Enrollment)
        .filter(Enrollment.id.in_({g.enrollment_id for g in grades}))
        .all()
    }
    learners = {
        learner.id: learner
        for learner in session.query(Learner)
        .filter(Learner.id.in_({e.learner_id for e in enrollments.values()}))
        .all()
    }
    sections = {
        s.id: s
        for s in session.query(Section)
        .filter(Section.id.in_({o.section_id for o in offerings}))
        .all()
    }
    subjects = {
        s.id: s
        for s in session.query(Subject)
        .filter(Subject.id.in_({o.subject_id for o in offerings}))
        .all()
    }
    terms = {
        t.id: t for t in session.query(Term).filter_by(school_year_id=school_year_id).all()
    }

    rows = []
    for grade in grades:
        offering = offering_by_id.get(grade.section_subject_offering_id)
        if offering is None:
            continue
        threshold = passing_by_offering[offering.id]
        value = float(grade.official_grade)
        if value >= threshold:
            continue  # below the ceiling but passing under its own rule
        enrollment = enrollments.get(grade.enrollment_id)
        learner = learners.get(enrollment.learner_id) if enrollment else None
        section = sections.get(offering.section_id)
        term = terms.get(offering.term_id)
        if learner is None or section is None or term is None:
            continue
        subject = subjects.get(offering.subject_id)
        rows.append(
            SubjectRiskRow(
                enrollment_id=enrollment.id,
                learner_name=f"{learner.last_name}, {learner.first_name}",
                section_id=section.id,
                section_name=section.name,
                offering_id=offering.id,
                subject_id=offering.subject_id,
                subject_name=subject.official_name if subject else "",
                subject_code=subject.code if subject else "",
                term_id=term.id,
                term_name=term.name,
                term_number=term.term_number,
                grade=value,
                passing_grade=threshold,
                status=grade.status.value if grade.status else "",
            )
        )

    # Furthest below the line first — the learner needing most attention,
    # not the alphabetically unlucky one.
    rows.sort(
        key=lambda r: (-r.shortfall, r.section_name, r.term_number, r.learner_name)
    )
    return SubjectRiskReport(passing_grade=default_passing, rows=tuple(rows))


# --------------------------------------------------------------------------
# Annual standing (§19-20)
# --------------------------------------------------------------------------
#
# The year-end counterpart of `at_risk_learners`, reading
# `annual_grade_summaries` instead of the term summaries. Three things
# make it more than the same query against a different table.
#
# **It never says "will not be promoted".** DO 017 explicitly leaves
# retention, promotion, graduation and honors to a forthcoming order
# (§25, §26), and the order also adds a rule the finalize guard does not
# yet implement — a learner taking more electives than the minimum must
# pass all of them. So this reports what the stored summary says and
# stops there. Naming a consequence would be inventing school policy.
#
# **The General Average is read, never recomputed.** It is unit-weighted
# under DO 017 and built from each subject's real term pattern (rule 4);
# a second implementation here would drift from the report card. The
# stored `averaging_method` and `total_units` come along so the number
# can be explained rather than merely displayed.
#
# **The failed-subject list obeys §16.** `subject_final_grades` carries a
# row for *every* subject including both Grade 11 language components,
# but the components' finals are not what counts — the combined learning
# area's is, once. Listing the raw failed rows would report a learner as
# failing Effective Communication and Mabisang Komunikasyon when the pair
# as one area passed, or miss a failed pair whose components each
# scraped through. `_failed_areas` applies the same substitution the
# General Average does.


@dataclass(frozen=True)
class AnnualRiskRow:
    """One learner's year, as the stored annual summary describes it."""

    enrollment_id: uuid.UUID
    learner_name: str
    section_id: uuid.UUID
    section_name: str
    grade_level_name: str
    strand_id: uuid.UUID | None
    strand_name: str
    general_average: float | None
    lowest_final_grade: float | None
    failed_subject_count: int
    complete: bool
    # How the average was reached, so a mis-set unit is visible rather
    # than just a slightly different plausible number.
    averaging_method: str
    total_units: float | None
    # Learning areas the learner failed, already collapsed per §16.
    failed_areas: tuple[str, ...]

    @property
    def provisional(self) -> bool:
        """An incomplete record's average is built from part of the
        subject list, so it will still move."""
        return not self.complete


@dataclass(frozen=True)
class AnnualSectionRow:
    section_id: uuid.UUID
    section_name: str
    grade_level_name: str
    learners: int
    complete: int
    flagged: int

    @property
    def incomplete(self) -> int:
        return self.learners - self.complete

    @property
    def complete_rate(self) -> float | None:
        if not self.learners:
            return None
        return 100.0 * self.complete / self.learners


@dataclass(frozen=True)
class AnnualRiskReport:
    passing_grade: float
    sections: tuple[AnnualSectionRow, ...]
    flagged: tuple[AnnualRiskRow, ...]

    @property
    def any_summaries(self) -> bool:
        return any(s.learners for s in self.sections)


def annual_risk(session, school_year_id, section_ids=None) -> AnnualRiskReport:
    """Learners whose stored **annual** summary shows a failing subject
    or a failing General Average.

    **Only failing learners are named.** An incomplete annual record is
    the other thing that blocks a year closing, but right now that is
    almost every learner, so it is reported per section as a count rather
    than as a list of hundreds. `complete_rate` is the finalize-readiness
    figure; the flagged list is the academic one.
    """
    from app.grading_service import resolve_passing_grade
    from app.models.grades import AnnualGradeSummary

    passing_grade = float(resolve_passing_grade(session, school_year_id))

    sections = _sections_in_scope(session, school_year_id, section_ids)
    if not sections:
        return AnnualRiskReport(passing_grade=passing_grade, sections=(), flagged=())

    enrollments = {
        e.id: e
        for e in session.query(Enrollment)
        .filter(
            Enrollment.school_year_id == school_year_id,
            Enrollment.section_id.in_(list(sections)),
            Enrollment.enrollment_status.in_(ACTIVE_ENROLLMENT_STATUSES),
        )
        .all()
    }
    if not enrollments:
        return AnnualRiskReport(passing_grade=passing_grade, sections=(), flagged=())

    summaries = {
        s.enrollment_id: s
        for s in session.query(AnnualGradeSummary)
        .filter(AnnualGradeSummary.enrollment_id.in_(list(enrollments)))
        .all()
    }

    flagged_ids = [
        enrollment_id
        for enrollment_id, summary in summaries.items()
        if (summary.failed_subject_count or 0) > 0
        or (
            summary.general_average is not None
            and float(summary.general_average) < passing_grade
        )
    ]

    learners = {
        learner.id: learner
        for learner in session.query(Learner)
        .filter(
            Learner.id.in_({enrollments[i].learner_id for i in flagged_ids})
        )
        .all()
    } if flagged_ids else {}
    grade_levels = {g.id: g for g in session.query(GradeLevel).all()}
    strands = {s.id: s for s in session.query(Strand).all()}
    failed_by_enrollment = _failed_areas(session, school_year_id, flagged_ids)

    rows = []
    for enrollment_id in flagged_ids:
        enrollment = enrollments[enrollment_id]
        summary = summaries[enrollment_id]
        section = sections.get(enrollment.section_id)
        learner = learners.get(enrollment.learner_id)
        if section is None or learner is None:
            continue
        grade_level = grade_levels.get(section.grade_level_id)
        strand = strands.get(section.strand_id)
        rows.append(
            AnnualRiskRow(
                enrollment_id=enrollment_id,
                learner_name=f"{learner.last_name}, {learner.first_name}",
                section_id=section.id,
                section_name=section.name,
                grade_level_name=grade_level.name if grade_level else "",
                strand_id=section.strand_id,
                strand_name=strand.name if strand else "",
                general_average=(
                    float(summary.general_average)
                    if summary.general_average is not None
                    else None
                ),
                lowest_final_grade=(
                    float(summary.lowest_final_grade)
                    if summary.lowest_final_grade is not None
                    else None
                ),
                failed_subject_count=summary.failed_subject_count or 0,
                complete=summary.completion_status == CompletionStatus.COMPLETE,
                averaging_method=(
                    summary.averaging_method.value if summary.averaging_method else ""
                ),
                total_units=(
                    float(summary.total_units)
                    if summary.total_units is not None
                    else None
                ),
                failed_areas=tuple(failed_by_enrollment.get(enrollment_id, ())),
            )
        )

    flagged_set = set(flagged_ids)
    by_section: dict = {}
    for enrollment_id, enrollment in enrollments.items():
        bucket = by_section.setdefault(
            enrollment.section_id, {"learners": 0, "complete": 0, "flagged": 0}
        )
        bucket["learners"] += 1
        summary = summaries.get(enrollment_id)
        if summary is not None and summary.completion_status == CompletionStatus.COMPLETE:
            bucket["complete"] += 1
        if enrollment_id in flagged_set:
            bucket["flagged"] += 1

    section_rows = []
    for section_id, bucket in by_section.items():
        section = sections[section_id]
        grade_level = grade_levels.get(section.grade_level_id)
        section_rows.append(
            AnnualSectionRow(
                section_id=section.id,
                section_name=section.name,
                grade_level_name=grade_level.name if grade_level else "",
                learners=bucket["learners"],
                complete=bucket["complete"],
                flagged=bucket["flagged"],
            )
        )

    rows.sort(
        key=lambda r: (
            -r.failed_subject_count,
            r.general_average if r.general_average is not None else 1e9,
            r.learner_name,
        )
    )
    section_rows.sort(key=lambda s: (-s.flagged, s.complete_rate or 0.0, s.section_name))
    return AnnualRiskReport(
        passing_grade=passing_grade,
        sections=tuple(section_rows),
        flagged=tuple(rows),
    )


def _failed_areas(session, school_year_id, enrollment_ids) -> dict:
    """`{enrollment_id: (learning area names failed,)}`, collapsed per §16.

    The Grade 11 language pair is one learning area for this purpose. Its
    two component subjects each carry a `subject_final_grades` row with
    its own remark, and neither is what counts — the
    `combined_learning_area_results` row is, once. So the components are
    dropped and the combined area substituted, exactly the way the
    General Average is built (rule 4). Getting this wrong reports a
    learner as failing two languages when the pair passed, or as failing
    none when the pair did not.
    """
    if not enrollment_ids:
        return {}

    from app.models.enums import SubjectRemark
    from app.models.grades import CombinedLearningAreaResult, SubjectFinalGrade
    from app.models.subjects import CombinedLearningArea, CombinedLearningAreaComponent

    component_subject_ids = {
        row[0]
        for row in session.query(CombinedLearningAreaComponent.subject_id).all()
    }
    finals = (
        session.query(SubjectFinalGrade)
        .filter(
            SubjectFinalGrade.enrollment_id.in_(enrollment_ids),
            SubjectFinalGrade.school_year_id == school_year_id,
            SubjectFinalGrade.remark == SubjectRemark.FAILED,
        )
        .all()
    )
    combined = (
        session.query(CombinedLearningAreaResult)
        .filter(
            CombinedLearningAreaResult.enrollment_id.in_(enrollment_ids),
            CombinedLearningAreaResult.school_year_id == school_year_id,
            CombinedLearningAreaResult.remark == SubjectRemark.FAILED,
        )
        .all()
    )

    subject_names = {}
    wanted = {f.subject_id for f in finals} - component_subject_ids
    if wanted:
        subject_names = {
            s.id: s.official_name
            for s in session.query(Subject).filter(Subject.id.in_(wanted)).all()
        }
    area_names = {}
    if combined:
        area_names = {
            a.id: a.name
            for a in session.query(CombinedLearningArea)
            .filter(
                CombinedLearningArea.id.in_({c.combined_learning_area_id for c in combined})
            )
            .all()
        }

    result: dict = {}
    for final in finals:
        if final.subject_id in component_subject_ids:
            continue
        name = subject_names.get(final.subject_id)
        if name:
            result.setdefault(final.enrollment_id, []).append(name)
    for row in combined:
        name = area_names.get(row.combined_learning_area_id)
        if name:
            result.setdefault(row.enrollment_id, []).append(name)
    return {key: tuple(sorted(names)) for key, names in result.items()}


# --------------------------------------------------------------------------
# Attendance risk (§31)
# --------------------------------------------------------------------------
#
# **The rule is not reimplemented here.** `app/attendance_engine.py`
# already owns §31 — what an eligible class day is, that LATE and CUTTING
# still count as present, that an unencoded day is neither present nor
# absent, and that a five-day absence run counts in *class* days so a
# weekend does not break it but a day the learner turned up does. A
# second version of any of that would drift from SF2 while looking
# right, so this module batches the I/O and calls `summarize_attendance`
# per learner, exactly as the Attendance page does.
#
# **Bounded to one month, deliberately.** The other metrics aggregate in
# SQL and return tens of rows; consecutive-run detection cannot, because
# it needs each learner's days in order. One month is ~20 class days ×
# the roster, which is a real amount of data — so the query selects three
# columns rather than ORM objects, and the function takes a month rather
# than a year.


@dataclass(frozen=True)
class AttendanceRiskRow:
    """One learner flagged by §31 in one month."""

    enrollment_id: uuid.UUID
    learner_name: str
    section_id: uuid.UUID
    section_name: str
    grade_level_name: str
    strand_id: uuid.UUID | None
    strand_name: str
    eligible_days: int
    days_present: int
    days_absent: int
    late_count: int
    cutting_count: int
    unencoded_days: int
    longest_run: int
    run_started: date | None
    run_ended: date | None

    @property
    def known_days(self) -> int:
        """Days somebody has actually marked. LATE and CUTTING are days
        present, so present + absent is the whole encoded set."""
        return self.days_present + self.days_absent

    @property
    def absence_rate(self) -> float | None:
        """Absences as a share of the days **anybody has marked**, and
        None when nobody has marked any.

        Deliberately not `absent / eligible`: on a month nobody has
        encoded, that reads 0%, which looks like perfect attendance
        rather than an empty sheet. Same rule as everywhere else here —
        absent data is not a zero value.
        """
        if not self.known_days:
            return None
        return 100.0 * self.days_absent / self.known_days


@dataclass(frozen=True)
class AttendanceSectionRow:
    """A section's month, in totals rather than in people.

    Exists so the page can report attendance for everyone without
    naming everyone: the flagged list stays short because §31 flags few
    learners, while this covers the whole roster.
    """

    section_id: uuid.UUID
    section_name: str
    grade_level_name: str
    strand_id: uuid.UUID | None
    strand_name: str
    learners: int
    eligible_days: int
    days_present: int
    days_absent: int
    unencoded_days: int
    flagged: int

    @property
    def known_days(self) -> int:
        return self.days_present + self.days_absent

    @property
    def absence_rate(self) -> float | None:
        """**Recomputed from the totals**, never the mean of the
        learners' own rates — the percentage trap that shipped twice
        here already, in SF2 and again in SF4.

        Denominated on days actually marked, not on eligible days, so an
        unencoded month reads as unknown rather than as 0% absence. Read
        it next to `encoded_rate`, which says how much of the month the
        figure is based on.
        """
        if not self.known_days:
            return None
        return 100.0 * self.days_absent / self.known_days

    @property
    def encoded_rate(self) -> float | None:
        if not self.eligible_days:
            return None
        return 100.0 * (self.eligible_days - self.unencoded_days) / self.eligible_days


@dataclass(frozen=True)
class AttendanceRiskReport:
    year: int
    month: int
    class_days: int
    sections: tuple[AttendanceSectionRow, ...]
    flagged: tuple[AttendanceRiskRow, ...]

    @property
    def any_records(self) -> bool:
        return any(s.days_present or s.days_absent for s in self.sections)


def attendance_risk(
    session, school_year_id, year: int, month: int, section_ids=None
) -> AttendanceRiskReport:
    """§31's five-consecutive-absence warning, plus each section's totals,
    for one month.

    Flags **only** what the spec defines as a warning. There is no
    absence-rate threshold here on purpose: §31 names the consecutive
    run and nothing else, and a percentage cutoff invented in an
    analytics page would read as school policy. The rate is reported
    alongside as context, never as the thing being judged.
    """
    from app.attendance_engine import (
        Movement,
        compute_active_window,
        summarize_attendance,
    )
    from app.models.attendance import AcademicCalendarDate, AttendanceRecord
    from app.models.learners import LearnerMovement
    from app.models.organization import SchoolYear

    sections = _sections_in_scope(session, school_year_id, section_ids)
    if not sections:
        return AttendanceRiskReport(year, month, 0, (), ())

    class_days = (
        session.query(AcademicCalendarDate)
        .filter(
            AcademicCalendarDate.school_year_id == school_year_id,
            AcademicCalendarDate.is_default_class_day.is_(True),
            func.extract("year", AcademicCalendarDate.calendar_date) == year,
            func.extract("month", AcademicCalendarDate.calendar_date) == month,
        )
        .order_by(AcademicCalendarDate.calendar_date)
        .all()
    )
    if not class_days:
        return AttendanceRiskReport(year, month, 0, (), ())
    day_by_id = {d.id: d.calendar_date for d in class_days}
    all_days = [d.calendar_date for d in class_days]

    enrollments = (
        session.query(Enrollment)
        .filter(
            Enrollment.school_year_id == school_year_id,
            Enrollment.section_id.in_(list(sections)),
        )
        .all()
    )
    if not enrollments:
        return AttendanceRiskReport(year, month, len(class_days), (), ())

    enrollment_ids = [e.id for e in enrollments]
    learners = {
        learner.id: learner
        for learner in session.query(Learner)
        .filter(Learner.id.in_({e.learner_id for e in enrollments}))
        .all()
    }
    grade_levels = {g.id: g for g in session.query(GradeLevel).all()}
    strands = {s.id: s for s in session.query(Strand).all()}
    school_year = session.get(SchoolYear, school_year_id)

    # Batched rather than `active_window_for` per learner, which costs two
    # round trips each. The window itself is still built by the engine's
    # own `compute_active_window`, so the movement rules stay in one place.
    movements: dict = {}
    for movement in (
        session.query(LearnerMovement)
        .filter(LearnerMovement.enrollment_id.in_(enrollment_ids))
        .all()
    ):
        movements.setdefault(movement.enrollment_id, []).append(movement)

    # Three columns, not ORM objects: a month across the whole school is
    # roughly 20 class days times the roster, and hydrating that many
    # instances is the expensive part.
    statuses: dict = {}
    for enrollment_id, calendar_date_id, status in (
        session.query(
            AttendanceRecord.enrollment_id,
            AttendanceRecord.calendar_date_id,
            AttendanceRecord.status,
        )
        .filter(
            AttendanceRecord.enrollment_id.in_(enrollment_ids),
            AttendanceRecord.calendar_date_id.in_(list(day_by_id)),
        )
        .all()
    ):
        day = day_by_id.get(calendar_date_id)
        if day is not None:
            statuses.setdefault(enrollment_id, {})[day] = status

    flagged: list[AttendanceRiskRow] = []
    totals: dict = {}
    for enrollment in enrollments:
        section = sections.get(enrollment.section_id)
        learner = learners.get(enrollment.learner_id)
        if section is None or learner is None:
            continue
        window = compute_active_window(
            [
                Movement(m.movement_type, m.effective_date)
                for m in movements.get(enrollment.id, [])
            ],
            default_start=school_year.start_date if school_year else None,
        )
        summary = summarize_attendance(
            all_days, window, statuses.get(enrollment.id, {})
        )
        if summary.eligible_days == 0:
            continue

        bucket = totals.setdefault(
            section.id,
            {"learners": 0, "eligible": 0, "present": 0, "absent": 0, "unencoded": 0, "flagged": 0},
        )
        bucket["learners"] += 1
        bucket["eligible"] += summary.eligible_days
        bucket["present"] += summary.days_present
        bucket["absent"] += summary.days_absent
        bucket["unencoded"] += summary.unencoded_days

        if not summary.has_consecutive_absence_warning:
            continue
        longest_start, longest_end, longest = _longest_run(summary, all_days)
        bucket["flagged"] += 1
        grade_level = grade_levels.get(section.grade_level_id)
        strand = strands.get(section.strand_id)
        flagged.append(
            AttendanceRiskRow(
                enrollment_id=enrollment.id,
                learner_name=f"{learner.last_name}, {learner.first_name}",
                section_id=section.id,
                section_name=section.name,
                grade_level_name=grade_level.name if grade_level else "",
                strand_id=section.strand_id,
                strand_name=strand.name if strand else "",
                eligible_days=summary.eligible_days,
                days_present=summary.days_present,
                days_absent=summary.days_absent,
                late_count=summary.late_count,
                cutting_count=summary.cutting_count,
                unencoded_days=summary.unencoded_days,
                longest_run=longest,
                run_started=longest_start,
                run_ended=longest_end,
            )
        )

    section_rows = []
    for section_id, bucket in totals.items():
        section = sections[section_id]
        grade_level = grade_levels.get(section.grade_level_id)
        strand = strands.get(section.strand_id)
        section_rows.append(
            AttendanceSectionRow(
                section_id=section.id,
                section_name=section.name,
                grade_level_name=grade_level.name if grade_level else "",
                strand_id=section.strand_id,
                strand_name=strand.name if strand else "",
                learners=bucket["learners"],
                eligible_days=bucket["eligible"],
                days_present=bucket["present"],
                days_absent=bucket["absent"],
                unencoded_days=bucket["unencoded"],
                flagged=bucket["flagged"],
            )
        )

    # Worst first on both lists: the longest absence run, then the most
    # days missed; and for sections, the highest absence rate.
    flagged.sort(key=lambda r: (-r.longest_run, -r.days_absent, r.learner_name))
    section_rows.sort(key=lambda s: (-(s.absence_rate or 0.0), s.section_name))
    return AttendanceRiskReport(
        year=year,
        month=month,
        class_days=len(class_days),
        sections=tuple(section_rows),
        flagged=tuple(flagged),
    )


def _longest_run(summary, all_days) -> tuple:
    """The longest §31 run on a summary, measured in eligible class days
    the same way the engine measures it."""
    best = (None, None, 0)
    for start, end in summary.consecutive_absence_runs:
        length = sum(1 for day in all_days if start <= day <= end)
        if length > best[2]:
            best = (start, end, length)
    return best


def attendance_headline(report: AttendanceRiskReport) -> tuple[int, int, float | None]:
    """`(learners flagged, sections affected, overall absence rate)`.

    The rate is recomputed from the school's totals, not averaged across
    sections — a 5-learner section and a 45-learner one do not weigh the
    same.
    """
    absent = sum(s.days_absent for s in report.sections)
    known = sum(s.known_days for s in report.sections)
    return (
        len(report.flagged),
        len({s.section_id for s in report.sections if s.flagged}),
        100.0 * absent / known if known else None,
    )


def at_risk_headline(rows) -> tuple[int, int]:
    """`(learners, flags)`.

    Two numbers because they are not the same one. A learner failing in
    all three terms contributes three rows; reporting that as "3 learners
    at risk" overstates the problem by exactly the amount the school
    would most want to get right.
    """
    return len({row.enrollment_id for row in rows}), len(rows)


# --------------------------------------------------------------------------
# Award eligibility (§24)
# --------------------------------------------------------------------------
#
# **Nothing here decides who wins an award.** `app/award_service.py` owns
# §24 — the complete-record and derogatory-record requirements, the
# minimum average, the tier ladder, and the reason string that has to
# accompany every "not eligible". This reads the `learner_awards` rows
# that function already wrote, and counts them. A second evaluator living
# in an analytics page would sooner or later name a learner the Awards
# page will not certify, which is rule 1 with a certificate attached.
#
# **"Not computed" is not "not eligible", and today it is almost
# everybody.** A `learner_awards` row exists only after someone has
# pressed *Compute eligibility for all* on the Awards page, for that
# section, for that policy version. Counting an unjudged learner as
# ineligible would report a school with no honours at all when the truth
# is that nobody has run it yet — rule 2 in a different costume. So the
# eligible share is denominated on the learners actually **computed**,
# never on the roster, and it is `None` until at least one is. It is
# displayed next to `computed_rate`, which says how much of the roster
# the figure rests on: "3 eligible out of 4 judged" and "3 out of 40" are
# very different claims. Same pairing, and the same reason, as the
# attendance rate sitting next to `encoded_rate`.
#
# **A stale award is worse than a missing one, because it looks
# answered.** `learner_awards.computed_at` records when the row was
# evaluated; the summary it was evaluated against carries its own
# `computed_at`. A grade encoded after the award was computed leaves a
# stored result describing a grade set that no longer exists. Those are
# counted and named, never quietly refreshed — recomputing would write,
# and this page writes nothing.
#
# **An override is counted apart and never folded in.** §40 and §67 make
# an override a human decision with an audited reason, and
# `compute_award_eligibility` deliberately leaves those rows alone.
# Adding them to the engine's eligible count would make an
# administrator's decision indistinguishable from the policy's, which is
# the one thing the audit trail exists to keep separate. An overridden
# row is also never stale — it is not waiting for a recompute.
#
# **One policy version at a time.** Academic Excellence is annual and
# judged on the General Average; the tiered Honors is per term and judged
# on the Term Average. A learner can hold both, so a combined "eligible"
# count across policies is a number that means nothing. The page picks a
# version the way the attendance section picks a month.


@dataclass(frozen=True)
class AwardPolicyOption:
    """One selectable award policy version, carrying the two things the
    page needs to lay itself out: whether it has a term dimension, and
    whether it is a tier ladder or a single flat award."""

    version_id: uuid.UUID
    policy_name: str
    version_number: int
    scope: str
    status: str
    tiered: bool
    requires_complete_record: bool

    @property
    def label(self) -> str:
        return f"{self.policy_name} (v{self.version_number})"

    @property
    def per_term(self) -> bool:
        return self.scope == "TERM"

    @property
    def average_label(self) -> str:
        """What the policy is judged on, named the way the report card
        names it — a TERM policy reads the Term Average (§17), an ANNUAL
        one the General Average (§19/§20)."""
        return "Term Average" if self.per_term else "General Average"


@dataclass(frozen=True)
class AwardSectionRow:
    section_id: uuid.UUID
    section_name: str
    grade_level_id: uuid.UUID | None
    grade_level_name: str
    strand_id: uuid.UUID | None
    strand_name: str
    term_id: uuid.UUID | None
    term_name: str
    term_number: int
    learners: int
    computed: int
    eligible: int
    not_eligible: int
    overridden: int
    stale: int
    # Records the policy's own completeness requirement would reject.
    # Read straight off the summary's `completion_status`, never derived
    # from grades — and it is context, not the award's reason. The reason
    # §24 requires is per learner and lives on the Awards page.
    incomplete_records: int

    @property
    def not_computed(self) -> int:
        return max(self.learners - self.computed, 0)

    @property
    def computed_rate(self) -> float | None:
        """`None`, not 0%, for a section with nobody on the roll — an
        empty section has not been judged badly, it has nothing to
        judge."""
        if not self.learners:
            return None
        return 100.0 * self.computed / self.learners

    @property
    def eligible_rate(self) -> float | None:
        """A share of the learners **judged**, not of the roster, and
        `None` until somebody has been judged. See the note above this
        dataclass — it is the number the whole metric turns on."""
        if not self.computed:
            return None
        return 100.0 * self.eligible / self.computed


@dataclass(frozen=True)
class AwardLearnerRow:
    """One learner the stored award row says is eligible."""

    enrollment_id: uuid.UUID
    learner_name: str
    section_id: uuid.UUID
    section_name: str
    grade_level_name: str
    term_id: uuid.UUID | None
    term_name: str
    award_name: str
    average: float | None
    is_override: bool
    stale: bool


@dataclass(frozen=True)
class AwardEligibilityReport:
    policy: AwardPolicyOption
    sections: tuple[AwardSectionRow, ...]
    eligible: tuple[AwardLearnerRow, ...]

    @property
    def any_computed(self) -> bool:
        return any(s.computed for s in self.sections)


def award_policy_options(session, school_year_id) -> tuple[AwardPolicyOption, ...]:
    """Every award policy version effective for the year.

    Two queries, and no scoping argument: a policy is school-wide
    configuration, not learner data — an adviser choosing between
    "Academic Excellence" and "Honors" reads the same list the registrar
    reads. What either of them then sees *through* it is scoped by
    `award_eligibility`.
    """
    from app.models.awards import AwardPolicy, AwardPolicyVersion

    versions = (
        session.query(AwardPolicyVersion)
        .filter_by(effective_school_year_id=school_year_id)
        .all()
    )
    if not versions:
        return ()
    policies = {
        p.id: p
        for p in session.query(AwardPolicy)
        .filter(AwardPolicy.id.in_({v.award_policy_id for v in versions}))
        .all()
    }
    options = [_policy_option(version, policies.get(version.award_policy_id)) for version in versions]
    options.sort(key=lambda o: (o.policy_name, -o.version_number))
    return tuple(options)


def _policy_option(version, policy) -> AwardPolicyOption:
    return AwardPolicyOption(
        version_id=version.id,
        policy_name=policy.name if policy else "Award",
        version_number=version.version_number,
        scope=version.scope.value if version.scope else "ANNUAL",
        status=version.status.value if version.status else "",
        tiered=bool(version.tier_thresholds),
        requires_complete_record=bool(version.require_complete_record),
    )


def award_eligibility(
    session, school_year_id, award_policy_version_id, section_ids=None
) -> AwardEligibilityReport | None:
    """What the stored `learner_awards` rows say, for one policy version.

    Returns `None` when the version does not exist — a policy deleted
    between the page drawing its selector and this running is a missing
    question, not an answer of zero.

    A TERM-scoped policy produces one row per section **per term**, since
    it is judged three times a year; an ANNUAL one produces one row per
    section with `term_id` left `None`. The page filters those rows in
    Python, so changing term costs no round trip.
    """
    from app.models.awards import AwardPolicy, AwardPolicyVersion, LearnerAward
    from app.models.enums import AwardResult, AwardScope
    from app.models.grades import AnnualGradeSummary

    version = session.get(AwardPolicyVersion, award_policy_version_id)
    if version is None:
        return None
    policy = session.get(AwardPolicy, version.award_policy_id)
    option = _policy_option(version, policy)
    per_term = version.scope == AwardScope.TERM

    sections = _sections_in_scope(session, school_year_id, section_ids)
    if not sections:
        return AwardEligibilityReport(policy=option, sections=(), eligible=())

    grade_levels = {g.id: g for g in session.query(GradeLevel).all()}
    strands = {s.id: s for s in session.query(Strand).all()}
    terms = (
        session.query(Term)
        .filter_by(school_year_id=school_year_id)
        .order_by(Term.term_number)
        .all()
    )

    enrollments = {
        e.id: e
        for e in session.query(Enrollment)
        .filter(
            Enrollment.school_year_id == school_year_id,
            Enrollment.section_id.in_(list(sections)),
            Enrollment.enrollment_status.in_(ACTIVE_ENROLLMENT_STATUSES),
        )
        .all()
    }
    if not enrollments:
        return AwardEligibilityReport(policy=option, sections=(), eligible=())

    awards = (
        session.query(LearnerAward)
        .filter(
            LearnerAward.enrollment_id.in_(list(enrollments)),
            LearnerAward.award_policy_version_id == version.id,
        )
        .all()
    )

    # Keyed `(enrollment_id, term_id)` for a TERM policy and
    # `(enrollment_id, None)` for an ANNUAL one — the same key the awards
    # are bucketed under below, so a term-scoped award is only ever
    # compared against its own term's summary and never against the
    # year's. Columns, not ORM instances: a roster's worth of summaries is
    # the one thing on this page that scales with learners.
    if per_term:
        summaries = {
            (row[0], row[1]): (row[2], row[3], row[4])
            for row in session.query(
                TermGradeSummary.enrollment_id,
                TermGradeSummary.term_id,
                TermGradeSummary.term_average,
                TermGradeSummary.computed_at,
                TermGradeSummary.completion_status,
            )
            .filter(TermGradeSummary.enrollment_id.in_(list(enrollments)))
            .all()
        }
    else:
        summaries = {
            (row[0], None): (row[1], row[2], row[3])
            for row in session.query(
                AnnualGradeSummary.enrollment_id,
                AnnualGradeSummary.general_average,
                AnnualGradeSummary.computed_at,
                AnnualGradeSummary.completion_status,
            )
            .filter(AnnualGradeSummary.enrollment_id.in_(list(enrollments)))
            .all()
        }

    buckets: dict = {}

    def bucket_for(section_id, term_id):
        return buckets.setdefault(
            (section_id, term_id),
            {
                "learners": 0,
                "computed": 0,
                "eligible": 0,
                "not_eligible": 0,
                "overridden": 0,
                "stale": 0,
                "incomplete": 0,
            },
        )

    # The denominator first, so a section nobody has run the check on
    # still appears with its roster rather than dropping out of the table
    # — which is the state this metric mostly has to report.
    slots = [t.id for t in terms] if per_term else [None]
    for enrollment in enrollments.values():
        for term_id in slots:
            entry = bucket_for(enrollment.section_id, term_id)
            entry["learners"] += 1
            summary = summaries.get((enrollment.id, term_id))
            if summary is not None and summary[2] != CompletionStatus.COMPLETE:
                entry["incomplete"] += 1

    wanted_learner_ids = set()
    eligible_rows = []
    for award in awards:
        enrollment = enrollments.get(award.enrollment_id)
        if enrollment is None:
            continue
        # A TERM policy's rows carry a term; an ANNUAL policy's do not. A
        # row of the wrong shape answers a different question and is left
        # out rather than counted into this one.
        if per_term and award.term_id is None:
            continue
        if not per_term and award.term_id is not None:
            continue
        term_id = award.term_id if per_term else None
        if (enrollment.section_id, term_id) not in buckets:
            continue
        entry = buckets[(enrollment.section_id, term_id)]
        entry["computed"] += 1

        eligible = award.award_result == AwardResult.ELIGIBLE_AWARDED
        if eligible:
            entry["eligible"] += 1
        else:
            entry["not_eligible"] += 1
        if award.is_override:
            entry["overridden"] += 1

        summary = summaries.get((enrollment.id, term_id))
        stale = (
            False
            if award.is_override
            else _is_stale(award.computed_at, summary[1] if summary else None)
        )
        if stale:
            entry["stale"] += 1

        if eligible:
            wanted_learner_ids.add(enrollment.learner_id)
            eligible_rows.append((enrollment, award, term_id, summary, stale))

    learners = (
        {
            learner.id: learner
            for learner in session.query(Learner)
            .filter(Learner.id.in_(wanted_learner_ids))
            .all()
        }
        if wanted_learner_ids
        else {}
    )
    term_names = {t.id: (t.name, t.term_number) for t in terms}

    named = []
    for enrollment, award, term_id, summary, stale in eligible_rows:
        learner = learners.get(enrollment.learner_id)
        section = sections.get(enrollment.section_id)
        if learner is None or section is None:
            continue
        grade_level = grade_levels.get(section.grade_level_id)
        named.append(
            AwardLearnerRow(
                enrollment_id=enrollment.id,
                learner_name=f"{learner.last_name}, {learner.first_name}",
                section_id=section.id,
                section_name=section.name,
                grade_level_name=grade_level.name if grade_level else "",
                term_id=term_id,
                term_name=term_names.get(term_id, ("", 0))[0] if term_id else "",
                # The tier as it was awarded, read from the row — never
                # re-derived from the average against `tier_thresholds`.
                # The ladder was applied once, at compute time, against
                # the version in force then.
                award_name=award.award_name or option.policy_name,
                average=(
                    float(summary[0]) if summary and summary[0] is not None else None
                ),
                is_override=bool(award.is_override),
                stale=stale,
            )
        )

    section_rows = []
    for (section_id, term_id), entry in buckets.items():
        section = sections[section_id]
        grade_level = grade_levels.get(section.grade_level_id)
        strand = strands.get(section.strand_id)
        name, number = term_names.get(term_id, ("", 0)) if term_id else ("", 0)
        section_rows.append(
            AwardSectionRow(
                section_id=section.id,
                section_name=section.name,
                grade_level_id=section.grade_level_id,
                grade_level_name=grade_level.name if grade_level else "",
                strand_id=section.strand_id,
                strand_name=strand.name if strand else "",
                term_id=term_id,
                term_name=name,
                term_number=number,
                learners=entry["learners"],
                computed=entry["computed"],
                eligible=entry["eligible"],
                not_eligible=entry["not_eligible"],
                overridden=entry["overridden"],
                stale=entry["stale"],
                incomplete_records=entry["incomplete"],
            )
        )

    # Highest first — it is an honour roll, and the learner at the top is
    # what it is for. A missing average sorts last rather than as a zero
    # (rule 2), which would otherwise park an eligible learner whose
    # average has not been stored at the bottom, reading as the weakest.
    named.sort(
        key=lambda r: (
            r.average is None,
            -(r.average or 0.0),
            r.term_name,
            r.learner_name,
        )
    )
    section_rows.sort(key=lambda s: (s.term_number, -s.eligible, s.section_name))
    return AwardEligibilityReport(
        policy=option, sections=tuple(section_rows), eligible=tuple(named)
    )


def _is_stale(award_computed_at, summary_computed_at) -> bool:
    """Was the award judged before the average it was judged on last
    moved?

    Both timestamps are written tz-aware into `TIMESTAMP WITHOUT TIME
    ZONE` columns, so a value read back from Postgres is naive while one
    still sitting in the session from an uncommitted write is not.
    Comparing the two raises rather than answering wrongly, which is the
    right failure — but only if it never reaches a user, so both sides
    are normalised here.
    """
    if award_computed_at is None or summary_computed_at is None:
        return False
    return _naive_utc(summary_computed_at) > _naive_utc(award_computed_at)


def _naive_utc(value):
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def award_headline(rows) -> tuple[int, int, int, float | None]:
    """`(learners, computed, eligible, eligible share of computed)`.

    Four numbers because three of them are needed to read the fourth
    honestly. The share is recomputed from the totals — never averaged
    across sections, which would let a five-learner section weigh as much
    as a forty-five-learner one — and it is `None` while nothing has been
    computed, because a school that has not run the eligibility check has
    not produced zero award winners.
    """
    learners = sum(row.learners for row in rows)
    computed = sum(row.computed for row in rows)
    eligible = sum(row.eligible for row in rows)
    return (
        learners,
        computed,
        eligible,
        100.0 * eligible / computed if computed else None,
    )


def award_tiers(rows) -> list[tuple[str, int]]:
    """`[(award name, learners), ...]`, commonest first.

    Recomputed from whatever rows are in view rather than stored, for the
    same reason `roll_up` recomputes a percentage: the page filters in
    Python, and a count carried through a filter is a count of something
    else.
    """
    counts: dict = {}
    for row in rows:
        counts[row.award_name] = counts.get(row.award_name, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
