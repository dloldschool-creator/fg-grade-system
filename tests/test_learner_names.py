"""Tests for the learner-name storage rule (uppercase, trimmed,
whitespace-collapsed) — see app.models.learners.normalize_name."""

import pytest

from app.naming import normalize_name


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Dela Cruz", "DELA CRUZ"),
        ("juan", "JUAN"),
        ("ALREADY UPPER", "ALREADY UPPER"),
        ("  padded  ", "PADDED"),
        ("double  spaced", "DOUBLE SPACED"),
        ("de la  Cruz  ", "DE LA CRUZ"),
        ("ñoño", "ÑOÑO"),  # non-ASCII uppercases correctly
    ],
)
def test_names_are_uppercased_and_whitespace_normalized(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
def test_blank_becomes_none_not_empty_string(raw):
    """Optional fields (middle/extension name) must end up NULL rather
    than '', and a required field of only whitespace has to fail the
    caller's not-empty check rather than saving blank."""
    assert normalize_name(raw) is None


def test_normalizing_twice_changes_nothing():
    """Re-running the backfill over already-normalized rows must be a
    no-op, so it's safe to run more than once."""
    once = normalize_name("  Dela   Cruz ")
    assert normalize_name(once) == once
