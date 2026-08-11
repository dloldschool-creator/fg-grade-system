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


def _term_grades_by_subject(session: Session, enrollment: Enrollment) -> dict:
    """subject_id -> {term_number: official_grade | None}, read straight
    from `term_grades` rather than from `subject_final_grades` — a subject
    can have some terms encoded and others not, which is exactly what
    makes it Incomplete."""
    offerings = (
        session.query(SectionSubjectOffering)
        .filter_by(section_id=enrollment.section_id, school_year_id=enrollment.school_year_id)
        .all()
    )
    terms = {
        t.id: t for t in session.query(Term).filter_by(school_year_id=enrollment.school_year_id).all()
    }
    term_grades = {
        tg.section_subject_offering_id: tg
        for tg in session.query(TermGrade).filter_by(enrollment_id=enrollment.id).all()
    }
    result: dict = {}
    for offering in offerings:
        term = terms.get(offering.term_id)
        if term is None:
            continue
        grade = term_grades.get(offering.id)
        result.setdefault(offering.subject_id, {})[term.term_number] = (
            grade.official_grade if grade else None
        )
    return result


def build_learning_area_rows(session: Session, enrollment: Enrollment) -> list[LearningAreaRow]:
    """The rows for one learner, in print order: each combined learning
    area followed by its indented components, then every other subject."""
    finals = {
        f.subject_id: f
        for f in session.query(SubjectFinalGrade).filter_by(enrollment_id=enrollment.id).all()
    }
    combined_results = (
        session.query(CombinedLearningAreaResult).filter_by(enrollment_id=enrollment.id).all()
    )
    by_subject = _term_grades_by_subject(session, enrollment)

    rows: list[LearningAreaRow] = []
    handled: set = set()

    for area_result in combined_results:
        area = session.get(CombinedLearningArea, area_result.combined_learning_area_id)
        components = (
            session.query(CombinedLearningAreaComponent)
            .filter_by(combined_learning_area_id=area_result.combined_learning_area_id)
            .order_by(CombinedLearningAreaComponent.display_order)
            .all()
        )
        component_terms: set[int] = set()
        for component in components:
            component_terms |= set(by_subject.get(component.subject_id, {}))
        rows.append(
            LearningAreaRow(
                name=area.name,
                term_grades={
                    1: area_result.term1_combined,
                    2: area_result.term2_combined,
                    3: area_result.term3_combined,
                },
                final_grade=area_result.final_grade,
                remark=area_result.remark.value if area_result.remark else None,
                # The pair runs whenever either component does.
                offered_terms=component_terms or {1, 2, 3},
            )
        )
        for component in components:
            subject = session.get(Subject, component.subject_id)
            rows.append(
                LearningAreaRow(
                    name=subject.official_name,
                    term_grades=by_subject.get(component.subject_id, {}),
                    # Blank on purpose — §16. The component's own final
                    # grade exists in the database; the form just doesn't
                    # show it, because the parent row carries it.
                    final_grade=None,
                    remark=None,
                    offered_terms=set(by_subject.get(component.subject_id, {})),
                    is_component=True,
                )
            )
            handled.add(component.subject_id)

    for subject_id, final in finals.items():
        if subject_id in handled:
            continue
        subject = session.get(Subject, subject_id)
        rows.append(
            LearningAreaRow(
                name=subject.official_name,
                term_grades=by_subject.get(subject_id, {}),
                final_grade=final.final_grade,
                remark=final.remark.value if final.remark else None,
                offered_terms=set(by_subject.get(subject_id, {})),
            )
        )
    return rows
