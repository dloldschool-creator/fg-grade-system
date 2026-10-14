"""In-app quick guide.

Deliberately not a feature tour. Every item here is something that has a
**non-obvious rule behind it** — a place where doing the natural thing
produces a wrong record rather than an error message. Anything the screen
already makes obvious is left out; the point is to be short enough to be
read.

The text lives in `app/help_content.py`; this page only lays it out and
searches it. Entries are grouped by topic within a role, and the search
box at the top looks through every role at once.
"""

import re

import streamlit as st

from app.admin_pages._helpers import render_flashes
from app.auth import get_current_user
from app.help_content import BY_ROLE, ROLE_LABELS, UNIVERSAL
from app.live_search import live_search, search_words


# There was a NOTES list here, rendered under its own heading at the foot
# of the page. It held one question — why a learner's grades won't
# finalize — which is an adviser's question, so it lives in the adviser's
# own section now. A separate heading for one item made the reader look in
# two places for the same kind of answer.


def grouped_by_topic(items) -> list[tuple[str, list]]:
    """`[(topic, [(title, body), ...]), ...]`, topics in the order they
    first appear and entries keeping their order within a topic."""
    groups: dict[str, list] = {}
    for topic, title, body in items:
        groups.setdefault(topic, []).append((title, body))
    return list(groups.items())


def search_entries(query: str, roles: list[str]) -> list[tuple[str, str, str, str]]:
    """`[(where, topic, title, body), ...]` for every entry matching
    **all** the words of `query`, in any order — typing more only narrows.

    Looks through the topic, title, body and the role's label
    (`ROLE_LABELS`), so "attendance adviser" finds the adviser's attendance
    entries. Not the role's whole heading: the adviser's reads
    "...teachers, attendance, cards and awards", which would make every
    adviser entry match "attendance". Each word must *start* a word of the
    text, so "transfer" finds "transferred" but a short word like "in"
    doesn't match inside "encoding" and quietly narrow nothing. Everyone's
    entries come first, then the reader's own role(s), then the rest, so
    the likeliest answer is at the top; each result says which role it is
    written for, because an adviser's rule may be found by a teacher.
    """
    words = search_words(query)
    if not words:
        return []
    order = [("Everyone", UNIVERSAL)]
    order += [(ROLE_LABELS[code], BY_ROLE[code][1]) for code in BY_ROLE if code in roles]
    order += [(ROLE_LABELS[code], BY_ROLE[code][1]) for code in BY_ROLE if code not in roles]
    patterns = [re.compile(r"(?<!\w)" + re.escape(word)) for word in words]
    found = []
    for where, items in order:
        for topic, title, body in items:
            haystack = f"{where} {topic} {title} {body}".casefold()
            if all(pattern.search(haystack) for pattern in patterns):
                found.append((where, topic, title, body))
    return found


def _render_items(items) -> None:
    """One role's entries, under a label per topic — or none when the whole
    list is one topic, where the label would only repeat the heading above.
    A coloured label rather than a Markdown header: headers get an anchor
    id from their text, and topics repeat across roles."""
    groups = grouped_by_topic(items)
    for topic, entries in groups:
        if len(groups) > 1:
            st.markdown(f":blue[**{topic}**]")
        for title, body in entries:
            st.markdown(f"**{title}**")
            st.markdown(body)
            st.write("")


def _render_results(results) -> None:
    for where, topic, title, body in results:
        st.caption(f"{where} · {topic}")
        st.markdown(f"**{title}**")
        st.markdown(body)
        st.divider()


@st.fragment
def _guide(roles: list[str]) -> None:
    """The search box and everything under it. A fragment, so typing
    reruns only this and not the page header; the guide itself reads no
    database."""
    query = live_search(
        "Search the guide",
        key="quick_guide_search",
        placeholder="Type to filter — e.g. attendance, transfer, DRAFT, reopen",
    )

    if query:
        results = search_entries(query, roles)
        if not results:
            st.info("Nothing in the guide matches that. Try fewer or different words, or ask the admin.")
            return
        st.caption(f"{len(results)} entry(s) match — every word you typed is in each one.")
        _render_results(results)
        return

    st.subheader("Applies to everyone")
    _render_items(UNIVERSAL)

    mine = [code for code in BY_ROLE if code in roles]
    others = [code for code in BY_ROLE if code not in mine]

    if mine:
        st.divider()
        st.subheader("Your role")
        for code in mine:
            heading, items = BY_ROLE[code]
            with st.expander(heading, expanded=len(mine) == 1):
                _render_items(items)

    if others:
        st.divider()
        st.subheader("Other roles" if mine else "By role")
        st.caption("Shown so you know what to expect from colleagues' screens.")
        for code in others:
            heading, items = BY_ROLE[code]
            with st.expander(heading):
                _render_items(items)


def render() -> None:
    current_user = get_current_user()
    st.title("Quick Guide")
    st.caption(
        "Think of this as a quick guide sheet for the parts of the pages that "
        "aren't obvious. Everything else is pretty self-explanatory. Let "
        "the admin know if you get stuck on anything! -DL"
    )
    render_flashes()

    _guide(sorted(current_user.role_codes) if current_user else [])
