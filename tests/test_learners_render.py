"""The Learner Masterlist, rendered through Streamlit's own runtime.

Added after the live page went down on 2026-10-04: the parent-contact
form, drawn once per learner, gave every learner's widgets the same keys,
and Streamlit refused the second one with StreamlitDuplicateElementKey.
Every unit test passed, because none of them drew the page with more than
one learner on it. This one does: the real `render()` against the live
database, as a stand-in Super Admin (50 learners listed), pressing nothing.
"""

from streamlit.testing.v1 import AppTest

SCRIPT = """
import streamlit as st
from app.admin_pages import learners as page
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


def test_the_masterlist_renders_with_many_learners_on_it():
    at = AppTest.from_string(SCRIPT, default_timeout=120)
    at.run(timeout=120)
    assert not at.exception, [e.value for e in at.exception]
    contact_boxes = [w for w in at.text_input if w.label == "Parent/guardian name"]
    assert len(contact_boxes) > 1, "expected a contact form for more than one learner"
    keys = [w.key for w in at.text_input] + [w.key for w in at.checkbox]
    assert len(keys) == len(set(keys)), "two widgets share a key"


def test_the_shs_admission_form_is_hidden_for_grade_12_learners():
    """The SHS admission record is about entry into Grade 11, so a learner
    whose newest enrollment is in Grade 12 gets no such form on their card.
    Checked on the rendered page: every learner listed has a contact form,
    and exactly the non-Grade-12 ones also have the admission form."""
    import uuid

    from app.admin_pages.learners import _grade_12_learner_ids
    from app.database import SessionLocal

    at = AppTest.from_string(SCRIPT, default_timeout=120)
    at.run(timeout=120)
    assert not at.exception, [e.value for e in at.exception]

    listed = {
        uuid.UUID(w.key.split("contact_")[1].split(".")[0])
        for w in at.text_input
        if w.label == "Parent/guardian name"
    }
    with_form = {
        uuid.UUID(w.key.split("shs_date_")[1])
        for w in at.date_input
        if w.key and w.key.startswith("shs_date_")
    }
    session = SessionLocal()
    try:
        grade_12 = _grade_12_learner_ids(session, listed)
    finally:
        session.rollback()
        session.close()

    assert with_form == listed - grade_12
    assert not (with_form & grade_12)
    if not grade_12:
        import pytest

        pytest.skip("no Grade 12 learner among the listed learners")
