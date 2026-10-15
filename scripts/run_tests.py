"""Run the right amount of the test suite.

    python scripts/run_tests.py fast      # offline tier, ~30 s, no network
    python scripts/run_tests.py changed   # tests your uncommitted edits can affect
    python scripts/run_tests.py full      # everything, including slow sweeps

Extra arguments go to pytest:  ``... changed -x -k reopen``.

**What `changed` is, and is not.** It starts from the files git says you
changed (staged, unstaged and new), walks the import graph of ``app/`` and
``scripts/`` to every module that depends on them, and runs the test files
that mention any of those modules. Tree-scanning guards (import order,
expander cost, read-only roles, model defaults...) always run, because they
read source files rather than importing them, so no import edge points at
them. It is a *selection*, not a proof: it follows static imports, so it can
miss a dependency that only exists in data. That is why `full` is the gate
before a push that reaches the running app.

Changes it cannot reason about send you to `full`: requirements,
migrations, templates, Streamlit config, pytest config.
"""

from __future__ import annotations

import ast
import functools
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

# A change to any of these can break tests that import none of them.
RUN_FULL_IF_CHANGED = (
    "requirements.txt",
    "pytest.ini",
    "tests/conftest.py",
    ".streamlit/",
    "alembic/",
    "sf-templates/",
)

# Test files that scan the source tree instead of importing from it.
_TREE_SCAN = re.compile(r"rglob|\.glob\(|os\.walk|iterdir|ast\.parse\(.*read_text")


def git_lines(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def changed_files() -> list[str]:
    tracked = git_lines("diff", "--name-only", "HEAD")
    new = git_lines("ls-files", "--others", "--exclude-standard")
    return sorted(set(tracked) | set(new))


def module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@functools.lru_cache(maxsize=1)
def project_modules() -> dict[str, Path]:
    found = {}
    for folder in ("app", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            if "__pycache__" not in path.parts:
                found[module_name(path)] = path
    return found


def imports_of(path: Path, known: set[str]) -> set[str]:
    """Project modules this file imports, anywhere in it (function-level
    imports count: the import-order rule makes them common here)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            for alias in node.names:  # `from app import audit_service`
                found.add(f"{node.module}.{alias.name}")
    return {name for name in found if name in known}


@functools.lru_cache(maxsize=1)
def import_graph() -> dict[str, set[str]]:
    modules = project_modules()
    return {name: imports_of(path, set(modules)) for name, path in modules.items()}


def affected_modules(changed: set[str], graph: dict[str, set[str]]) -> set[str]:
    """The changed modules plus everything that imports them, transitively."""
    reverse: dict[str, set[str]] = {}
    for module, deps in graph.items():
        for dep in deps:
            reverse.setdefault(dep, set()).add(module)
    seen, stack = set(changed), list(changed)
    while stack:
        for parent in reverse.get(stack.pop(), ()):
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return seen


def mentions(text: str, module: str) -> bool:
    """Does a test file refer to `module`, by dotted path or `from pkg import name`?
    Matches inside strings too — AppTest scripts import pages in a string."""
    if re.search(rf"(?<![\w.]){re.escape(module)}(?![\w])", text):
        return True
    package, _, name = module.rpartition(".")
    if not package:
        return False
    for match in re.finditer(rf"from\s+{re.escape(package)}\s+import\s+(\([^)]*\)|[^\n]*)", text):
        if re.search(rf"\b{re.escape(name)}\b", match.group(1)):
            return True
    return False


def select_tests(changed: list[str]) -> tuple[list[str] | None, str]:
    """-> (test files to run, a one-line reason). ``None`` means run everything;
    an empty list means no test file applies."""
    for path in changed:
        if path.startswith(RUN_FULL_IF_CHANGED):
            return None, f"{path} can affect any test, so running everything"

    if not any(p.endswith(".py") for p in changed):
        return [], "no Python file changed (docs only?), so no test can be affected"

    modules = project_modules()
    graph = import_graph()
    changed_modules = {
        module_name(ROOT / p) for p in changed if p.endswith(".py") and (ROOT / p).exists()
    } & set(modules)
    changed_tests = {p for p in changed if p.startswith("tests/") and p.endswith(".py")}

    selected = set(changed_tests)
    affected = affected_modules(changed_modules, graph) if changed_modules else set()
    for test in TESTS.glob("test_*.py"):
        text = test.read_text(encoding="utf-8", errors="ignore")
        rel = test.relative_to(ROOT).as_posix()
        if _TREE_SCAN.search(text) or any(mentions(text, m) for m in affected):
            selected.add(rel)

    reason = (
        f"{len(changed_modules)} changed module(s) -> {len(affected)} affected -> "
        f"{len(selected)} test file(s)"
    )
    return sorted(selected), reason


def parallel_args() -> list[str]:
    """Four workers when pytest-xdist is installed, serial otherwise. Safe
    because every test rolls back and none shares a row (see CLAUDE.md)."""
    return ["-n", "4"] if importlib.util.find_spec("xdist") else []


def run_pytest(extra: list[str]) -> int:
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra]
    print("+", " ".join(command[3:]), flush=True)
    return subprocess.call(command, cwd=ROOT)


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"fast", "changed", "full"}:
        print(__doc__)
        return 2
    mode, extra = argv[0], argv[1:]

    if mode == "fast":
        return run_pytest(["-m", "not db", *extra])
    if mode == "full":
        return run_pytest(["--runslow", *parallel_args(), *extra])

    changed = changed_files()
    if not changed:
        print("No uncommitted changes — nothing for `changed` to test.")
        return 0
    files, reason = select_tests(changed)
    print(reason)
    if files is None:
        return run_pytest(["--runslow", *parallel_args(), *extra])
    if not files:
        print("Nothing to run.")
        return 0
    return run_pytest([*files, *extra])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
