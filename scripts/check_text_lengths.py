"""Read-only: the longest stored value in every text column that is longer
than the data-entry limits in app/text_rules.py.

Run before deploying a change to those limits. A form prefilled from the
database hands its value to Streamlit, which cuts it to `max_chars` — so a
stored value longer than its box's limit is silently shortened the next time
anyone saves that form. Prints nothing alarming when every value fits.

    python scripts/check_text_lengths.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import String, Text, func, select  # noqa: E402

import app.models  # noqa: E402,F401
from app.database import SessionLocal  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.text_rules import BODY_MAX, NOTE_MAX, SHORT_MAX  # noqa: E402

# The only column allowed to run past NOTE_MAX.
LONG_COLUMNS = {"certificate_body_template": BODY_MAX}
# Written by the system, not typed into a capped box.
SYSTEM_COLUMNS = {"user_agent", "previous_value", "new_value"}


def main() -> int:
    session = SessionLocal()
    over = 0
    try:
        for table in Base.metadata.sorted_tables:
            for column in table.columns:
                # Exact types: Enum subclasses String but has no length().
                if type(column.type) not in (String, Text) or column.name in SYSTEM_COLUMNS:
                    continue
                longest = session.execute(select(func.max(func.length(column)))).scalar()
                if not longest or longest <= SHORT_MAX:
                    continue
                cap = LONG_COLUMNS.get(column.name, NOTE_MAX)
                flag = "OVER" if longest > cap else "ok"
                over += longest > cap
                print(f"{flag:4} {table.name}.{column.name}: longest {longest} (box limit {cap})")
    finally:
        session.close()
    print(f"\n{over} column(s) hold a value longer than the box that edits it.")
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main())
