"""What the pages say to the people using them.

The page descriptions were originally written for whoever was building
the system, and drifted into citing the specification at teachers. These
tests keep the two audiences apart: a docstring or comment may cite the
spec freely — that is how the rules stay traceable — while a caption a
teacher reads may not.
"""

import ast
from pathlib import Path

import pytest

PAGES = sorted((Path(__file__).resolve().parent.parent / "app" / "admin_pages").glob("*.py"))

# Streamlit calls whose string arguments a user actually reads.
UI_CALLS = {
    "caption", "title", "header", "subheader", "write", "markdown",
    "warning", "info", "error", "success", "metric", "radio", "selectbox",
    "checkbox", "button", "download_button", "text_input", "text_area",
    "date_input", "number_input", "form_submit_button", "expander",
    "multiselect", "file_uploader", "toggle", "slider",
}

# Vocabulary that belongs in the codebase, not on a page. Each of these
# was actually on a page before this test existed.
JARGON = [
    "single source of truth",
    "append-only",
    "migration tooling",
    "derived grade tables",
    "seed data only",
    "CLAUDE.md",
    "master-spec",
    "not a fault",
]


def _ui_strings(path: Path):
    """Every string a Streamlit call renders, with its line number."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr not in UI_CALLS:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                yield node.lineno, sub.value


@pytest.mark.parametrize("path", PAGES, ids=[p.stem for p in PAGES])
def test_no_spec_citations_reach_the_screen(path):
    """"(§30)" tells a teacher nothing. The rule it refers to still has to
    be explained, or dropped — but never cited."""
    offenders = [f"line {line}: {text[:70]}" for line, text in _ui_strings(path) if "§" in text]
    assert not offenders, f"{path.name} shows spec references:\n" + "\n".join(offenders)


@pytest.mark.parametrize("path", PAGES, ids=[p.stem for p in PAGES])
def test_no_internal_vocabulary_reaches_the_screen(path):
    offenders = []
    for line, text in _ui_strings(path):
        lowered = text.lower()
        for phrase in JARGON:
            if phrase.lower() in lowered:
                offenders.append(f"line {line}: {phrase!r}")
    assert not offenders, f"{path.name} shows internal wording:\n" + "\n".join(offenders)


def test_docstrings_may_still_cite_the_spec():
    """The separation only works if the developer-facing half is left
    alone — the citations are what keep each rule traceable."""
    cited = [
        path.name
        for path in PAGES
        if "§" in (ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or "")
    ]
    assert cited, "expected at least one page docstring to still cite the spec"
