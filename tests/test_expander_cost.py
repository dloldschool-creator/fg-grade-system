"""No page may query or render a document inside a per-row expander.

**Streamlit runs an `st.expander()` body whether or not it is open.** A
collapsed panel is not a skipped one — so a query inside one is paid for
every row on every rerun, and Streamlit reruns the whole script on every
widget interaction. At ~85ms per round trip that is how the Awards page
came to spend 13.6 seconds per click on a forty-learner section, and the
Grade Summary ten.

Nothing about it looks wrong in the source, which is the point of testing
it structurally instead of trusting review. This reads the AST rather
than the text so that a docstring *describing* the rule can't trip it.

The fix is always the same: load it into a dict above the loop, and hand
each panel its slice.
"""

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

# Rendering these scales with the roster, so they belong behind a Build
# button rather than in a body that runs on every rerun.
EXPENSIVE_CALLS = ("generate_", "build_sf9", "workbooks_to_pdf")


def _page_files():
    return sorted(APP.rglob("*.py"))


def _is_st_call(node, attr):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
    )


def _contains_expander(node):
    return any(_is_st_call(n, "expander") for n in ast.walk(node))


def _session_calls(node):
    """`session.query(...)` / `session.get(...)` anywhere under `node`."""
    found = []
    for n in ast.walk(node):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "session"
            and n.func.attr in ("query", "get")
        ):
            found.append(n.lineno)
    return found


def _guarded_by_a_button(loop, target):
    """True when `target` only runs after the user pressed something.

    A build-on-click download is fine inside a loop — that is exactly the
    pattern SF9, Term Cards and Awards use — so it must not be reported.
    """
    for node in ast.walk(loop):
        if isinstance(node, ast.If) and any(
            _is_st_call(n, "button") or _is_st_call(n, "form_submit_button")
            for n in ast.walk(node.test)
        ):
            if target in {id(n) for n in ast.walk(node)}:
                return True
    return False


def _row_loops(tree):
    """Every `for` loop that draws an expander per row."""
    return [n for n in ast.walk(tree) if isinstance(n, ast.For) and _contains_expander(n)]


@pytest.mark.parametrize("path", _page_files(), ids=lambda p: p.name)
def test_no_queries_inside_a_per_row_expander_loop(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    local_funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    offenders = []
    for loop in _row_loops(tree):
        for line in _session_calls(loop):
            offenders.append(f"{path.name}:{line} queries directly in the loop")
        # A helper called from the loop costs the same as inlining it.
        for node in ast.walk(loop):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in local_funcs
            ):
                for line in _session_calls(local_funcs[node.func.id]):
                    offenders.append(
                        f"{path.name}:{line} queries inside {node.func.id}(), "
                        f"called from the loop at line {loop.lineno}"
                    )

    assert not offenders, (
        "A per-row st.expander body runs even when collapsed, so these are "
        "paid once per row on every rerun. Batch them above the loop:\n  "
        + "\n  ".join(sorted(set(offenders)))
    )


@pytest.mark.parametrize("path", _page_files(), ids=lambda p: p.name)
def test_no_ungated_document_generation_inside_a_per_row_loop(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for loop in _row_loops(tree):
        for node in ast.walk(loop):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id.startswith(EXPENSIVE_CALLS)
                and not _guarded_by_a_button(loop, id(node))
            ):
                offenders.append(f"{path.name}:{node.lineno} calls {node.func.id}()")

    assert not offenders, (
        "Document generation in a per-row expander renders for every row on "
        "every rerun. Put it behind a Build button:\n  " + "\n  ".join(sorted(set(offenders)))
    )


def test_the_scan_actually_finds_the_loops_it_is_meant_to_guard():
    """A structural test that silently matches nothing always passes. This
    fails if the pages stop being shaped the way the scan assumes."""
    total = sum(len(_row_loops(ast.parse(p.read_text(encoding="utf-8")))) for p in _page_files())
    assert total >= 10, f"expected the per-row expander pages to still exist, found {total}"
