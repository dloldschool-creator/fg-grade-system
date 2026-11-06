"""Supabase clients — Auth only, per CLAUDE.md (never used for data
queries; that's SQLAlchemy's job, see app/database.py)."""

import os

from dotenv import load_dotenv
from supabase import Client, ClientOptions, create_client

load_dotenv()


def get_anon_client() -> Client:
    """Publishable/anon key — sign-in, the remembered-session restore,
    password change and logout (app/auth.py).

    A fresh client per call, with **auto-refresh off**. The library's
    default starts a background timer whenever a client holds a session,
    which refreshes — and so spends — the refresh token about an hour
    later, then reschedules itself for as long as the process lives. Each
    of these clients is thrown away after one call, so that timer was a
    leaked thread per sign-in, and it spent the token the "Keep me signed
    in" cookie holds: the next refresh then presented a used token,
    Supabase's reuse detection revoked the session, and the 8 hours lasted
    about one. Nothing here needs a token kept fresh — the database is
    reached through SQLAlchemy, not these tokens.
    """
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_ANON_KEY"],
        options=ClientOptions(auto_refresh_token=False, persist_session=False),
    )


def get_admin_client() -> Client:
    """Service-role key — bypasses all access rules. Used only for
    auth.admin.* user-provisioning calls (app/user_provisioning.py). Never
    used for data queries."""
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
