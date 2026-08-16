"""Track when a password was last changed, and when a user last signed in

Two nullable columns on ``users``.

``password_changed_at`` is NULL for "still on the temporary password an
admin issued", which is what the new first-login gate checks. It is read
**once per login** and carried on the session object, never queried per
page render — ``require_role`` runs at the top of every page and
Streamlit re-runs the whole script on every click, so a query there would
add a round trip to every interaction in the app.

``last_login_at`` already existed on the model and in the table, and
nothing had ever written to it. The login path now does.

**Who gets backfilled is decided by whether they have ever signed in.**
The gate treats NULL as "must change", so backfilling nobody would lock
every existing account — including the only Super Admin — out of every
page on a live system, mid-term. Backfilling everybody would exempt the
two accounts issued hours before this migration, who are the exact people
it was built for.

Supabase settles it without a guess: ``auth.users.last_sign_in_at`` is
NULL until the account is actually used. Someone who has signed in is
assumed to have had the chance to set their own password and is marked
compliant; someone who never has is still holding the slip of paper the
admin handed them, so they stay NULL and meet the gate on first login.
No names and no dates are hardcoded — the rule reads the world.

If the ``auth`` schema is not reachable (a local database with no
Supabase behind it), every row is backfilled instead. That is the
fail-safe direction: the worst case is a gate that does not fire, not an
app nobody can sign in to.

Additive: one nullable column and an UPDATE over a handful of rows. No
rewrite, no lock worth the name, and the running app ignores columns it
does not know about — so this can go in before the code that reads it.

Revision ID: e2b6c1f4a733
Revises: d41f7a2c9e50
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

revision = "e2b6c1f4a733"
down_revision = "d41f7a2c9e50"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(), nullable=True))

    bind = op.get_bind()
    auth_users_reachable = bind.execute(
        sa.text("SELECT to_regclass('auth.users') IS NOT NULL")
    ).scalar()

    if auth_users_reachable:
        # Anyone who has actually signed in has had the chance to set
        # their own password; anyone who has not is still on the one an
        # admin issued, and stays NULL so the gate catches them.
        bind.execute(
            sa.text(
                """
                UPDATE users u
                   SET password_changed_at = now()
                  FROM auth.users a
                 WHERE a.id = u.supabase_auth_user_id
                   AND a.last_sign_in_at IS NOT NULL
                """
            )
        )
    else:
        # No Supabase behind this database. Fail safe: exempt everyone
        # rather than risk locking out an app nobody can then sign in to.
        bind.execute(sa.text("UPDATE users SET password_changed_at = now()"))


def downgrade() -> None:
    op.drop_column("users", "password_changed_at")
