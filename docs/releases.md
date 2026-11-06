# Releases

Semantic versioning, adapted for a web app with no public API:

- **MAJOR** — a change to an official output's rules (grading policy, a form's
  layout). `1.0.0` is the first production release (live 2026-08-12).
- **MINOR** — a new page or capability.
- **PATCH** — a bug fix or wording change.

Before `1.0.0` (pre-production), minor bumps may include rule changes. DO 017 was counted as a minor bump because it went live while `term_grades` was empty, so no existing figure changed.

To cut a release: edit `VERSION`, commit, then tag that commit.
The sign-in page reads `VERSION`; the tag must match it.

```bash
git tag -a vX.Y.Z -m "summary"
git push origin vX.Y.Z
```

## Backfilled milestones (2026-11-06)

| Tag | Commit | Date | What it marks |
|---|---|---|---|
| v0.1.0 | 324551d | 2026-08-10 | Initial commit, Phases 1–7 (foundations, grading, awards core) |
| v0.2.0 | cbf7c8e | 2026-08-11 | Phases 8–14: attendance, SF2, SF9, academic record, term cards, import, audit; Python PDF renderer; deployment prep |
| v1.0.0 | 90e6c80 | 2026-08-12 | Live on Streamlit Cloud (production); adds SF4 |
| v1.0.1 | 3911c3f | 2026-08-12 | Fix: crash on reload from `_helpers` importing models |
| v1.0.2 | 486c584 | 2026-08-13 | Fix: term-grade import, which had never worked |
| v1.1.0 | 41faa98 | 2026-08-20 | DO 017 unit-weighted averages and Subject Units page |
| v1.1.1 | 69fc94e | 2026-08-21 | Fix: Learner Masterlist scoped to the adviser's own learners |
| v1.2.0 | af88a6a | 2026-08-30 | Overview → Insights (all metrics, role-scoped views) |
| v1.3.0 | b6d306c | 2026-09-17 | Award policy options, certificates, Finalize All, SF9 exit status and duplex printing, irregular-learner substitutions |
| v1.4.0 | 4b940f5 | 2026-09-26 | Single "Save & submit grades" button; encoding closes after deadline |
| v1.5.0 | 36a7b5e | 2026-10-04 | Parent contacts and Parent Notices (§78) |
| v1.6.0 | e73a331 | 2026-10-17 | Enrollment correct/withdraw, no Prepare step, attendance reopen, reason rules, SF2 reprint flags |
| v1.7.0 | 324db42 | 2026-10-18 | Query-count performance pass, admission-record and bulk-upload templates |
| v1.8.0 | 8c23f79 | 2026-10-29 | Attendance P stored as a bit; database-space card |
| v1.8.1 | (the commit that renumbers) | 2026-11-06 | Attendance finalize hint, faculty guide and docs; release numbering begins |

Versions before `v1.8.1` were assigned afterwards, based on the go-live
commit as `v1.0.0`; they were never shown in the app. An earlier set of
`v0.3.0`–`v0.11.1` tags was pushed and replaced the same day.

## Releases since numbering began

| Tag | Date | What changed |
|---|---|---|
| v1.9.0 | 2026-11-06 | "Keep me signed in on this device for 8 hours" at sign-in (off by default): a refresh or dropped connection no longer signs you out. Logout deletes the cookie and revokes the token on that device only. |
| v1.9.1 | 2026-11-06 | Fix: remembered sign-ins lasting ~1 hour (library background refresh spent the cookie token; also leaked a thread per sign-in). Network blips keep the cookie; password change keeps it current; school name escaped on sign-in page. |
| v1.9.2 | 2026-11-06 | Fix: "Keep me signed in" did nothing on Streamlit Cloud — its proxy keeps the cookie from `st.context.cookies`, so the browser now reports it through the component. |
