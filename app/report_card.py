"""The learner's Learning Progress and Achievement rows, as they appear
on a report card (§16, §35).

**One implementation, used by both the Grade Summary screen and the
generated SF9.** The combined-language display rule is the single most
bug-prone thing in this system (CLAUDE.md rule 3), so having the screen
and the printed form derive their rows separately would be an open
invitation for the two to disagree — and the printed one is the one that
goes home to a parent.

The rule (§16), for Grade 11's Effective Communication / Mabisang
Komunikasyon pair:

  - the **parent** row shows the combined term grades AND a Final Grade;
  - each **component** row shows its own term grades but its Final Grade
    cell stays **blank**.

Every other subject is an ordinary row with its own term grades and its
own Final Grade.

**On query cost.** The database is ~85ms away, so a query issued per
learner is the difference between a page that renders instantly and one
that takes a minute. `ReportCardContext` holds everything shared by a
section — offerings, terms, subjects, combined areas — plus the
per-enrollment grade rows for a whole roster, all fetched in a fixed
handful of queries. Building rows for forty learners costs the same
number of round trips as building them for one.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.grades import (
    CombinedLearningAreaResult,
    SubjectFinalGrade,
    TermGrade,
)
from app.models.learners import Enrollment
from app.models.organization import Term
from app.models.subjects import (
    CombinedLearningArea,
    CombinedLearningAreaComponent,
    SectionSubjectOffering,
    Subject,
)

# How far component rows are indented under their parent on the form.
COMPONENT_INDENT = " " * 10


@dataclass
class LearningAreaRow:
    """One printed line. `is_component` drives the indent, and
    `final_grade`/`remark` are None on a component precisely because §16
    says those cells stay blank — not because the value is unknown.

    `offered_terms` is what distinguishes "this subject doesn't run that
    term" from "it runs but nobody has encoded a grade yet". Both look
    like an empty cell in `term_grades`, but they mean opposite things:
    the first is shaded on the report card to show no grade is expected,
    the second is a genuine gap that still needs filling.
    """

    name: str
    term_grades: dict[int, Decimal | None]
    final_grade: Decimal | None
    remark: str | None
    offered_terms: set[int] = field(default_factory=lambda: {1, 2, 3})
    is_component: bool = False

    @property
    def display_name(self) -> str:
        return f"{COMPONENT_INDENT}{self.name}" if self.is_component else self.name

    def is_offered(self, term_number: int) -> bool:
        return term_number in self.offered_terms


@dataclass
class ReportCardContext:
    """Everything needed to build rows for one section's learners, loaded
    up front so nothing has to be fetched inside a per-learner loop."""

    # subject_id -> {term_number: offering_id}
    offerings_by_subject: dict
    # subject_id -> display order taken from section_subject_offerings
    subject_order: dict
    subjects: dict          # subject_id -> Subject
    areas: list             # [(CombinedLearningArea, [component subject_id, ...])]
    term_grades: dict       # (enrollment_id, offering_id) -> Decimal | None
    finals: dict            # (enrollment_id, subject_id) -> SubjectFinalGrade
    combined: dict          # (enrollment_id, area_id) -> CombinedLearningAreaResult


def load_report_context(session: Session, enrollments: list[Enrollment]) -> ReportCardContext:
    """Loads a whole roster in a fixed number of queries.

    Every enrollment passed in must belong to the same section and school
    year — the offerings and combined areas are shared, which is what
    makes the batching sound.
    """
    if not enrollments:
        return ReportCardContext({}, {}, {}, [], {}, {}, {})

    first = enrollments[0]
    enrollment_ids = [e.id for e in enrollments]

    terms = {
        t.id: t.term_number
        for t in session.query(Term).filter_by(school_year_id=first.school_year_id).all()
    }
    offerings = (
        session.query(SectionSubjectOffering)
        .filter_by(section_id=first.section_id, school_year_id=first.school_year_id)
        .all()
    )
    offerings_by_subject: dict = {}
    subject_order: dict = {}
    offering_ids = []
    for offering in offerings:
        term_number = terms.get(offering.term_id)
        if term_number is None:
            continue
        offerings_by_subject.setdefault(offering.subject_id, {})[term_number] = offering.id
        offering_ids.append(offering.id)
        # Lowest display_order across the subject's offerings decides where
        # it prints, so a subject keeps one position regardless of how many
        # terms it runs in.
        existing = subject_order.get(offering.subject_id)
        candidate = offering.display_order if offering.display_order is not None else 9999
        if existing is None or candidate < existing:
            subject_order[offering.subject_id] = candidate

    subject_ids = set(offerings_by_subject)
    areas: list = []
    for area in (
        session.query(CombinedLearningArea).filter_by(grade_level_id=first.grade_level_id).all()
    ):
        components = (
            session.query(CombinedLearningAreaComponent)
            .filter_by(combined_learning_area_id=area.id)
            .order_by(CombinedLearningAreaComponent.display_order)
            .all()
        )
        component_ids = [c.subject_id for c in components]
        areas.append((area, component_ids))
        subject_ids.update(component_ids)

    subjects = {}
    if subject_ids:
        subjects = {
            s.id: s for s in session.query(Subject).filter(Subject.id.in_(subject_ids)).all()
        }

    term_grades: dict = {}
    if offering_ids:
        for grade in (
            session.query(TermGrade)
            .filter(
                TermGrade.enrollment_id.in_(enrollment_ids),
                TermGrade.section_subject_offering_id.in_(offering_ids),
            )
            .all()
        ):
            term_grades[(grade.enrollment_id, grade.section_subject_offering_id)] = (
                grade.official_grade
            )

    finals = {
        (f.enrollment_id, f.subject_id): f
        for f in session.query(SubjectFinalGrade)
        .filter(SubjectFinalGrade.enrollment_id.in_(enrollment_ids))
        .all()
    }
    combined = {
        (r.enrollment_id, r.combined_learning_area_id): r
        for r in session.query(CombinedLearningAreaResult)
        .filter(CombinedLearningAreaResult.enrollment_id.in_(enrollment_ids))
        .all()
    }
    return ReportCardContext(
        offerings_by_subject, subject_order, subjects, areas, term_grades, finals, combined
    )


def _term_grades_for(context: ReportCardContext, enrollment_id, subject_id) -> dict:
    """{term_number: grade | None} for one subject. A term is present as a
    key when the subject is *offered* then, whether or not a grade has
    been encoded — which is what tells "doesn't run" apart from "not yet
    graded"."""
    return {
        term_number: context.term_grades.get((enrollment_id, offering_id))
        for term_number, offering_id in context.offerings_by_subject.get(subject_id, {}).items()
    }


def build_learning_area_rows(
    session: Session, enrollment: Enrollment, context: ReportCardContext | None = None
) -> list[LearningAreaRow]:
    """The rows for one learner, in print order: each combined learning
    area followed by its indented components, then every other subject.

    Pass a `context` when rendering a whole section — otherwise one is
    loaded for this learner alone, which is correct but costs a fresh
    round of queries per learner.
    """
    if context is None:
        context = load_report_context(session, [enrollment])

    rows: list[LearningAreaRow] = []
    handled: set = set()

    for area, component_ids in context.areas:
        result = context.combined.get((enrollment.id, area.id))
        if result is None:
            continue
        component_terms: set[int] = set()
        for subject_id in component_ids:
            component_terms |= set(context.offerings_by_subject.get(subject_id, {}))
        rows.append(
            LearningAreaRow(
                name=area.name,
                term_grades={
                    1: result.term1_combined,
                    2: result.term2_combined,
                    3: result.term3_combined,
                },
                final_grade=result.final_grade,
                remark=result.remark.value if result.remark else None,
                # The pair runs whenever either component does.
                offered_terms=component_terms or {1, 2, 3},
            )
        )
        for subject_id in component_ids:
            subject = context.subjects.get(subject_id)
            if subject is None:
                continue
            rows.append(
                LearningAreaRow(
                    name=subject.official_name,
                    term_grades=_term_grades_for(context, enrollment.id, subject_id),
                    # Blank on purpose — §16. The component's own final
                    # grade exists in the database; the form just doesn't
                    # show it, because the parent row carries it.
                    final_grade=None,
                    remark=None,
                    offered_terms=set(context.offerings_by_subject.get(subject_id, {})),
                    is_component=True,
                )
            )
            handled.add(subject_id)

    # Remaining subjects in the order the section's offerings set, which
    # keeps a learner's card printing the same way every time (the old
    # per-learner query had no ORDER BY, so its order was whatever
    # Postgres happened to return).
    remaining = [
        (enrollment_id, subject_id)
        for (enrollment_id, subject_id) in context.finals
        if enrollment_id == enrollment.id and subject_id not in handled
    ]
    remaining.sort(
        key=lambda key: (
            context.subject_order.get(key[1], 9999),
            getattr(context.subjects.get(key[1]), "official_name", ""),
        )
    )
    for key in remaining:
        subject_id = key[1]
        subject = context.subjects.get(subject_id)
        if subject is None:
            continue
        final = context.finals[key]
        rows.append(
            LearningAreaRow(
                name=subject.official_name,
                term_grades=_term_grades_for(context, enrollment.id, subject_id),
                final_grade=final.final_grade,
                remark=final.remark.value if final.remark else None,
                offered_terms=set(context.offerings_by_subject.get(subject_id, {})),
            )
        )
    return rows


def build_term_subject_rows(
    session: Session,
    enrollment: Enrollment,
    term_number: int,
    context: ReportCardContext | None = None,
) -> list[tuple[str, Decimal | None]]:
    """(subject name, grade) for the subjects actually active in one term,
    in the section's print order — what a temporary term card lists (§39:
    "show only subjects active during the selected term").

    Note this deliberately does **not** use the combined-language parent
    row. §17 keeps Effective Communication and Mabisang Komunikasyon as
    two separate subjects when computing the Term Average, so a card that
    prints the Term Average has to itemise the same two subjects that
    average is made of. Collapsing them into the parent here would show a
    subject list that doesn't add up to the figure beneath it.

    That's the opposite of the annual report card, where §16 does collapse
    the pair — see `build_learning_area_rows`.
    """
    if context is None:
        context = load_report_context(session, [enrollment])

    ordered: list[tuple[int, str, Decimal | None]] = []
    for subject_id, by_term in context.offerings_by_subject.items():
        if term_number not in by_term:
            continue  # subject doesn't run this term
        subject = context.subjects.get(subject_id)
        if subject is None:
            continue
        ordered.append(
            (
                context.subject_order.get(subject_id, 9999),
                subject.official_name,
                context.term_grades.get((enrollment.id, by_term[term_number])),
            )
        )
    # The section's own print order (the Order column on Section Subject
    # Offerings, seeded from the subject profile) — **not** alphabetical.
    # This sorted on the name until 2026-08-17, which put Events
    # Management above General Mathematics on a printed card while the
    # annual report card, the gradebook and the teachers' own workbook all
    # listed the languages first. `build_learning_area_rows` already used
    # `subject_order`; the two now agree, so a subject sits in the same
    # place on both cards. The name only breaks a tie between two subjects
    # sharing an order.
    ordered.sort(key=lambda row: (row[0], row[1]))
    return [(name, grade) for _, name, grade in ordered]
