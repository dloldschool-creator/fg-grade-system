"""Re-applying a profile's Order must move print order and nothing else.

`section_subject_offerings.display_order` decides where a subject prints on
SF9, the term cards, Grade Summary and Export — and, once a year is
finalized, the position frozen into the permanent academic record. Seeding
only ever *creates* offerings (it skips subject/terms that already exist),
so "Re-apply profile order" is the only way to change that order after a
section has been seeded.

It is therefore a bulk write to the table §48 makes the source of truth for
what a learner is graded on, which is exactly the kind of thing that must
not quietly do more than it says. The decisions are unit-tested through the
pure `order_changes`; the writes are checked structurally, because
exercising the real function needs a session and the DB-backed tests here
run against the live Supabase instance.
"""

import ast
import pathlib
from types import SimpleNamespace

import pytest

from app.admin_pages.section_offerings import order_changes

PAGE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "app" / "admin_pages" / "section_offerings.py"
)


def offering(subject_id, display_order):
    return SimpleNamespace(subject_id=subject_id, display_order=display_order)


# --- What re-apply decides to touch ---------------------------------------


def test_it_moves_the_offerings_the_profile_lists():
    offerings = [offering("math", 0), offering("english", 0)]
    changes, untouched = order_changes(offerings, {"math": 20, "english": 10})

    assert [(o.subject_id, desired) for o, desired in changes] == [
        ("math", 20),
        ("english", 10),
    ]
    assert untouched == 0


def test_a_subject_the_profile_does_not_list_is_left_alone():
    """The bug this rule prevents: a manually added offering is on no
    profile, so a blind re-apply would reset it to the column default and
    send it to the top of every form."""
    manual = offering("research-elective", 15)
    changes, untouched = order_changes([manual], {"math": 20})

    assert changes == []
    assert untouched == 1
    assert manual.display_order == 15, "order_changes must not mutate"


def test_a_row_already_at_the_right_order_is_not_rewritten():
    """Otherwise a second click bumps `version` on every row (breaking rule
    9's optimistic concurrency for whoever else has the section open) and
    writes an audit entry recording no change."""
    changes, untouched = order_changes([offering("math", 20)], {"math": 20})

    assert changes == []
    assert untouched == 0


def test_every_term_of_a_multi_term_subject_moves_together():
    """A subject running three terms has three offering rows, and
    report_card sorts on the lowest of them. Re-apply keys on the subject,
    so all three land on the same number and the lowest is unambiguous."""
    rows = [offering("math", 0), offering("math", 0), offering("math", 0)]
    changes, _ = order_changes(rows, {"math": 30})

    assert [desired for _, desired in changes] == [30, 30, 30]


def test_the_combined_language_components_are_not_special_cased_here():
    """They need no exclusion: report_card prints the pair from its own
    loop before display_order is consulted at all, so an order set on a
    component is a harmless no-op rather than something to guard against.
    Pinned so nobody later 'fixes' it by filtering here."""
    components = [offering("eff-comm", 0), offering("mab-kom", 0)]
    changes, untouched = order_changes(components, {"eff-comm": 5, "mab-kom": 6})

    assert len(changes) == 2
    assert untouched == 0


# --- What the write actually touches --------------------------------------


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in {PAGE.name} — this test has gone stale")


def _attributes_assigned_on(func: ast.FunctionDef, variable: str) -> set[str]:
    """Attributes of `variable` written to anywhere in `func`, including
    augmented assignment (`x.version += 1`)."""
    written = set()
    for node in ast.walk(func):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == variable
            ):
                written.add(target.attr)
    return written


def test_reapply_writes_display_order_and_the_version_bump_only():
    written = _attributes_assigned_on(_function("_reapply_profile_order"), "offering")

    assert written == {"display_order", "version"}, (
        f"{PAGE.name} _reapply_profile_order() writes {sorted(written)} on an "
        f"offering. It is advertised as changing print order only: anything "
        f"else here silently edits what a learner is graded on. `version` is "
        f"required (rule 9); nothing else belongs."
    )


@pytest.mark.parametrize("call", ["audit_service.record", "try_commit"])
def test_reapply_audits_and_commits_safely(call):
    """Rule 8 (every sensitive change is audit-logged) and the house rule
    against a bare `session.commit()` where a constraint could fire."""
    source = ast.unparse(_function("_reapply_profile_order"))
    assert call in source, f"_reapply_profile_order() never calls {call}"
    assert "session.commit()" not in source
