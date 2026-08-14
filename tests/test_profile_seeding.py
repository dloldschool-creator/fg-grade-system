"""Seeding a section's offerings must carry the profile's own fields over.

The bug this exists for: "Seed offerings from profile" built each
`SectionSubjectOffering` without passing `display_order`, so every seeded
offering took the column default of 0. The Order you set on Subject
Profiles therefore sorted the profile editor and nothing else.

Nothing failed. `SectionSubjectOffering.display_order` is what actually
orders the printed forms — `report_card.build_learning_area_rows` sorts on
it (taking the lowest across a subject's terms), as do Grade Summary,
Section Offerings and Teacher Assignments. With every value tied at 0 the
sort silently collapsed to its `official_name` tiebreak, so SF9 and the
term cards printed alphabetically and looked perfectly reasonable while
ignoring the order the registrar had entered.

That is plausible-wrong-output rather than an error, so it is checked
structurally: a forwarding bug is visible in the AST, and this needs no
database (the seed path lives inside an `st.form` on a Streamlit page and
the other DB-backed tests run against the live Supabase instance).

Note the deliberate choice *not* to refactor the constructor call into
`SectionSubjectOffering(**fields)`. tests/test_model_kwargs.py checks every
model constructor keyword against the real columns by walking the AST, and
it can only see keywords written out literally — `**fields` would be
skipped, quietly dropping that guard from this call.
"""

import ast
import pathlib

import pytest

PAGE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "app" / "admin_pages" / "section_offerings.py"
)

SEED_FUNCTION = "_seed_from_profile"
MODEL = "SectionSubjectOffering"

# Fields the profile entry carries that the seeded offering must inherit,
# mapped to the attribute on `SubjectProfileSubject` they come from. The
# three term flags are not here: they decide *how many* offerings get
# created, not what goes on one.
FORWARDED = {"display_order": "display_order"}


def _seed_constructor_call():
    """The `SectionSubjectOffering(...)` built by the seed-from-profile path."""
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == SEED_FUNCTION:
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == MODEL
                ):
                    return inner
    return None


@pytest.mark.parametrize("field", sorted(FORWARDED))
def test_the_seeded_offering_inherits_the_profile_field(field):
    call = _seed_constructor_call()
    keywords = {k.arg: k.value for k in call.keywords if k.arg}

    assert field in keywords, (
        f"{PAGE.name}:{call.lineno} {MODEL}(...) does not pass {field}=, so every "
        f"seeded offering takes the column default and the value set on Subject "
        f"Profiles never reaches the printed forms."
    )

    value = keywords[field]
    source = FORWARDED[field]
    assert isinstance(value, ast.Attribute) and value.attr == source, (
        f"{PAGE.name}:{call.lineno} {MODEL}({field}=...) must read the profile "
        f"entry's own `.{source}`, not a constant: a hardcoded value ties every "
        f"subject together exactly as the missing keyword did."
    )


def test_the_check_can_actually_find_what_it_inspects():
    """Guards the guard: if the function or the call were renamed, every
    assertion above would be made against None and the failure would be an
    AttributeError rather than the explanation it is meant to give."""
    call = _seed_constructor_call()
    assert call is not None, (
        f"no {MODEL}(...) call found inside {SEED_FUNCTION}() — this test has "
        f"gone stale and is no longer checking anything"
    )
    # Sanity that it is the seeding call and not some other construction:
    # seeding is the only place that resolves a category off the subject.
    assert "subject_category_id" in {k.arg for k in call.keywords}
