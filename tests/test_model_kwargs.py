"""Every keyword passed to a model constructor must be a real column.

The bug this exists for: `commit_term_grades` passed
`encoded_by_user_id=user_id` to `TermGrade`. That column is on
`AttendanceRecord`, not `TermGrade` — the name is plausible and the code
reads fine. SQLAlchemy's declarative constructor raises TypeError for an
unknown keyword, so **importing a new term grade crashed every time**;
the update branch set the same name as a plain instance attribute, which
Python allows and the ORM silently discards.

It shipped, and no test caught it because none of them committed an
INSERT through that path. A dress rehearsal on a real section found it in
the first minute of encoding.

This checks statically, so it needs no database and covers the branch
that only runs for a brand-new row.
"""

import ast
import importlib
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

# Where a wrong-but-plausible column name would do real damage: the
# writers that create rows the school's records depend on.
MODULES = [
    "app.import_specs",
    "app.grading_service",
    "app.award_service",
    "app.academic_record_service",
    "app.attendance_service",
    "app.sf2_report",
    "app.sf4_report",
    "app.sf9_report",
]


def _model_columns(name: str):
    """The column names of a mapped class, or None if `name` isn't one."""
    for module in (
        "app.models.grades", "app.models.learners", "app.models.subjects",
        "app.models.attendance", "app.models.awards", "app.models.admin",
        "app.models.academic_record", "app.models.rbac", "app.models.organization",
        "app.models.academic_structure", "app.models.reports",
    ):
        cls = getattr(importlib.import_module(module), name, None)
        table = getattr(cls, "__table__", None)
        if table is not None:
            return {c.name for c in table.columns}
    return None


def _constructor_calls(tree):
    """(ClassName, [keywords], lineno) for every `Something(...)` call."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names = [k.arg for k in node.keywords if k.arg]
            if names:
                yield node.func.id, names, node.lineno


@pytest.mark.parametrize("module_name", MODULES)
def test_model_constructors_only_use_real_columns(module_name):
    path = APP / (module_name.split(".", 1)[1].replace(".", "/") + ".py")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    problems = []
    for cls_name, keywords, line in _constructor_calls(tree):
        columns = _model_columns(cls_name)
        if columns is None:
            continue  # not a mapped model — a dataclass, a helper, anything
        for keyword in keywords:
            if keyword not in columns:
                problems.append(
                    f"{path.name}:{line} {cls_name}({keyword}=...) — no such column. "
                    f"Did you mean one of {sorted(columns)[:6]}…?"
                )

    assert not problems, (
        "SQLAlchemy raises TypeError for these at INSERT time:\n  " + "\n  ".join(problems)
    )


def test_the_check_can_actually_resolve_models():
    """Guards the guard: if _model_columns stopped finding anything, every
    call above would be skipped and the test would pass on a broken app."""
    assert _model_columns("TermGrade"), "TermGrade should resolve to a mapped table"
    assert "encoded_by_user_id" in _model_columns("AttendanceRecord")
    assert "encoded_by_user_id" not in _model_columns("TermGrade"), (
        "this is the exact confusion the module docstring describes"
    )
    assert _model_columns("Sf4Row") is None, "plain dataclasses must be skipped"
