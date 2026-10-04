"""Parent/guardian contact details (§78.1): cleaning and validation.

Dependency-free on purpose — no `app.models`, no Streamlit — so the
Masterlist page, the importer and the notice senders share one definition
of "a valid number" and importing it can never disturb import order.

Every function returns ``(value, error)``. A blank input is **not** an
error: contact details are optional, and a blank is stored as NULL, never
as an empty string (rule 2 in spirit — not-given is not the same as a
value).
"""

import re

# 254 is the longest address SMTP permits.
EMAIL_MAX_LENGTH = 254
_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")

# What people type into a contact cell to mean "nothing here". Read as
# blank rather than refused — SF1-derived files are full of them.
_PLACEHOLDERS = {"n/a", "na", "none", "nil", "null", "-", "--", "—", "–", "."}

# The fields this module governs, in form order.
CONTACT_FIELDS = ("guardian_name", "guardian_email", "guardian_mobile", "notices_consent")

_TRUE = {"y", "yes", "true", "1", "oo", "x", "✓"}
_FALSE = {"n", "no", "false", "0", "hindi"}


def _text(raw) -> str:
    value = str(raw if raw is not None else "").strip()
    return "" if value.lower() in _PLACEHOLDERS else value


def contact_values(learner) -> dict:
    """A learner's contact as a plain dict — what an audit entry compares.
    Takes the ORM object duck-typed, so this module stays model-free."""
    return {field: getattr(learner, field) for field in CONTACT_FIELDS}


CONSENT_NEEDS_CONTACT = (
    "consent needs a parent/guardian email or mobile number to send to"
)


def consent_without_contact(values: dict) -> bool:
    """True for a contact that would be consented with nowhere to send.

    Checked on the values a save *would leave*, not on what was typed: an
    update that blanks the only email of a consented learner fails it too.
    A consented learner with no address would otherwise reach the notice
    queue looking ready and fail only at send time.
    """
    return bool(values.get("notices_consent")) and not (
        values.get("guardian_email") or values.get("guardian_mobile")
    )


def apply_contact(learner, changes: dict, today) -> None:
    """Writes `changes` (any subset of CONTACT_FIELDS) onto `learner`.

    The one place the consent date is decided, shared by the Masterlist
    form and the contact import: dated `today` when consent turns on,
    cleared when it turns off, and left alone when it doesn't change — a
    re-saved form must not move the date consent was first recorded.
    `today` is the caller's, in school time; the host runs on UTC.
    """
    for field, value in changes.items():
        if field == "notices_consent":
            if value != learner.notices_consent:
                learner.notices_consent = value
                learner.notices_consent_date = today if value else None
        else:
            setattr(learner, field, value)


def clean_email(raw) -> tuple[str | None, str | None]:
    value = _text(raw).lower()
    if not value:
        return None, None
    if len(value) > EMAIL_MAX_LENGTH or not _EMAIL.fullmatch(value):
        return None, f"{str(raw).strip()!r} is not an email address"
    return value, None


def normalise_mobile(raw) -> tuple[str | None, str | None]:
    """A Philippine mobile number, stored as ``+639XXXXXXXXX``.

    Accepts what people actually type or paste: ``09171234567``,
    ``0917 123 4567``, ``+63 917 123 4567``, ``639171234567`` and
    ``9171234567``. That last one is what Excel leaves behind when it
    treats the cell as a number and drops the leading zero, and
    ``9171234567.0`` is the same thing read back as a float — both are
    undone rather than rejected, as ``parse_lrn`` does for an LRN.
    """
    value = _text(raw)
    if not value:
        return None, None
    if re.fullmatch(r"\d+\.0+", value):
        value = value.split(".")[0]
    digits = re.sub(r"[\s()\-.]", "", value)
    if digits.startswith("+"):
        digits = digits[1:]
    if not digits.isdigit():
        return None, f"{str(raw).strip()!r} is not a mobile number"
    if digits.startswith("63"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    if re.fullmatch(r"9\d{9}", digits):
        return f"+63{digits}", None
    return None, f"{str(raw).strip()!r} is not a Philippine mobile number (09XXXXXXXXX)"


def display_mobile(stored: str | None) -> str:
    """``+639171234567`` -> ``09171234567``, the way it is dialled and
    written on a letter. Anything that is not in the stored shape is
    returned unchanged rather than guessed at."""
    if stored and re.fullmatch(r"\+639\d{9}", stored):
        return "0" + stored[3:]
    return stored or ""


def parse_consent(raw) -> tuple[bool | None, str | None]:
    """``None`` means the cell was blank — "not stated", which an update
    must leave alone and a new learner stores as not consented."""
    value = _text(raw).lower()
    if not value:
        return None, None
    if value in _TRUE:
        return True, None
    if value in _FALSE:
        return False, None
    return None, f"{str(raw).strip()!r} is not Yes or No"
