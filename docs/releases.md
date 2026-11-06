# Releases

Semantic versioning, adapted for a web app with no public API:

- **MAJOR** — a change to an official output's rules (grading policy, a form's
  layout). `1.0.0` is the first release used for real encoding.
- **MINOR** — a new page or capability.
- **PATCH** — a bug fix or wording change.

While below `1.0.0`, minor bumps may include rule changes.

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
| v0.3.0 | 90e6c80 | 2026-08-12 | Live on Streamlit Cloud; adds SF4 |
| v0.3.1 | 3911c3f | 2026-08-12 | Fix: crash on reload from `_helpers` importing models |
| v0.3.2 | 486c584 | 2026-08-13 | Fix: term-grade import, which had never worked |
| v0.4.0 | 41faa98 | 2026-08-20 | DO 017 unit-weighted averages and Subject Units page |
| v0.4.1 | 69fc94e | 2026-08-21 | Fix: Learner Masterlist scoped to the adviser's own learners |
| v0.5.0 | af88a6a | 2026-08-30 | Overview → Insights (all metrics, role-scoped views) |
| v0.6.0 | b6d306c | 2026-09-17 | Award policy options, certificates, Finalize All, SF9 exit status and duplex printing, irregular-learner substitutions |
| v0.7.0 | 4b940f5 | 2026-09-26 | Single "Save & submit grades" button; encoding closes after deadline |
| v0.8.0 | 36a7b5e | 2026-10-04 | Parent contacts and Parent Notices (§78) |
| v0.9.0 | e73a331 | 2026-10-17 | Enrollment correct/withdraw, no Prepare step, attendance reopen, reason rules, SF2 reprint flags |
| v0.10.0 | 324db42 | 2026-10-18 | Query-count performance pass, admission-record and bulk-upload templates |
| v0.11.0 | 8c23f79 | 2026-10-29 | Attendance P stored as a bit; database-space card |
| v0.11.1 | (the commit that adds `VERSION`) | 2026-11-06 | Attendance finalize hint, faculty guide and docs; release numbering begins |

Versions before `v0.11.1` are judgement calls assigned afterwards, not
numbers that were ever shown in the app.
