"""Guards the things a deployment depends on.

Documentation that drifts from the code is worse than none: a wrong
environment-variable name is discovered at 7am on the morning the app is
supposed to be live, by someone who cannot read the source to check.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
DEPLOYMENT = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
OPERATIONS = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

# Read by app/database.py, app/auth.py and app/supabase_clients.py.
REQUIRED = [
    "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
    "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY",
]
OPTIONAL = [
    "SESSION_TIMEOUT_MINUTES",
    "DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT", "DB_POOL_RECYCLE",
]


@pytest.mark.parametrize("name", REQUIRED + OPTIONAL)
def test_every_env_var_the_code_reads_is_in_the_example(name):
    assert re.search(rf"^{name}=", ENV_EXAMPLE, re.M), f"{name} missing from .env.example"


def test_the_env_vars_the_code_reads_are_the_ones_documented():
    """Catches a rename in the code that the example file didn't follow —
    e.g. DB_MAX_OVERFLOW being written as DB_POOL_MAX_OVERFLOW."""
    sources = "\n".join(
        (ROOT / "app" / name).read_text(encoding="utf-8")
        for name in ("database.py", "auth.py", "supabase_clients.py")
    )
    read_by_code = set(re.findall(r"environ\.get\(\s*[\"'](\w+)[\"']", sources))
    read_by_code |= set(re.findall(r"getenv\(\s*[\"'](\w+)[\"']", sources))
    documented = set(re.findall(r"^(\w+)=", ENV_EXAMPLE, re.M))
    assert read_by_code <= documented, f"undocumented: {sorted(read_by_code - documented)}"


@pytest.mark.parametrize("name", REQUIRED)
def test_deployment_doc_lists_every_required_secret(name):
    assert name in DEPLOYMENT, f"{name} missing from docs/deployment.md"


def test_no_secret_value_is_committed():
    """The anon key is publishable and fine; the service-role key and the
    database password never are."""
    assert re.search(r"^SUPABASE_SERVICE_ROLE_KEY=\s*$", ENV_EXAMPLE, re.M)
    assert re.search(r"^DB_PASSWORD=\s*$", ENV_EXAMPLE, re.M)


def test_env_is_gitignored():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in ignored]


def test_the_entrypoint_is_where_the_docs_say():
    """streamlit_app.py must stay at the repo root — Streamlit only adds
    the entrypoint's own directory to sys.path, so moving it into app/
    breaks every `from app.… import`."""
    assert (ROOT / "streamlit_app.py").exists()
    assert "streamlit run streamlit_app.py" in DEPLOYMENT


def test_the_runbook_covers_the_operations_that_bite():
    """Each of these is a procedure that goes wrong destructively if
    improvised: migration ordering, template replacement, and the
    encoding toggle a teacher will phone about.

    Matched on what a coordinator would actually search for, not on
    column names — the runbook is deliberately written without Python
    identifiers in it.
    """
    for topic in ("alembic upgrade head", "sf-templates/", "OPEN / CLOSED"):
        assert topic in OPERATIONS, f"operations.md does not cover {topic}"


def test_no_os_level_dependency_crept_back_in():
    """The whole deployment story is "pip install and run". Shelling out
    to an external binary would quietly reintroduce the packaging problem
    that removing LibreOffice solved — and it would fail on the host
    rather than here.

    Parsed rather than grepped: a docstring is allowed to *explain* the
    rule without tripping it, which a substring search cannot tell apart
    from breaking it.
    """
    import ast

    banned = {"subprocess", "os.system", "popen"}
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            elif isinstance(node, ast.Attribute):
                names = {node.attr}
            else:
                continue
            if names & banned:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}: {sorted(names & banned)}")

    # `soffice` only ever appears as a string, so it still needs a text
    # check — but only outside docstrings and comments.
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "soffice" in node.value and not _is_docstring(tree, node):
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}: soffice literal")

    assert not offenders, f"external-process dependency in {offenders}"


def _is_docstring(tree, node) -> bool:
    for parent in ast.walk(tree):
        body = getattr(parent, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and first.value is node:
                return True
    return False
