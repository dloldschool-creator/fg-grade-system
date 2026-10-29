"""How attendance is stored (2026-10-29): a saved P is a bit in
`attendance_encoded_days`, and only a day that says something else has an
`attendance_records` row. Offline — the database-backed behaviour is in
test_attendance_page.py.

The structural guard is the important one. Nothing about a reader that
queries `attendance_records` on its own looks wrong, and it would run fine:
it would just count every saved P as "not yet encoded" — on SF2, on the
report card, on Insights. So reading a day's status outside
`app/attendance_service.py` fails here instead.
"""

import ast
from datetime import date
from pathlib import Path

from app.attendance_service import FULL_MONTH, day_bit, month_of

APP = Path(__file__).resolve().parent.parent / "app"

# Files allowed to read AttendanceRecord rows themselves. attendance_service
# is the reader of both forms; nothing else needs to be.
ALLOWED = {"attendance_service.py"}


def test_every_day_of_a_month_has_its_own_bit_inside_a_signed_int():
    bits = [day_bit(date(2026, 7, day)) for day in range(1, 32)]
    assert len(set(bits)) == 31
    assert all(bit & FULL_MONTH == bit for bit in bits)
    assert FULL_MONTH == 2**31 - 1  # Postgres INTEGER is signed 32-bit
    assert sum(bits) == FULL_MONTH


def test_month_of_is_the_first_of_the_month():
    assert month_of(date(2026, 9, 17)) == date(2026, 9, 1)


def _reads_attendance_rows(tree: ast.AST) -> list[int]:
    """Line numbers that read a status off attendance_records directly:
    `AttendanceRecord.status`, or `query(AttendanceRecord)` /
    `select(AttendanceRecord)` loading whole rows."""
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "status"
            and isinstance(node.value, ast.Name)
            and node.value.id == "AttendanceRecord"
        ):
            found.append(node.lineno)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
            and (node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id)
            in {"query", "select"}
            and any(isinstance(a, ast.Name) and a.id == "AttendanceRecord" for a in node.args)
        ):
            found.append(node.lineno)
    return found


def test_only_attendance_service_reads_a_days_status_from_attendance_records():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        if path.name in ALLOWED or "models" in path.parts:
            continue
        for line in _reads_attendance_rows(ast.parse(path.read_text(encoding="utf-8"))):
            offenders.append(f"{path.relative_to(APP.parent)}:{line}")
    assert not offenders, (
        "These read attendance_records directly, which misses every saved P "
        "(a bit in attendance_encoded_days, no row). Use "
        "attendance_service.statuses_for_days / statuses_for_enrollment: "
        + ", ".join(offenders)
    )


def test_the_guard_catches_the_shape_it_exists_for():
    caught = _reads_attendance_rows(
        ast.parse(
            "session.query(AttendanceRecord.enrollment_id, AttendanceRecord.status)\n"
            "session.query(AttendanceRecord).all()\n"
            "select(AttendanceRecord)\n"
        )
    )
    assert sorted(caught) == [1, 2, 3]
    assert _reads_attendance_rows(ast.parse("session.query(AttendanceRecord.id)")) == []
