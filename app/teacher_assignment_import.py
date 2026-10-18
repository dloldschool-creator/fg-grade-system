"""Bulk teacher assignment from a spreadsheet (§47).

The page-level version of `scripts/import_teacher_assignments.py`: the same
file shape (Section, Subject, Teacher), the same rule that **nothing is
created** — a name that doesn't resolve is an error, never a new row — but
it goes through `teacher_assignment_service.assign_subject`, so a row is
audited per subject exactly as the one-at-a-time form does, and a
reassignment deactivates rather than deletes.

**An assignment is an access grant** (see the service docstring), so this
is Super Admin only; an adviser keeps the per-section form.

A row with a blank Teacher is skipped, not an error: the downloadable
template lists every subject that has no teacher yet, and a file with only
some of them filled in is the normal case.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.import_pipeline import ColumnSpec
from app.import_specs import columns_template_bytes
from app.models.academic_structure import Section
from app.teacher_assignment_service import (
    active_teachers,
    assign_subject,
    load_year_subjects,
)

COLUMNS = [
    ColumnSpec("section", "Section", True, ("sectionname", "class")),
    ColumnSpec("subject", "Subject", True, ("subjectname", "learningarea")),
    ColumnSpec("teacher", "Teacher", True, ("teachername", "subjectteacher")),
]


def key(value) -> str:
    """Letters and digits only, so punctuation, 'Ñ' and double spaces can't
    be the reason a real name fails to resolve."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper().replace("Ñ", "N"))


@dataclass
class Plan:
    """What a file would do. `assignments` are (SubjectRow, teacher, section)."""

    assignments: list = field(default_factory=list)
    already: int = 0
    skipped_blank: int = 0
    conflicts: list = field(default_factory=list)  # (SubjectRow, teacher, section)
    errors: list = field(default_factory=list)  # {"row", "column", "message"}


def _sections_by_name(session, school_year_id) -> dict:
    by_name = defaultdict(list)
    for section in session.query(Section).filter_by(school_year_id=school_year_id).all():
        by_name[key(section.name)].append(section)
    return by_name


def build_plan(session, rows: list[dict], school_year_id, *, replace: bool = False) -> Plan:
    """Resolve every row without writing anything.

    `rows` are mapped rows (`section`, `subject`, `teacher`, `__row__`).
    A subject already held by a *different* teacher lands in `conflicts`;
    with `replace` it is also planned as a reassignment.
    """
    plan = Plan()
    sections = _sections_by_name(session, school_year_id)
    teachers = defaultdict(list)
    for user in active_teachers(session):
        teachers[key(user.full_name)].append(user)
        teachers[key(user.email)].append(user)

    # The whole year at once: four queries, not four per section named.
    loaded = load_year_subjects(session, school_year_id)  # section id -> [SubjectRow]
    seen: dict = {}  # (section id, subject id) -> teacher id

    def error(row, column, message):
        plan.errors.append({"row": row.get("__row__"), "column": column, "message": message})

    for row in rows:
        section_name = row.get("section", "").strip()
        subject_name = row.get("subject", "").strip()
        teacher_name = row.get("teacher", "").strip()
        if not (section_name or subject_name or teacher_name):
            continue
        if not teacher_name:
            plan.skipped_blank += 1
            continue

        found = sections.get(key(section_name), [])
        if len(found) != 1:
            error(row, "Section", f"{section_name!r}: " + (
                "no such section this school year" if not found
                else f"{len(found)} sections share that name"))
            continue
        section = found[0]

        matched = teachers.get(key(teacher_name), [])
        if len(matched) != 1:
            error(row, "Teacher", f"{teacher_name!r}: " + (
                "no active SUBJECT_TEACHER account with that name or email" if not matched
                else f"{len(matched)} accounts match; use the email address"))
            continue
        teacher = matched[0]

        wanted = key(subject_name)
        if not wanted:
            error(row, "Subject", "no subject given")
            continue
        # A blank short name or code keys to "", which must never match.
        hits = [
            r for r in loaded.get(section.id, [])
            if wanted in {
                k for k in (
                    key(r.subject.official_name), key(r.subject.short_name), key(r.subject.code)
                ) if k
            }
        ]
        if len(hits) != 1:
            error(row, "Subject", f"{subject_name!r}: " + (
                f"not offered to {section.name} this school year" if not hits
                else f"{len(hits)} subjects in {section.name} match"))
            continue
        subject_row = hits[0]

        marker = (section.id, subject_row.subject.id)
        if marker in seen and seen[marker] != teacher.id:
            error(row, "Subject", f"{subject_name!r} appears twice for {section.name} with different teachers")
            continue
        if marker in seen:
            continue
        seen[marker] = teacher.id

        others = set(subject_row.holders) - {str(teacher.id)}
        if others:
            # Someone else holds at least one term.
            plan.conflicts.append((subject_row, teacher, section))
            if replace:
                plan.assignments.append((subject_row, teacher, section))
        elif subject_row.has_unassigned_term:
            # Nobody, or only this teacher with a term still open: fill it in.
            # assign_subject leaves the terms they already hold alone.
            plan.assignments.append((subject_row, teacher, section))
        else:
            plan.already += 1
    return plan


def commit_plan(session, plan: Plan, actor_user_id) -> int:
    """Write the planned assignments. Does not commit. Returns terms written."""
    written = 0
    for subject_row, teacher, section in plan.assignments:
        count, _replaced = assign_subject(
            session, subject_row, teacher.id, actor_user_id=actor_user_id, section=section
        )
        written += count
    return written


def template_bytes(session, school_year_id) -> bytes:
    """Every subject with a term still without a teacher, Teacher blank."""
    rows = []
    sections = (
        session.query(Section).filter_by(school_year_id=school_year_id).order_by(Section.name).all()
    )
    by_section = load_year_subjects(session, school_year_id)
    for section in sections:
        for subject_row in by_section.get(section.id, []):
            if subject_row.has_unassigned_term:
                rows.append({"section": section.name, "subject": subject_row.subject.official_name})
    return columns_template_bytes(COLUMNS, extra_rows=rows)
