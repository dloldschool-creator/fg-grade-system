"""A search box that searches as you type.

`st.text_input` only reports its value when the user presses Enter or
leaves the box, so a search built on it cannot narrow while you type.
This is a small `st.components.v2` component — no third-party package, so
`requirements.txt` alone still deploys the app — that sends the box's
text to Python once the typing *pauses* (`debounce_ms`, 300 ms by
default) and immediately on Enter. Typing "pineda anne" at a normal speed
therefore costs one to three reruns, not eleven.

Each report reruns the script, so the caller decides what that costs:
put the box and the results it drives in an `@st.fragment`, so a
keystroke reruns the list and not the whole page. Both pages that use it
do.

`search_words` is the other half: a query is split into words and *every*
word must match somewhere, in any order. Names are stored "PINEDA, ANNE",
so searching for the phrase "pineda anne" used to find nothing.

Deliberately dependency-free (only Streamlit): it is imported by pages at
load time, and a module like that must never reach `app.models`
(see CLAUDE.md, "The deployed host runs a different Python").
"""

import streamlit as st
import streamlit.components.v2 as components

_HTML = """
<label class="ls-wrap">
  <span class="ls-label"></span>
  <input class="ls-input" type="search" autocomplete="off" spellcheck="false">
</label>
"""

_CSS = """
.ls-wrap { display: flex; flex-direction: column; gap: 0.25rem; font-family: var(--st-font, inherit); }
.ls-label { font-size: 0.875rem; color: var(--st-text-color, inherit); }
.ls-label:empty { display: none; }
.ls-input {
  box-sizing: border-box; width: 100%; height: 2.5rem; padding: 0 0.75rem;
  font: inherit; font-size: 1rem; color: var(--st-text-color, inherit);
  background: var(--st-secondary-background-color, #f0f2f6);
  border: 1px solid transparent; border-radius: var(--st-base-radius, 0.5rem); outline: none;
}
.ls-input:focus { border-color: var(--st-primary-color, #ff4b4b); }
"""

# Re-invoked by Streamlit on every rerun with fresh `data`, so the
# listeners are attached once (guarded by a data attribute) and always
# call the *latest* setStateValue, kept on the element.
_JS = """
export default function(component) {
  const { data, setStateValue, parentElement } = component;
  const input = parentElement.querySelector('.ls-input');
  parentElement.querySelector('.ls-label').textContent = data.label || '';
  input.placeholder = data.placeholder || '';
  input._send = setStateValue;
  if (!input.dataset.ready) {
    input.dataset.ready = '1';
    input.value = data.value || '';
    const send = () => {
      clearTimeout(input._timer);
      if (input.value !== input._sent) {
        input._sent = input.value;
        input._send('query', input.value);
      }
    };
    input._sent = input.value;
    input.addEventListener('input', () => {
      clearTimeout(input._timer);
      input._timer = setTimeout(send, data.debounce || 300);
    });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });
  }
  return () => clearTimeout(input._timer);
}
"""

_NAME = "live_search"
_component = None


def _mount():
    """The component's mount command, registered with the *running*
    Streamlit runtime.

    Not registered at import: `components.component()` registers into
    whatever registry exists at that moment, and when no runtime is
    running yet — a page module imported by a test, or any import that
    happens before Streamlit starts — that is a throwaway one, and the
    first mount fails with "Component 'live_search' is not registered".
    `tests/test_learners_render.py` caught exactly that. Checking the live
    registry on each call costs a dict lookup."""
    global _component
    if _component is None or components.get_bidi_component_manager().get(_NAME) is None:
        _component = components.component(_NAME, html=_HTML, css=_CSS, js=_JS)
    return _component


def live_search(label: str, *, key: str, placeholder: str = "", debounce_ms: int = 300) -> str:
    """The box's current text, stripped. Empty until something is typed."""
    remembered = f"{key}__last"
    result = _mount()(
        key=key,
        data={
            "label": label,
            "placeholder": placeholder,
            "debounce": debounce_ms,
            # Refills the box if the component is mounted afresh (leaving
            # the page and coming back) without losing what was typed.
            "value": st.session_state.get(remembered, ""),
        },
        default={"query": ""},
        on_query_change=lambda: None,
    )
    query = (getattr(result, "query", None) or "").strip()
    st.session_state[remembered] = query
    return query


def search_words(query: str) -> list[str]:
    """The words of a query, lower-cased, commas ignored. Every word must
    match for a row to be shown, so typing more only ever narrows."""
    return [word for word in query.replace(",", " ").casefold().split() if word]


def like_pattern(word: str) -> str:
    """`%word%` for ILIKE with `escape="\\\\"`, the word's own `%`, `_` and
    backslash taken literally — typed "_" must not match everything."""
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
