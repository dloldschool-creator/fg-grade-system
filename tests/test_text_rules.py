"""Required reasons are plausible text, and no free-text box is unbounded.

The reason rules are a speed bump, not a proof (see app/text_rules.py): the
cases below pin down both what they stop and — as importantly — what they
must let through, because a false reject blocks a registrar mid-task.

The structural test at the bottom exists because a missing `max_chars`
looks perfectly fine in the source and in a browser. Nothing fails until
someone pastes a page of text into a free-tier database.
"""

import ast
import pathlib

import pytest

from app import text_rules
from app.text_rules import (
    COMPOSED_REASON_MAX,
    REASON_MAX,
    ReasonInvalid,
    clean_text,
    reason_problem,
    validate_reason,
)

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


@pytest.mark.parametrize(
    "text",
    [
        "Not enrolled in LIS per school head's directive",
        "Enrolled in the wrong section by mistake",
        "Mali ang section na nailagay sa enrollment",  # Filipino
        "Dropped per SF9 request",  # abbreviations and a digit
        "Typo in LRN, corrected by registrar",
        "Transfer to T-L section",
        "  spaced   out\nover\nlines  ",  # whitespace is normalised, not rejected
        "hahahaha okay lang po",  # loose: repeated syllables are not one character
        "Grade 11 learner moved to Jr. section",
        "a" * 3 + " tama na ito",  # a run of three is fine
        "x" * 0 + "Reason of exactly ten",
    ],
)
def test_real_reasons_pass(text):
    assert reason_problem(text) is None
    assert validate_reason(text) == clean_text(text)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "\n\t",
        ".",
        "ok",
        "asdf",
        "asdfghjkl",  # long enough, but one word
        "..........",
        "1234567890",  # no letters
        "12 34 56 78 90",  # words by spacing, no letters
        "aaaaaaaa bbbb",  # a character repeated four or more times
        "see   ....   here",
        "no reason!!!!",
        "x y z q",  # too short once trimmed
    ],
)
def test_lazy_reasons_fail(text):
    assert reason_problem(text) is not None
    with pytest.raises(ReasonInvalid):
        validate_reason(text)


def test_reason_invalid_is_a_value_error():
    # Callers that already catch ValueError (reopen_month, set_override)
    # keep working without being edited.
    assert issubclass(ReasonInvalid, ValueError)


def test_too_long_reason_is_refused_with_its_length():
    problem = reason_problem("word " * 200)
    assert problem is not None and str(REASON_MAX) in problem
    assert reason_problem("word " * 50) is None  # 249 characters


def test_composed_limit_is_a_separate_larger_cap():
    wrapped = "Enrollment withdrawal of " + "Juan Dela Cruz " * 6 + " authorized by " + "Maria Santos " * 7 + ": " + "word " * 59
    assert len(wrapped) > REASON_MAX
    assert reason_problem(wrapped) is not None
    assert reason_problem(wrapped, max_chars=COMPOSED_REASON_MAX) is None


def test_clean_text_collapses_whitespace():
    assert clean_text("  a \n\n b\t c ") == "a b c"
    assert clean_text(None) == ""


def test_limits_are_ordered_sensibly():
    assert (
        text_rules.SHORT_MAX
        < text_rules.ADDRESS_MAX
        < text_rules.REASON_MAX
        < text_rules.NOTE_MAX
        < text_rules.BODY_MAX
    )
    assert text_rules.REASON_MAX < text_rules.COMPOSED_REASON_MAX


def test_text_rules_imports_nothing_from_app():
    """Imported by nearly everything; see tests/test_import_order.py."""
    tree = ast.parse((APP / "text_rules.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("app"), node.module
        elif isinstance(node, ast.Import):
            assert not any(a.name.startswith("app") for a in node.names)


# --- services refuse a lazy reason even when the page is bypassed ------------


def test_audit_service_refuses_a_lazy_required_reason():
    from app import audit_service

    with pytest.raises(ValueError):
        audit_service.record(
            None,  # never reached: the reason check runs first
            action=audit_service.GRADE_REOPENED,
            object_type="enrollments",
            object_id="00000000-0000-0000-0000-000000000000",
            reason="ok",
        )


LAZY = "ok"  # 2 characters: fails every rule at once
MULTILINE = "line one" + chr(10) + "line two"


def test_every_service_refuses_a_lazy_reason_before_touching_anything():
    """Each service validates first, so None stands in for the session and
    the call would crash with AttributeError if the check were not there.
    Driven for real, not by grepping the source (a substring test goes green
    whether or not the behaviour holds)."""
    from types import SimpleNamespace

    from app import audit_archive_service, award_service, parent_notice_service
    from app.attendance_service import reopen_month
    from app.enrollment_section_correction import CorrectionRefused, correct_section
    from app.enrollment_withdrawal import WithdrawalRefused, withdraw_enrollment
    from app.notice_rules import NoticeGroup

    with pytest.raises(WithdrawalRefused):
        withdraw_enrollment(
            None, enrollment_id=None, expected_version=1, user_id=None,
            reason=LAZY, authorized_by="Head", confirm_last_name="X",
        )
    with pytest.raises(CorrectionRefused):
        correct_section(
            None, enrollment=None, expected_section_id=None, new_section_id=None,
            user_id=None, reason=LAZY,
        )
    with pytest.raises(ReasonInvalid):
        parent_notice_service.set_override(
            None, enrollment_id=None, term_id=None, decision=NoticeGroup.RELEASE,
            reason=LAZY, user_id=None,
        )
    with pytest.raises(ReasonInvalid):
        reopen_month(None, SimpleNamespace(), None, LAZY)
    with pytest.raises(ReasonInvalid):
        award_service.set_award_override(None, None, None, None, None, LAZY)
    with pytest.raises(ReasonInvalid):
        audit_archive_service.delete_before(
            None, before=None, expected_count=0, actor_user_id=None, reason=LAZY
        )


def test_a_reason_with_digits_in_a_run_is_fine():
    # An LRN or an amount legitimately holds "0000".
    assert reason_problem("Wrong LRN 100000123456 corrected per PSA") is None
    assert reason_problem("Typo: entered 10000 instead of 100") is None
    assert reason_problem("No reason!!!!") is not None  # punctuation runs still fail


def test_audit_record_only_rewrites_a_required_reason():
    """Other actions keep exactly what the caller recorded (rule 8)."""
    from app import audit_service

    class _Session:
        def add(self, entry):
            self.entry = entry

    session = _Session()
    audit_service.record(
        session, action=audit_service.GRADE_CHANGED, object_type="x",
        object_id="00000000-0000-0000-0000-000000000000", reason=MULTILINE,
    )
    assert session.entry.reason == MULTILINE
    audit_service.record(
        session, action=audit_service.GRADE_REOPENED, object_type="x",
        object_id="00000000-0000-0000-0000-000000000000",
        reason="line one" + chr(10) + "  line two  here",
    )
    assert session.entry.reason == "line one line two here"


def test_a_withdrawal_reason_at_the_limit_still_fits_the_composed_cap():
    """enrollment_withdrawal wraps the typed reason in a 100-character name
    and authorizer; the result must pass reopen_month's composed limit."""
    from app.text_rules import SHORT_MAX

    wrapped = (
        f"Enrollment withdrawal of {'N' * SHORT_MAX} (authorized by {'A' * SHORT_MAX}): "
        + "x" * REASON_MAX
    )
    assert len(wrapped) <= COMPOSED_REASON_MAX


# --- no free-text box without a length limit ----------------------------------

# Login and password boxes are not stored, and a limit would only truncate a
# long passphrase. Every other box — typed-confirmation ones included — is
# capped.
UNLIMITED_OK = {
    ("auth.py", "Email"),
    ("auth.py", "Password"),
    ("auth.py", "New password"),
    ("auth.py", "Confirm new password"),
}


def _unlimited_text_boxes():
    found = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("text_input", "text_area")
            ):
                continue
            if any(kw.arg == "max_chars" for kw in node.keywords):
                continue
            label = (
                node.args[0].value
                if node.args and isinstance(node.args[0], ast.Constant)
                else "<computed label>"
            )
            if (path.name, label) in UNLIMITED_OK:
                continue
            found.append(f"{path.relative_to(APP)}:{node.lineno} {label!r}")
    return found


def test_every_text_box_has_a_length_limit():
    missing = _unlimited_text_boxes()
    assert not missing, (
        "These free-text boxes have no max_chars (use a limit from app/text_rules.py, "
        "or helpers.text_field, which sets one):\n  " + "\n  ".join(missing)
    )


def test_an_import_row_with_an_overlong_name_is_an_error():
    """The importers write what no form would accept unless they check too."""
    from app.import_pipeline import ValidationResult
    from app.import_specs import _flag_long_names

    result = ValidationResult()
    _flag_long_names(result, 7, [("Last Name", "A" * 101), ("First Name", "Ana"), ("Middle Name", "")])
    assert [(e.row_number, e.column) for e in result.errors] == [(7, "Last Name")]
