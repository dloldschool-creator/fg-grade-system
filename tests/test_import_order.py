"""A module every page imports must not initialise `app.models` at load.

This is the rule that took the live app down on 2026-08-12, and the only
one of the recorded traps with no test behind it until now.

**What happened.** `app/admin_pages/_helpers.py` gained
`from app.models.academic_structure import ...` at module level. Every
page imports `_helpers`, and isort sorts `app.admin_pages._helpers` above
`app.auth`, so `_helpers` became the *first* thing to initialise
`app.models` — a package whose `__init__` imports its own submodules
while still initialising itself. On the host's Python 3.14 that re-entry
executed a model module twice and the second `class GradeLevel(...)`
raised `InvalidRequestError: Table 'grade_levels' is already defined`.
Nothing rendered. Everything had passed locally on 3.13 first.

**Why this is structural and not behavioural.** The crash needs 3.14; the
pin in `test_deployment_contract.py` now keeps both sides on 3.13, so
importing these modules in a subprocess proves nothing here. The test
therefore asserts the *shape* — no load-time `app.models` in a
universally-imported module — which holds whatever the interpreter does.

**The set is derived, not listed.** CLAUDE.md states the rule about
`_helpers.py` by name, but the hazard belongs to a class of file: any
module most pages import at load dictates the whole app's import order.
Naming one file means the next such helper — `section_access`,
`roster_order`, something not yet written — inherits the trap without
inheriting the rule. So the pages are read, their shared imports counted,
and whatever comes out is what gets checked.
"""

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"

# Empty, and worth keeping that way. `app.auth` sat here until
# 2026-08-21: imported by every page and the entrypoint, doing
# `from app.models.rbac import ...` at module level, exempted on the
# evidence that it had come through the whole 3.14 period without
# crashing. Surviving is not the same as being safe — it never explained
# why `app.models.rbac` was survivable where `app.models.academic_structure`
# was not — and the fix was three lines, so it was closed instead of
# carried. Anything added back needs better grounds than "it hasn't
# crashed yet".
KNOWN_EXCEPTIONS = set()


def _page_files():
    """Every module Streamlit loads as a page, plus the entrypoint."""
    pages = [
        p
        for p in sorted((APP / "admin_pages").glob("*.py"))
        if p.name not in ("__init__.py", "_helpers.py")
    ]
    return pages + [ROOT / "streamlit_app.py"]


def _module_name(path):
    rel = path.resolve().relative_to(ROOT).with_suffix("")
    return ".".join(rel.parts)


def _load_time_imports(path):
    """First-party modules imported at MODULE level.

    Body-level only: an import inside a function is exactly the fix this
    rule asks for, so it must not count against the file that applies it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.split(".")[0] == "app")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.split(".")[0] == "app":
                found.add(node.module)
    return found


def _universal_modules():
    """Modules at least half the pages import at load, models aside.

    Half rather than all: `_helpers` is imported by every *page* but not
    by `streamlit_app.py`, and an intersection would drop the one file
    the rule was written about.
    """
    pages = _page_files()
    counts = {}
    for page in pages:
        for module in _load_time_imports(page):
            counts[module] = counts.get(module, 0) + 1

    return {
        module
        for module, seen in counts.items()
        if seen >= len(pages) / 2
        and not module.startswith("app.models")
        and (APP.parent / pathlib.Path(*module.split("."))).with_suffix(".py").exists()
    }


def _closure(module):
    """`module` plus everything it pulls in at load, first-party only.

    A universal module is only as safe as what it imports: `_helpers`
    importing `app.database` would be just as fatal if `app.database`
    imported `app.models`.
    """
    seen, queue = set(), [module]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        path = (APP.parent / pathlib.Path(*name.split("."))).with_suffix(".py")
        if path.exists():
            queue.extend(_load_time_imports(path))
    return seen


def test_the_universally_imported_modules_are_the_ones_expected():
    """Guards the derivation itself. If this list changes, a new module
    has become load-bearing for import order and the test below now
    governs it — which is the point, but it should be a visible event."""
    assert _universal_modules() == {"app.admin_pages._helpers", "app.auth"}


def test_no_universal_module_reaches_app_models_at_load():
    offenders = {}
    for module in sorted(_universal_modules() - KNOWN_EXCEPTIONS):
        reached = sorted(m for m in _closure(module) if m.startswith("app.models"))
        if reached:
            offenders[module] = reached

    assert not offenders, (
        "these modules are imported by most pages, so they decide when "
        f"`app.models` first initialises: {offenders}. Move the import "
        "inside the function that needs it — `section_picker` in "
        "`_helpers.py` is the worked example."
    )


def test_section_access_stays_dependency_free():
    """CLAUDE.md keeps `app.section_access` importing nothing first-party
    so that importing it can never affect import order. It is small, it is
    imported from page code, and it would be an easy one to 'tidy'."""
    assert _load_time_imports(APP / "section_access.py") == set()
