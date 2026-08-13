"""A server default has to be a SQL call, not a string that looks like one.

Fourteen columns were declared ``mapped_column(server_default="now()")``.
SQLAlchemy treats a *string* server default as a literal value, so the DDL
went out as ``DEFAULT 'now()'`` and Postgres resolved it once, at migration
time, into a fixed constant:

    audit_logs.created_at DEFAULT '2026-08-10 04:31:28.755305'::timestamp

Every audit entry then reported the same creation time, days apart. Nothing
errored; the column was populated, non-null, and plausible. That is what makes
it worth a test — the failure is silent, and the only symptom is a log that
cannot order two events.

Checked against the models rather than the database so it fails on the machine
of whoever writes the next model, not weeks later in production.
"""

import ast
from pathlib import Path

import pytest

MODELS = sorted((Path(__file__).resolve().parent.parent / "app" / "models").glob("*.py"))

# Strings that are genuinely constant values are fine as a literal default —
# an enum's value, a number, a boolean. What must never be a string is a call.
LOOKS_LIKE_A_CALL = "("


def _server_default_strings(path: Path):
    """Yield (line, value) for every server_default given a plain string."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "server_default":
                continue
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                yield value.lineno, value.value


@pytest.mark.parametrize("path", MODELS, ids=lambda p: p.name)
def test_no_server_default_is_a_sql_call_written_as_a_string(path):
    offenders = [
        f"{path.name}:{line} server_default={value!r}"
        for line, value in _server_default_strings(path)
        if LOOKS_LIKE_A_CALL in value
    ]
    assert not offenders, (
        "a string server_default is emitted as a literal, so Postgres freezes "
        "its value at migration time — use func.now() or text(...) instead of "
        f"a string: {offenders}"
    )


def test_the_timestamp_mixin_still_uses_a_live_call():
    """The mixin was always right, and is why the tables inheriting it escaped
    the bug. If it ever regresses, most of the database goes at once."""
    source = (Path(__file__).resolve().parent.parent / "app" / "models" / "base.py").read_text(
        encoding="utf-8"
    )
    assert "server_default=func.now()" in source
    assert 'server_default="now()"' not in source


def test_every_model_file_was_actually_examined():
    """Guards the glob: a models package that silently stops matching would
    make every assertion above vacuous."""
    names = {path.name for path in MODELS}
    assert {"base.py", "admin.py", "awards.py", "rbac.py"} <= names
    assert len(MODELS) >= 10
