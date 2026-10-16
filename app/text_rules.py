"""Rules for free text people type: how long it may be, and — for a
required reason — whether it is plausibly a reason at all.

**Dependency-free on purpose.** Nearly every page and service reaches this,
so it imports nothing from `app` (see tests/test_import_order.py).

**Two jobs, kept apart:**

1. *Length limits.* The database is the Supabase free tier, and the audit
   trail, remarks and comments are unbounded `String` columns, so the only
   thing standing between one pasted essay and the quota is these numbers.
   They are passed to Streamlit as `max_chars`, which stops typing in the
   browser; `validate_reason` repeats the reason limit for anything that
   reaches a service some other way (a script, a test, a stale tab).

2. *Reason quality.* A required reason exists so the audit log can answer
   "why" (§50, rule 8). `.strip()` alone accepts "x". This is a speed
   bump, **not a proof**: a regex cannot know a reason is true or even
   meaningful, and "this is my reason okay" passes. It stops the lazy
   escapes — "." , "asdf", "aaaaaa", "ok" — and nothing more. It is
   deliberately loose, because a strict rule just teaches people to type a
   longer fake one, and a registrar blocked mid-task by a false reject is
   a worse outcome than a weak reason getting through. No dictionary and no
   consonant rule: Filipino, names, "LRN", "NLS", "SF9" and "DepEd" would
   all be false rejects.
"""

import re

# --- Length limits (characters) ------------------------------------------
# One number per kind of field, so "how long may a remark be" has one answer.
SHORT_MAX = 100  # a name, label, code, room, position
ADDRESS_MAX = 200  # an address, a school name, a venue
REASON_MAX = 300  # why a change was made — audited, so kept tight
NOTE_MAX = 500  # a remark, comment, description or eligibility note
BODY_MAX = 1000  # certificate body wording — the one genuinely long field
# A reason the system *wraps* before storing — withdrawal prefixes the
# learner's name and who authorized it to the typed reason when it reopens
# an attendance month, and audit_service sees the wrapped text. Room for a
# 300-character reason, a 100-character name and a 100-character
# authorizer, with the fixed wording on top.
COMPOSED_REASON_MAX = 700

# --- Reason quality -------------------------------------------------------
REASON_MIN_CHARS = 10
REASON_MIN_WORDS = 2
REASON_MIN_LETTERS = 5

_WHITESPACE = re.compile(r"\s+")
# A letter or punctuation mark four or more times running: "aaaa", "....",
# "!!!!". Never a digit: an LRN, an amount or a code legitimately holds
# "0000" ("Wrong LRN 100000123456 corrected"), and refusing that would be
# the false reject this module exists to avoid. "hahahaha" is not one
# character repeated, so it passes — loose on purpose.
_RUN = re.compile(r"([^\W\d_]|[^\w\s])\1{3,}", re.IGNORECASE)
# A "word" is a run holding at least one letter, so "---" and "1234" are
# not words but "SF9", "T-L" and "Jr." are.
_WORD = re.compile(r"\S*[^\W\d_]\S*")
_LETTER = re.compile(r"[^\W\d_]")


class ReasonInvalid(ValueError):
    """The text is not acceptable as a required reason. The message is
    written for the person typing it."""


def clean_text(text) -> str:
    """Trimmed, with every run of whitespace — newlines from a text area
    included — collapsed to one space. A reason is a sentence, not a layout,
    and a stored newline breaks the one-line audit table."""
    return _WHITESPACE.sub(" ", text or "").strip()


def reason_problem(text, *, max_chars: int = REASON_MAX) -> str | None:
    """Why `text` is not an acceptable reason, or None if it is."""
    cleaned = clean_text(text)
    if not cleaned:
        return "A reason is required."
    if len(cleaned) > max_chars:
        return f"Keep the reason to {max_chars} characters or fewer (it is {len(cleaned)})."
    if len(cleaned) < REASON_MIN_CHARS:
        return f"Please give a reason of at least {REASON_MIN_CHARS} characters."
    if len(_LETTER.findall(cleaned)) < REASON_MIN_LETTERS:
        return "The reason needs real words, not just numbers or symbols."
    if len(_WORD.findall(cleaned)) < REASON_MIN_WORDS:
        return "Please describe the reason in a few words."
    if _RUN.search(cleaned):
        return "That doesn't look like a real reason — please re-type it."
    return None


def validate_reason(text, *, max_chars: int = REASON_MAX) -> str:
    """The cleaned reason, or `ReasonInvalid` saying what to fix.

    Call it where the reason enters the system — in the service that
    performs the change — so every route in is checked, and put the message
    straight on screen: it is already worded for the user.
    """
    problem = reason_problem(text, max_chars=max_chars)
    if problem:
        raise ReasonInvalid(problem)
    return clean_text(text)
