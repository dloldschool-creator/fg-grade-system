"""Parent/guardian contact (§78.1): validation, importer and wiring."""

import uuid
from types import SimpleNamespace

import pytest

from app.guardian_contact import clean_email, display_mobile, normalise_mobile, parse_consent
from app.import_pipeline import suggest_mapping
from app.import_specs import (
    LEARNER_CONTACT_IMPORT,
    LEARNER_IMPORT,
    commit_learner_contacts,
    validate_learner_contacts,
    validate_learners,
)


@pytest.mark.parametrize(
    "raw",
    [
        "09171234567",
        "0917 123 4567",
        "0917-123-4567",
        "+63 917 123 4567",
        "639171234567",
        "9171234567",  # Excel dropped the leading zero
        "9171234567.0",  # ...and read it back as a float
        9171234567,
    ],
)
def test_every_common_way_of_writing_a_mobile_normalises_the_same(raw):
    assert normalise_mobile(raw) == ("+639171234567", None)


@pytest.mark.parametrize("raw", ["0217123456", "0917123456", "091712345678", "abc", "+1 555 123 4567"])
def test_a_number_that_is_not_a_ph_mobile_is_rejected_not_guessed(raw):
    value, error = normalise_mobile(raw)
    assert value is None and error


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_is_not_an_error_and_is_none(raw):
    assert normalise_mobile(raw) == (None, None)
    assert clean_email(raw) == (None, None)
    assert parse_consent(raw) == (None, None)


def test_display_mobile_round_trips_to_the_dialled_form():
    assert display_mobile("+639171234567") == "09171234567"
    assert display_mobile(None) == ""
    assert display_mobile("garbage") == "garbage"


def test_email_is_lowercased_and_validated():
    assert clean_email("  Maria.Cruz@Gmail.COM ") == ("maria.cruz@gmail.com", None)
    for bad in ("maria", "maria@", "@gmail.com", "maria@gmail", "a b@c.com"):
        value, error = clean_email(bad)
        assert value is None and error


def test_consent_reads_yes_and_no_and_refuses_anything_else():
    assert parse_consent("Yes") == (True, None)
    assert parse_consent("n") == (False, None)
    value, error = parse_consent("maybe")
    assert value is None and error


@pytest.mark.parametrize("raw", ["N/A", "n/a", "NA", "none", "-", "—", "  -  "])
def test_placeholders_read_as_blank_not_as_errors(raw):
    assert normalise_mobile(raw) == (None, None)
    assert clean_email(raw) == (None, None)
    assert parse_consent(raw) == (None, None)


def test_contact_headers_are_auto_detected_and_all_optional():
    for field in ("guardian_name", "guardian_email", "guardian_mobile", "notices_consent"):
        column = next(c for c in LEARNER_IMPORT.columns if c.field == field)
        assert not column.required
    mapping = suggest_mapping(
        [
            "Parent/Guardian Name",
            "Parent Email",
            "Contact Number of Parent or Guardian",
            "Notice Consent",
        ],
        LEARNER_IMPORT,
    )
    assert mapping == {
        "guardian_name": "Parent/Guardian Name",
        "guardian_email": "Parent Email",
        "guardian_mobile": "Contact Number of Parent or Guardian",
        "notices_consent": "Notice Consent",
    }


@pytest.mark.parametrize("spec", [LEARNER_IMPORT, LEARNER_CONTACT_IMPORT])
def test_a_bare_email_or_mobile_header_is_never_taken_as_the_parents(spec):
    """A masterlist's plain "Email"/"Mobile" column is often the learner's
    own. Auto-mapping it would send the learner their own concern notice."""
    mapping = suggest_mapping(
        ["Email", "Email Address", "Mobile", "Mobile Number", "Contact Number", "Consent"], spec
    )
    assert not set(mapping) & {"guardian_email", "guardian_mobile", "notices_consent"}


class _EmptySession:
    """validate_learners only reads existing LRNs; none exist here."""

    def query(self, *_):
        return self

    def filter(self, *_):
        return self

    def all(self):
        return []


def _row(**extra):
    return {
        "__row__": 2,
        "last_name": "cruz",
        "first_name": "juan",
        "sex": "M",
        "birthdate": "2009-05-01",
        **extra,
    }


def test_a_valid_contact_flows_through_the_importer_normalised():
    result = validate_learners(
        _EmptySession(),
        [
            _row(
                guardian_name="maria cruz",
                guardian_email="Maria@Gmail.com",
                guardian_mobile="9171234567",
                notices_consent="yes",
            )
        ],
        {},
    )
    assert result.ok
    parsed = result.parsed[0]
    assert parsed["guardian_name"] == "MARIA CRUZ"
    assert parsed["guardian_email"] == "maria@gmail.com"
    assert parsed["guardian_mobile"] == "+639171234567"
    assert parsed["notices_consent"] is True


def test_a_row_without_contact_still_imports_with_no_consent():
    result = validate_learners(_EmptySession(), [_row()], {})
    assert result.ok
    parsed = result.parsed[0]
    assert parsed["guardian_email"] is None and parsed["guardian_mobile"] is None
    assert parsed["notices_consent"] is False


def test_a_bad_contact_cell_warns_but_still_creates_the_learner():
    """Contact is optional; an unreadable cell must not cost a valid learner."""
    result = validate_learners(
        _EmptySession(),
        [_row(guardian_email="nope", guardian_mobile="0917 / 0918", notices_consent="maybe")],
        {},
    )
    assert result.ok and len(result.parsed) == 1
    parsed = result.parsed[0]
    assert parsed["guardian_email"] is None and parsed["guardian_mobile"] is None
    assert parsed["notices_consent"] is False
    assert {w.column for w in result.warnings} == {
        "Parent/Guardian Email",
        "Parent/Guardian Mobile",
        "Notice Consent",
    }


# --- Contact update for existing learners (by LRN) -------------------------


class _LearnerSession:
    """Serves a fixed set of learners to any query, and collects adds."""

    def __init__(self, learners):
        self._learners = learners
        self.added = []

    def query(self, *_):
        return self

    def filter(self, *_):
        return self

    def all(self):
        return list(self._learners)

    def add(self, obj):
        self.added.append(obj)


def _learner(lrn, **contact):
    return SimpleNamespace(
        id=uuid.uuid4(),
        lrn=lrn,
        last_name="CRUZ",
        first_name="JUAN",
        guardian_name=contact.get("guardian_name"),
        guardian_email=contact.get("guardian_email"),
        guardian_mobile=contact.get("guardian_mobile"),
        notices_consent=contact.get("notices_consent", False),
        notices_consent_date=contact.get("notices_consent_date"),
    )


LRN_A, LRN_B = "107041140016", "107041140017"


def test_contact_update_matches_existing_learners_by_lrn():
    a = _learner(LRN_A)
    result = validate_learner_contacts(
        _LearnerSession([a]),
        [
            {"__row__": 2, "lrn": LRN_A, "guardian_mobile": "9171234567"},
            {"__row__": 3, "lrn": LRN_B, "guardian_mobile": "09171234567"},
            {"__row__": 4, "lrn": LRN_A, "guardian_mobile": "09171234567"},
        ],
        {},
    )
    assert [p["learner_id"] for p in result.parsed] == [a.id]
    messages = {e.row_number: e.message for e in result.errors}
    assert "no learner" in messages[3]
    assert "same LRN as row 2" in messages[4]


def test_an_adviser_can_only_update_learners_they_may_edit():
    mine, theirs = _learner(LRN_A), _learner(LRN_B)
    result = validate_learner_contacts(
        _LearnerSession([mine, theirs]),
        [
            {"__row__": 2, "lrn": LRN_A, "guardian_email": "a@b.com"},
            {"__row__": 3, "lrn": LRN_B, "guardian_email": "a@b.com"},
        ],
        {},
        adviser_user_id="adviser",
        editable_ids={mine.id},
    )
    assert [p["learner_id"] for p in result.parsed] == [mine.id]
    assert "not one of your learners" in result.errors[0].message


def test_a_blank_cell_leaves_the_stored_value_alone_and_changes_are_audited():
    learner = _learner(LRN_A, guardian_email="old@mail.com", guardian_name="MARIA CRUZ")
    session = _LearnerSession([learner])
    result = validate_learner_contacts(
        session,
        [{"__row__": 2, "lrn": LRN_A, "guardian_email": "", "guardian_mobile": "N/A",
          "guardian_name": "", "notices_consent": "yes", }],
        {},
    )
    assert result.ok
    assert commit_learner_contacts(session, result.parsed, user_id=uuid.uuid4()) == 1
    assert learner.guardian_email == "old@mail.com"
    assert learner.guardian_name == "MARIA CRUZ"
    assert learner.notices_consent is True and learner.notices_consent_date is not None
    (entry,) = session.added
    assert entry.action == "LEARNER_CONTACT_CHANGED"
    assert entry.previous_value == {"notices_consent": False}


def test_re_uploading_the_same_contact_changes_and_logs_nothing():
    learner = _learner(LRN_A, guardian_mobile="+639171234567")
    session = _LearnerSession([learner])
    result = validate_learner_contacts(
        session, [{"__row__": 2, "lrn": LRN_A, "guardian_mobile": "09171234567"}], {}
    )
    assert commit_learner_contacts(session, result.parsed, user_id=None) == 0
    assert session.added == []


def test_an_unreadable_value_in_a_contact_update_is_an_error():
    """Here the contact is the whole row, so there is nothing to save around it."""
    result = validate_learner_contacts(
        _LearnerSession([_learner(LRN_A)]),
        [{"__row__": 2, "lrn": LRN_A, "guardian_mobile": "12345"}],
        {},
    )
    assert not result.parsed and result.errors[0].column == "Parent/Guardian Mobile"


def test_the_contact_update_is_offered_on_the_import_page_and_the_masterlist():
    import inspect

    from app.admin_pages import learners
    from app.import_specs import SPECS

    assert SPECS[LEARNER_CONTACT_IMPORT.job_type] is LEARNER_CONTACT_IMPORT
    assert "_contact_upload_section(" in inspect.getsource(learners.render)


def test_the_contact_form_is_drawn_for_editable_learners_and_audited():
    import inspect

    from app.admin_pages import learners

    assert "_contact_form(session, learner, current_user)" in inspect.getsource(learners.render)
    source = inspect.getsource(learners._contact_form)
    assert "LEARNER_CONTACT_CHANGED" in source
    # Consent is dated in school time, never the UTC host's date.
    assert "SCHOOL_TZ" in source


def test_the_read_only_card_shows_no_contact_details():
    import inspect

    from app.admin_pages import learners

    source = inspect.getsource(learners._read_only_card)
    assert "guardian" not in source and "mobile" not in source
