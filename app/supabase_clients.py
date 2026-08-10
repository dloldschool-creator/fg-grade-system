"""Supabase clients — Auth only, per CLAUDE.md (never used for data
queries; that's SQLAlchemy's job, see app/database.py)."""

import os

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()


def get_anon_client() -> Client:
    """Publishable/anon key — used only for sign_in_with_password at login."""
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])


def get_admin_client() -> Client:
    """Service-role key — bypasses all access rules. Used only for
    auth.admin.* user-provisioning calls (app/user_provisioning.py). Never
    used for data queries."""
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
