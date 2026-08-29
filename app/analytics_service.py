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
from datetime import date

from sqlalchemy import and_, case, func, or_

from app.models.academic_structure import GradeLevel, Section, Strand, Track
from app.models.enums import CompletionStatus, EnrollmentStatus, OfferingStatus
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


def encoding_progress(session, school_year_id) -> list[EncodingRow]:
    """How far grade encoding has got, per section per term.

    Returns **every** section × term in the school year, unfiltered. The
    caller filters the returned rows in Python, and that is on purpose:
    the result is one row per section per term — 30 × 3 today — so the
    whole year's progress is small enough to cache once and slice for
    free. Pushing the grade-level/strand/section filters into SQL would
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
    sections = session.query(Section).filter_by(school_year_id=school_year_id).all()
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


def subject_grade_stats(session, school_year_id) -> GradeStats:
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

    sections = {
        s.id: s
        for s in session.query(Section).filter_by(school_year_id=school_year_id).all()
    }
    terms = {
        t.id: t for t in session.query(Term).filter_by(school_year_id=school_year_id).all()
    }
    if not sections or not terms:
        return GradeStats(passing_grade=passing_grade, bands=bands, rows=())

    grade_levels = {g.id: g for g in session.query(GradeLevel).all()}
    tracks = {t.id: t for t in session.query(Track).all()}
    strands = {s.id: s for s in session.query(Strand).all()}
    subjects = {s.id: s for s in session.query(Subject).all()}

    aggregated = _grades_by_section_term_subject(session, school_year_id, bands)

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


def _grades_by_section_term_subject(session, school_year_id, bands) -> dict:
    """One query. `{(section, term, subject): counts and totals}`.

    Filtered to the same four enrollment statuses as everything else on
    this page, so a section's distribution describes the same class its
    encoding percentage described.
    """
    band_expr = _band_expression(bands)
    results = (
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
        .all()
    )

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


def at_risk_learners(session, school_year_id) -> AtRiskReport:
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

    enrollments = {
        e.id: e
        for e in session.query(Enrollment)
        .filter(
            Enrollment.id.in_({s.enrollment_id for s in summaries}),
            Enrollment.enrollment_status.in_(ACTIVE_ENROLLMENT_STATUSES),
        )
        .all()
    }
    if not enrollments:
        return AtRiskReport(passing_grade=passing_grade, rows=())

    learners = {
        learner.id: learner
        for learner in session.query(Learner)
        .filter(Learner.id.in_({e.learner_id for e in enrollments.values()}))
        .all()
    }
    sections = {
        s.id: s
        for s in session.query(Section).filter_by(school_year_id=school_year_id).all()
    }
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


def at_risk_headline(rows) -> tuple[int, int]:
    """`(learners, flags)`.

    Two numbers because they are not the same one. A learner failing in
    all three terms contributes three rows; reporting that as "3 learners
    at risk" overstates the problem by exactly the amount the school
    would most want to get right.
    """
    return len({row.enrollment_id for row in rows}), len(rows)
