# First deployment

One-time setup. For day-to-day updates see `operations.md`.

---

## What the host has to provide

Very little, which is the point of having removed LibreOffice:

- **Python 3.13** — see below, this one matters
- `pip install -r requirements.txt`
- One long-running process: `streamlit run streamlit_app.py`
- Outbound network access to Supabase

No system packages, no Docker requirement, no build step.

### Pin the Python version to 3.13

The repository declares 3.13 in `.python-version` and in
`.devcontainer/devcontainer.json`. **Streamlit Cloud does not read either
of those** — its Python version is set in the app's own settings, and has
to be changed there by hand:

> Manage app → Settings → Python version → **3.13** → save, then reboot.

Do it. On 2026-08-12 the host was running 3.14 while development was on
3.13, and an import-order change that passed the entire test suite
locally crashed the deployed app on startup. Nothing catches that class
of bug except having the versions agree.

Check what the host is actually on with **Manage app → Terminal**:

```bash
python --version
```

---

## Recommended: Streamlit Community Cloud

Free, and the least work. Measured footprint is ~176 MB with sessions
connected and ~1 MB per additional signed-in user, so 40 teachers fit
inside the 1 GB limit with room to spare.

1. Push this repository to GitHub. **Private is fine.**
2. At share.streamlit.io, create an app pointing at
   `streamlit_app.py` on the `main` branch.
3. Add the secrets below in the app's **Settings → Secrets**.
4. Deploy. Every later `git push` redeploys automatically.

### Secrets

Streamlit Cloud has no `.env` file; it injects secrets as environment
variables from its own settings panel. The names are exactly those in
`.env.example`:

```
DB_HOST
DB_PORT
DB_NAME
DB_USER
DB_PASSWORD
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
SESSION_TIMEOUT_MINUTES
```

**Never commit `.env`.** It is already in `.gitignore` — check that it
stays there before the first push.

`DB_HOST` must be the **Session Pooler** host
(`aws-0-…​.pooler.supabase.com`), not `db.<ref>.supabase.co`. The direct
host is IPv6-only and unreachable from most networks, including some
hosting providers.

### The two caveats you are accepting

- **The app sleeps** after inactivity. The first visitor each morning
  waits roughly 30 seconds. Everyone after that is unaffected.
- **Supabase's free tier pauses** a project after about a week idle,
  which school holidays will trigger. Unpausing is one click, but
  somebody has to know to do it — it is in the operations runbook.

---

## If you outgrow it

A small always-on host (Render, Railway, Fly, a VPS, or an Oracle Always
Free VM) runs the same command with the same environment variables. No
code changes. Move if the sleep delay becomes annoying or a term's
encoding load proves heavier than expected.

---

## After the first deploy

1. **Create the first Super Admin**, from your machine, once:

   ```bash
   ./.venv/Scripts/python.exe -m scripts.bootstrap_admin you@example.com "YOUR NAME"
   ```

2. Sign in and check **School Info**, **School Years & Terms** (terms
   open for encoding), and the **Academic Calendar** — generate it, then
   correct November and December by hand against §28's counts.
3. Import learners and enrollments (**Import from Excel**).
4. Create sections, assign advisers, seed **Section Offerings**, and make
   the **Teacher Assignments**.
5. Take a backup before letting teachers in, so there is a known-good
   starting point.

---

## Security checklist before go-live

- [ ] `.env` is not in the repository
- [ ] The service-role key is set as a secret, never in code
- [ ] HTTPS is on (Streamlit Cloud does this for you)
- [ ] The first Super Admin has changed the bootstrap password
- [ ] Every teacher account has exactly the roles it needs
- [ ] A backup has been taken and stored somewhere encrypted
