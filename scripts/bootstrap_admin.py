"""One-time CLI script to create the first Super Admin account.

Run once, as a module (not a bare script) so `app` resolves on sys.path:
    python -m scripts.bootstrap_admin <email> "<full name>"

Prints a temporary password to this terminal — note it down, it will not
be shown again. Log in with it and use the in-app "Change Password"
control to set a real one.

After this, every subsequent user should be added through the app's Users
admin screen (app/admin_pages/users.py) — both paths call the same
app.user_provisioning.provision_user, so this script is not a special
case, just the first invocation before any admin exists to click the
button.
"""

import sys

from app.user_provisioning import provision_user


def main() -> None:
    if len(sys.argv) != 3:
        print('Usage: python -m scripts.bootstrap_admin <email> "<full name>"')
        raise SystemExit(1)

    email, full_name = sys.argv[1], sys.argv[2]
    result = provision_user(email, full_name, ["SUPER_ADMIN"])

    if result.already_existed:
        print(f"{email} already had a Supabase Auth account — reset its password and granted SUPER_ADMIN.")
    else:
        print(f"Created {email} and granted SUPER_ADMIN.")
    print(f"Temporary password: {result.temporary_password}")
    print("Done.")


if __name__ == "__main__":
    main()
