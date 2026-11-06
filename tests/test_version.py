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
    assert "(%s)" % version.UNKNOWN in version.version_line()


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
    assert line.startswith("%s (%s)" % (version.release(), version.short_sha()))
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


def test_release_reads_the_version_file_and_prefixes_v():
    text = open(version.os.path.join(version._ROOT, "VERSION"), encoding="utf-8").read().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", text), text
    assert version.release() == "v" + text


def test_release_line_warns_when_a_restart_is_pending(monkeypatch):
    monkeypatch.setattr(version, "_LOADED_SHA", "a" * 40)
    monkeypatch.setenv("APP_VERSION", "b" * 40)
    assert version.release_line().startswith(version.release())
    assert "(%s)" % version.short_sha() in version.release_line()
    assert "restart" in version.release_line()


def test_a_missing_version_file_reports_unknown(monkeypatch):
    monkeypatch.setattr(version, "_ROOT", "/nonexistent")
    assert version._read_release() == version.UNKNOWN


def test_the_release_is_the_one_loaded_not_the_one_on_disk(monkeypatch):
    """Same failure as the commit: a push without a restart changes VERSION
    on disk while the old code keeps running."""
    monkeypatch.setattr(version, "_LOADED_RELEASE", "v0.0.1")
    assert version.release() == "v0.0.1"
    assert version.release_line().startswith("v0.0.1 (")


def test_a_byte_order_mark_is_dropped(monkeypatch, tmp_path):
    (tmp_path / "VERSION").write_text("﻿1.2.3\r\n", encoding="utf-8")
    monkeypatch.setattr(version, "_ROOT", str(tmp_path))
    assert version._read_release() == "v1.2.3"


def _tags_on(commit):
    """v-tags naming `commit`, read from .git without running git.

    Loose tags are followed through their tag object (zlib, stdlib);
    packed tags use the peeled "^" line. A tag object that has itself been
    packed is skipped, which only makes this test skip, never fail wrongly.
    """
    import os
    import zlib

    git = version._GIT
    found = set()
    tag_dir = os.path.join(git, "refs", "tags")
    for name in os.listdir(tag_dir) if os.path.isdir(tag_dir) else []:
        sha = version._read(os.path.join(tag_dir, name))
        if not sha or not name.startswith("v"):
            continue
        target = sha
        obj = os.path.join(git, "objects", sha[:2], sha[2:])
        if os.path.exists(obj):
            with open(obj, "rb") as handle:
                raw = zlib.decompress(handle.read())
            if raw.startswith(b"tag "):
                body = raw.split(b"\x00", 1)[1].decode("utf-8", "replace")
                target = body.split("\n", 1)[0].split(" ", 1)[1]
        if target == commit:
            found.add(name)
    lines = (version._read(os.path.join(git, "packed-refs")) or "").splitlines()
    for i, line in enumerate(lines):
        if " refs/tags/v" not in line:
            continue
        sha, ref = line.split(" ", 1)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if (nxt[1:] if nxt.startswith("^") else sha) == commit:
            found.add(ref.rsplit("/", 1)[1])
    return found


def test_the_version_file_matches_the_tag_on_head_if_there_is_one():
    """docs/releases.md: a release tag and VERSION must match."""
    tags = _tags_on(version.disk_commit_sha())
    if not tags:
        pytest.skip("HEAD carries no v-tag")
    assert version._read_release() in tags, (version._read_release(), tags)
