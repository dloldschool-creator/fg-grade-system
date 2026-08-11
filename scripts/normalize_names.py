"""One-time backfill: bring existing person names in line with the
uppercase-on-save rule in `app.naming.normalize_name`.

Covers all three places a person's name is stored:
  - learners (last/first/middle/extension name)
  - users (teachers, advisers, registrars — `full_name`)
  - the school head's name on the school record

New and edited records are normalized by the pages themselves, so this
only exists for rows created before that rule.

Run with `--apply` to write; without it the script only reports what would
change, which is the safer default given the conversion is lossy ("de la
Cruz" becomes "DE LA CRUZ" and the original casing is not recoverable).

    python -m scripts.normalize_names          # dry run
    python -m scripts.normalize_names --apply  # write

Run as a module (`python -m scripts...`), not as a bare script, so the
repo root is on sys.path for `from app.* import`.
"""

import sys

from app.database import SessionLocal
from app.models.learners import Learner
from app.models.organization import School
from app.models.rbac import User
from app.naming import normalize_name

# (model, label, fields)
TARGETS = [
    (Learner, "learner", ("last_name", "first_name", "middle_name", "extension_name")),
    (User, "user", ("full_name",)),
    (School, "school", ("school_head_name",)),
]


def main() -> int:
    apply_changes = "--apply" in sys.argv

    session = SessionLocal()
    try:
        changes = []
        for model, label, fields in TARGETS:
            for row in session.query(model).all():
                for field in fields:
                    current = getattr(row, field)
                    normalized = normalize_name(current)
                    if current != normalized:
                        changes.append((label, field, current, normalized))
                        if apply_changes:
                            setattr(row, field, normalized)

        if not changes:
            print("Nothing to do — every name is already normalized.")
            return 0

        print(f"{len(changes)} field(s) differ:")
        for label, field, before, after in changes:
            print(f"  {label:8s} {field:15s} {before!r:34s} -> {after!r}")

        if apply_changes:
            session.commit()
            print(f"\nApplied {len(changes)} change(s).")
        else:
            print("\nDry run — nothing written. Re-run with --apply to write.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
