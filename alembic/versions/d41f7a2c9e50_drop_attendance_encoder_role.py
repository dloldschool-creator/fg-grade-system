"""Remove the ATTENDANCE_ENCODER role

§3E defines an Attendance Encoder, but the school's class advisers encode
their own section's attendance and always have. The role was seeded so an
administrator *could* grant it, and then no screens were ever built for
it — `EDITING_ROLES` in ``app/auth.py`` never included it, and no page
lists it in its `require_role`. Granting it on its own produced an
account that could sign in and reach nothing, which the quick guide had
to carry a warning about.

So it existed only as a dead entry in every role picker, including the
new bulk-add column. Removed at the school's request.

**This deletes `user_roles` grants of the role as well**, which it has to
— the foreign key is ON DELETE RESTRICT — and which is safe in a way
worth stating: the role conferred no page and no editing right, so
revoking it cannot narrow what anybody could actually do. It is expected
to delete zero rows in practice; nobody was ever given it.

Data only. No table is altered, nothing is locked beyond the statement,
and the app copes with the row being present or absent either way (it
reads the `roles` table to build its pickers), so this can run before or
after the code deploy without an in-between state that misbehaves.

Revision ID: d41f7a2c9e50
Revises: b7c31a9d40e2
Create Date: 2026-08-16
"""

from alembic import op

revision = "d41f7a2c9e50"
down_revision = "b7c31a9d40e2"
branch_labels = None
depends_on = None

CODE = "ATTENDANCE_ENCODER"


def upgrade() -> None:
    # Both tables that reference roles, cleared first. Every foreign key
    # in this schema is ON DELETE RESTRICT, so a single stray row in
    # either would abort the migration.
    for table in ("user_roles", "role_permissions"):
        op.execute(
            f"""
            DELETE FROM {table}
             WHERE role_id IN (SELECT id FROM roles WHERE code = '{CODE}')
            """
        )
    op.execute(f"DELETE FROM roles WHERE code = '{CODE}'")


def downgrade() -> None:
    # Restores the role itself. The grants cannot come back — which ones
    # existed was not recorded anywhere, and inventing them would hand
    # out access nobody asked for. Since the role grants nothing, a
    # restored row with no grants is the same state it was in before.
    op.execute(
        f"""
        INSERT INTO roles (id, code, name)
        VALUES (gen_random_uuid(), '{CODE}', 'Attendance Encoder')
        ON CONFLICT (code) DO NOTHING
        """
    )
