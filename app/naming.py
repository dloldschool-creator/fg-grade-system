"""How person names are stored, in one place.

Learner names, user (teacher/adviser) names and the school head's name all
follow the same rule so the masterlist, every DepEd form and every
on-screen list read consistently regardless of how each person typed them
in.
"""


def normalize_name(value: str | None) -> str | None:
    """UPPERCASE, trimmed, with internal runs of whitespace collapsed.

    DepEd forms (SF2, SF9, SF10) and the award certificates all print
    names in uppercase, so normalizing on the way in avoids the same
    person appearing three different ways across three screens.

    Blank becomes None, so an optional field (a middle or extension name)
    ends up NULL rather than an empty string. Callers must still validate
    that *required* names are non-empty **after** normalizing — "   "
    comes back as None.

    Deliberately lossy: "de la Cruz" is stored "DE LA CRUZ" and the
    original casing is not recoverable.
    """
    if value is None:
        return None
    cleaned = " ".join(value.split()).upper()
    return cleaned or None
