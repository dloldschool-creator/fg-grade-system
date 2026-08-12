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
    assert version.disk_commit_sha() == "release-2026-08"


def test_a_missing_git_directory_reports_unknown_rather_than_failing(monkeypatch):
    """A version line is never worth an error page."""
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.setattr(version, "_GIT", "/nonexistent/.git")
    monkeypatch.setattr(version, "_LOADED_SHA", version.UNKNOWN)
    assert version.commit_sha() == version.UNKNOWN
    assert version.version_line().startswith(version.UNKNOWN)


def test_the_reported_commit_is_the_one_loaded_not_the_one_on_disk(monkeypatch):
    """The failure this exists to catch: a host pulls a new commit without
    restarting, so the files move on while the loaded modules do not.
    Reading .git fresh reported the new SHA and looked like a successful
    deploy, when nothing had actually changed."""
    monkeypatch.setattr(version, "_LOADED_SHA", "a" * 40)
    monkeypatch.setenv("APP_VERSION", "b" * 40)

    assert version.commit_sha() == "a" * 40, "must name the running commit"
    assert version.disk_commit_sha() == "b" * 40
    assert version.restart_pending()


def test_a_pending_restart_is_stated_on_the_line(monkeypatch):
    monkeypatch.setattr(version, "_LOADED_SHA", "a" * 40)
    monkeypatch.setenv("APP_VERSION", "b" * 40)
    line = version.version_line()
    assert "aaaaaaa" in line
    assert "bbbbbbb" in line
    assert "restart" in line


def test_nothing_pending_when_disk_and_process_agree(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "c" * 40)
    monkeypatch.setattr(version, "_LOADED_SHA", "c" * 40)
    assert not version.restart_pending()
    assert "restart" not in version.version_line()


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
