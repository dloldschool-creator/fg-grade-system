"""The Parent Notices page, rendered through Streamlit's own runtime.

Nobody can view the page without an account holding the role, so this
runs the real `render()` against the live database as a stand-in Super
Admin, **pressing nothing** — the view path writes nothing, so the
database is untouched. `AppTest` has no browser (see the note in
`tests/test_add_form_reset.py`): this proves the script runs and builds
its tables for every section, not what they look like.
"""

import pytest
from streamlit.testing.v1 import AppTest

SCRIPT = """
import streamlit as st
from app.admin_pages import parent_notices as page
from app.auth import AuthUser

page.require_role = lambda *roles: AuthUser(
    id="00000000-0000-0000-0000-000000000001",
    supabase_auth_user_id="x",
    email="admin@example.com",
    full_name="TEST ADMIN",
    access_token="",
    refresh_token="",
    role_codes={"SUPER_ADMIN"},
)
page.render()
"""


def _run(at: AppTest) -> AppTest:
    at.run(timeout=120)
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.fixture
def app():
    return _run(AppTest.from_string(SCRIPT, default_timeout=120))


def test_the_page_renders_its_three_groups(app):
    labels = {m.label for m in app.metric}
    assert {"Release", "Concern", "Not ready"} <= labels


def _box(app, label):
    return next(s for s in app.selectbox if s.label == label)


def _grouped_or_empty(app) -> bool:
    return {"Release", "Concern", "Not ready"} <= {m.label for m in app.metric} or any(
        "No learners" in i.value for i in app.info
    )


def test_every_section_renders_in_the_first_term(app):
    """Term 1 is the one with real grades and attendance, so it exercises
    every group and every table on real rows."""
    for option in _box(app, "Section").options:
        _box(app, "Section").select(option)
        _run(app)
        assert _grouped_or_empty(app), option


def test_the_later_terms_render(app):
    for index in range(1, len(_box(app, "Term").options)):
        _box(app, "Term").select_index(index)
        _run(app)
        assert _grouped_or_empty(app)


HEAD_SCRIPT = SCRIPT.replace('role_codes={"SUPER_ADMIN"}', 'role_codes={"SCHOOL_HEAD"}')


def test_a_school_head_sees_the_groups_and_record_but_no_controls():
    at = _run(AppTest.from_string(HEAD_SCRIPT, default_timeout=120))
    assert {"Release", "Concern", "Not ready"} <= {m.label for m in at.metric}
    assert any(s.value == "Sent record" for s in at.subheader)
    assert not at.button, [b.label for b in at.button]
    assert not at.text_input


CONFIGURED_SCRIPT = (
    "import os\n"
    "os.environ['NOTICE_EMAIL_ADDRESS'] = 'school@example.com'\n"
    "os.environ['NOTICE_EMAIL_APP_PASSWORD'] = 'not-a-real-password'\n"
    + SCRIPT
)


def test_with_email_configured_the_panel_renders_for_every_section_and_sends_nothing():
    """Rendering never contacts the mail server; only the Send button
    would, and nothing here presses it."""
    import os

    at = _run(AppTest.from_string(CONFIGURED_SCRIPT, default_timeout=120))
    try:
        for option in _box(at, "Section").options:
            _box(at, "Section").select(option)
            _run(at)
            assert not any("isn't set up" in i.value for i in at.info)
    finally:
        os.environ.pop("NOTICE_EMAIL_ADDRESS", None)
        os.environ.pop("NOTICE_EMAIL_APP_PASSWORD", None)
