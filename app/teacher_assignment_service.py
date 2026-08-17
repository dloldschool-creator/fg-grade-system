"""Assigning a teacher to a subject, for one section (§47).

**A teaching assignment is an access grant.** The Gradebook decides what
a teacher may encode purely from `teacher_assignments.teacher_user_id`
(see `app/admin_pages/gradebook.py`) — there is no second check. So the
question "who may write these rows" is the same question as "who may
read and grade this roster", and the answer is deliberately *not* the
teacher themselves: a Super Admin may assign anywhere, an adviser only
within a section they advise. The person granting access is never the
person receiving it.

**Assignment is per subject here, per offering in the database.** A
teacher is named for a subject, not for a term, but an assignment row
hangs off a `section_subject_offering`, which is one per term. So one
call writes as many rows as that subject has terms — three for a core
subject, one for a single-term elective. Doing it per term instead was
21 decisions per section where 7 were meant.

Reassignment **deactivates** rather than deletes, so who taught what is
never lost; §47 is explicit about that, and the partial unique index
`uq_teacher_assignments_active_offering` enforces at most one active
assignment per offering, which is also what makes two people assigning
at the same moment an IntegrityError rather than a silent overwrite.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app import audit_service
from app.models.academic_structure import Section
from app.models.organization import Term
from app.models.subjects import SectionSubjectOffering, Subject, TeacherAssignment
from app.section_access import is_advised_by


@dataclass
class SubjectRow:
    """One subject in a section, with every term it is offered in."""

    subject: Subject
    offerings: list = field(default_factory=list)          # SectionSubjectOffering
    term_numbers: list = field(default_factory=list)
    # teacher_user_id -> how many of this subject's offerings they hold.
    holders: dict = field(default_factory=dict)

    @property
    def is_unassigned(self) -> bool:
        return not self.holders

    @property
    def is_split(self) -> bool:
        """Different teachers across the terms of one subject. Legal, and
        worth showing plainly rather than picking one to display."""
        return len(self.holders) > 1

    @property
    def sole_teacher_id(self):
        if len(self.holders) != 1:
            return None
        (teacher_id, count), = self.holders.items()
        return teacher_id if count == len(self.offerings) else None


def may_assign(section, current_user) -> bool:
    """A Super Admin anywhere; an adviser only in a section they advise."""
    if current_user.has_role("SUPER_ADMIN"):
        return True
    return current_user.has_role("ADVISER") and is_advised_by(section, current_user.id)


def load_section_subjects(session, section_id, school_year_id) -> list[SubjectRow]:
    """Every subject offered to the section, with its terms and holders.

    **Four queries, whatever the section's size.** The page renders one
    control per subject, and resolving the subject, the terms and the
    active assignment inside that loop would be ~60 round trips at 85ms
    each on every Streamlit rerun — the trap `tests/test_query_cost.py`
    exists for. Everything is loaded here, above the loop.
    """
    offerings = (
        session.query(SectionSubjectOffering)
        .filter_by(section_id=section_id, school_year_id=school_year_id)
        .all()
    )
    if not offerings:
        return []

    subject_ids = {o.subject_id for o in offerings}
    subjects = {
        s.id: s for s in session.query(Subject).filter(Subject.id.in_(subject_ids)).all()
    }
    terms = {
        t.id: t for t in session.query(Term).filter_by(school_year_id=school_year_id).all()
    }
    active = {
        a.section_subject_offering_id: a
        for a in session.query(TeacherAssignment)
        .filter(
            TeacherAssignment.section_subject_offering_id.in_([o.id for o in offerings]),
            TeacherAssignment.is_active.is_(True),
        )
        .all()
    }

    rows: dict = {}
    for offering in offerings:
        row = rows.setdefault(offering.subject_id, SubjectRow(subject=subjects[offering.subject_id]))
        row.offerings.append(offering)
        row.term_numbers.append(terms[offering.term_id].term_number)
        assignment = active.get(offering.id)
        if assignment is not None:
            key = str(assignment.teacher_user_id)
            row.holders[key] = row.holders.get(key, 0) + 1

    for row in rows.values():
        row.offerings.sort(key=lambda o: terms[o.term_id].term_number)
        row.term_numbers.sort()
    return sorted(rows.values(), key=lambda r: r.subject.official_name)


def _active_for(session, offering_ids) -> dict:
    return {
        a.section_subject_offering_id: a
        for a in session.query(TeacherAssignment)
        .filter(
            TeacherAssignment.section_subject_offering_id.in_(offering_ids),
            TeacherAssignment.is_active.is_(True),
        )
        .all()
    }


def assign_subject(session, row: SubjectRow, teacher_user_id, *, actor_user_id, section: Section):
    """Give every term of one subject to one teacher.

    Returns (written, replaced). Does not commit — the caller owns the
    transaction, so the audit entries and the assignments land together
    or not at all.
    """
    offering_ids = [o.id for o in row.offerings]
    active = _active_for(session, offering_ids)
    now = datetime.now(timezone.utc)

    written = replaced = 0
    for offering in row.offerings:
        current = active.get(offering.id)
        if current is not None:
            if str(current.teacher_user_id) == str(teacher_user_id):
                continue  # already theirs; leave the original assigned_at alone
            current.is_active = False
            current.unassigned_at = now
            replaced += 1
        session.add(
            TeacherAssignment(
                section_subject_offering_id=offering.id,
                teacher_user_id=teacher_user_id,
                assigned_by_user_id=actor_user_id,
                assigned_at=now,
            )
        )
        written += 1

    if written:
        # One entry per subject, not per term: the term rows are an
        # implementation detail of the same decision, and thirty-odd
        # entries per section would bury the log §50 exists to make
        # readable.
        audit_service.record(
            session,
            action=audit_service.TEACHER_ASSIGNED,
            object_type="section_subject_offerings",
            object_id=row.offerings[0].id,
            user_id=actor_user_id,
            previous={"teacher_user_ids": sorted(active_holders(active))} if active else None,
            new={
                "teacher_user_id": str(teacher_user_id),
                "section": section.name,
                "subject": row.subject.official_name,
                "terms": row.term_numbers,
            },
        )
    return written, replaced


def unassign_subject(session, row: SubjectRow, *, actor_user_id, section: Section) -> int:
    """Clear every term of one subject. Returns how many were cleared."""
    offering_ids = [o.id for o in row.offerings]
    active = _active_for(session, offering_ids)
    if not active:
        return 0

    now = datetime.now(timezone.utc)
    for assignment in active.values():
        assignment.is_active = False
        assignment.unassigned_at = now

    audit_service.record(
        session,
        action=audit_service.TEACHER_UNASSIGNED,
        object_type="section_subject_offerings",
        object_id=row.offerings[0].id,
        user_id=actor_user_id,
        previous={"teacher_user_ids": sorted(active_holders(active))},
        new={
            "section": section.name,
            "subject": row.subject.official_name,
            "terms": row.term_numbers,
        },
    )
    return len(active)


def active_holders(active: dict) -> set:
    return {str(a.teacher_user_id) for a in active.values()}
