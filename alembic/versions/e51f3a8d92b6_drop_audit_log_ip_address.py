"""drop audit_log ip_address

Revision ID: e51f3a8d92b6
Revises: c9a2e6f14d73
Create Date: 2026-09-09 00:00:00.000000

On Streamlit Community Cloud every request arrives through a local
reverse proxy, so the address `audit_service` could ever capture was
never the visitor's real one — either the proxy's own loopback, or (via
the X-Forwarded-For fallback) the platform's own internal network. The
column had already been hidden from the audit log viewer for exactly
this reason; this drops it (and the data in it, which was noise, not
evidence) rather than keep a column that can only ever mislead a reader
of an archived export.

downgrade() re-adds the column, but the original values are gone for
good — this is a deliberate data-loss migration, not a reversible one.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e51f3a8d92b6"
down_revision: Union[str, Sequence[str], None] = "c9a2e6f14d73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("audit_logs", "ip_address")


def downgrade() -> None:
    """Downgrade schema. Re-adds an empty column — the dropped values
    are not recoverable."""
    op.add_column(
        "audit_logs",
        sa.Column("ip_address", postgresql.INET(), nullable=True),
    )
