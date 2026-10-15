"""scripts/run_tests.py picks which tests to run from what you changed, so a
wrong pick means a test silently not run. These pin the picks that matter."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_tests  # noqa: E402


def test_a_change_to_a_service_selects_the_tests_that_import_it_directly():
    files, _ = run_tests.select_tests(["app/attendance_service.py"])
    assert "tests/test_attendance_reopen.py" in files
    assert "tests/test_enrollment_withdrawal.py" in files


def test_a_change_to_a_page_selects_its_own_test_and_the_tree_guards():
    files, _ = run_tests.select_tests(["app/admin_pages/sf2.py"])
    assert "tests/test_sf2_report.py" in files
    # They read source files rather than importing them, so only the
    # tree-scan rule can bring them in.
    assert "tests/test_import_order.py" in files
    assert "tests/test_expander_cost.py" in files


def test_a_change_reaches_tests_through_a_module_that_imports_it():
    """section_access is imported by attendance_service's pages, not by the
    attendance tests: the walk has to go through the importer."""
    files, _ = run_tests.select_tests(["app/section_access.py"])
    assert "tests/test_attendance_reopen.py" in files


def test_a_new_test_file_selects_itself():
    files, _ = run_tests.select_tests(["tests/test_brand_new.py"])
    assert "tests/test_brand_new.py" in files


def test_docs_only_changes_run_nothing():
    files, _ = run_tests.select_tests(["docs/build-log.md", "CLAUDE.md"])
    assert files == []


def test_changes_the_selection_cannot_reason_about_run_everything():
    for path in ("requirements.txt", "alembic/versions/x.py", "tests/conftest.py",
                 "pytest.ini", ".streamlit/config.toml", "sf-templates/sf9.xlsx"):
        files, _ = run_tests.select_tests([path])
        assert files is None, path


def test_mentions_finds_a_module_by_path_or_by_from_import():
    assert run_tests.mentions("import app.audit_service", "app.audit_service")
    assert run_tests.mentions("from app import audit_service", "app.audit_service")
    assert run_tests.mentions("from app import (\n    foo,\n    audit_service,\n)", "app.audit_service")
    # Inside a string, as AppTest scripts import pages.
    assert run_tests.mentions('"from app.admin_pages import sf2"', "app.admin_pages.sf2")


def test_mentions_does_not_match_a_longer_name_or_a_different_package():
    assert not run_tests.mentions("import app.audit_service_extra", "app.audit_service")
    assert not run_tests.mentions("from other import audit_service", "app.audit_service")
