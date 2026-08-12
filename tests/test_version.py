"""The deployed-version line.

Its whole job is to be trustworthy after a push, so the tests are about
it being correct and never being able to break a page.
"""

import re

import pytest

from app import version


def test_it_reports_the_commit_this_checkout_is_on():
    sha = version.commit_sha()
    assert sha != version.UNKNOWN, "no commit resolved from .git"
    assert re.fullmatch(r"[0-9a-f]{40}", sha), sha


def test_the_short_form_matches_what_git_log_shows():
    assert version.short_sha() == version.commit_sha()[:7]


def test_an_explicit_override_wins(monkeypatch):
    """A host that deploys without a .git directory can still report
    something useful."""
    monkeypatch.setenv("APP_VERSION", "release-2026-08")
    assert version.commit_sha() == "release-2026-08"


def test_a_missing_git_directory_reports_unknown_rather_than_failing(monkeypatch):
    """A version line is never worth an error page."""
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.setattr(version, "_GIT", "/nonexistent/.git")
    assert version.commit_sha() == version.UNKNOWN
    assert version.version_line().startswith(version.UNKNOWN)


@pytest.mark.parametrize(
    "seconds, expected",
    [(5, "up 5s"), (90, "up 1m"), (3700, "up 1h 1m"), (90000, "up 1d 1h")],
)
def test_uptime_reads_plainly(monkeypatch, seconds, expected):
    """Relative, not a timestamp: the server runs in UTC and the school
    does not, and "up 4m" answers "did it just restart?" without anyone
    doing the arithmetic."""
    monkeypatch.setattr(version, "_STARTED_AT", version.time.time() - seconds)
    assert version.uptime() == expected


def test_the_line_carries_both_facts():
    line = version.version_line()
    assert version.short_sha() in line
    assert "up " in line


def test_it_shells_out_to_nothing():
    """Reading git by running git would be the obvious implementation and
    the wrong one — nothing under app/ may spawn a process, which is what
    keeps the deployment to "pip install and run". Git's refs are plain
    text, so file reads suffice.

    Checked by parsing the imports, not by searching the text: the
    module's own docstring explains this rule and must be allowed to.
    """
    import ast

    tree = ast.parse(open(version.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert "subprocess" not in imported
    assert imported <= {"os", "time", "datetime"}, f"unexpected imports: {imported}"
