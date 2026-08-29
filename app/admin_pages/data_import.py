import pandas as pd
import streamlit as st

from app import audit_service
from app.admin_pages._helpers import (
    clear_text_fields,
    flash,
    generation_key,
    get_session,
    render_flashes,
    try_commit,
)
from app.auth import require_role
from app.display_time import format_time
from app.import_pipeline import (
    apply_mapping,
    create_job,
    missing_required,
    read_table,
    record_confirmation,
    record_validation,
    suggest_mapping,
)
from app.import_specs import SPECS
from app.models.admin import ImportJob
from app.models.organization import SchoolYear

def _upload_form(spec) -> str:
    """Names the uploader's generation, per job type.

    Per job type rather than page-wide so confirming a learner import does
    not also drop a half-filled term-grades file sitting in another tab.
    """
    return f"import_upload_{spec.job_type}"


PREVIEW_ROWS = 15
MAX_ERRORS_SHOWN = 50
NOT_MAPPED = "— not mapped —"


def _recent_jobs(session) -> None:
    """§51 step 7: every import leaves a record, so a bad migration can be
    traced back to who ran it and what it contained."""
    jobs = session.query(ImportJob).order_by(ImportJob.created_at.desc()).limit(10).all()
    if not jobs:
        st.caption("No imports have been run yet.")
        return
    st.table(
        [
            {
                "When": format_time(job.created_at),
                "Type": job.job_type.value,
                "File": job.file_path,
                "Status": job.status.value,
                "Rows": job.row_count,
                "Imported": job.imported_count if job.imported_count is not None else "—",
                "Errors": len(job.validation_errors) if job.validation_errors else 0,
            }
            for job in jobs
        ]
    )


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR")
    st.title("Import from Excel")
    st.caption(
        "Nothing is imported silently. Your file is checked and previewed first, "
        "and only saved when you confirm."
    )
    render_flashes()

    spec_choice = st.selectbox(
        "What are you importing?",
        options=list(SPECS),
        format_func=lambda v: SPECS[v].label,
    )
    spec = SPECS[spec_choice]
    st.caption(spec.description)

    with st.expander("Expected columns"):
        st.table(
            [
                {
                    "Column": column.label,
                    "Required": "yes" if column.required else "optional",
                    "Also accepts": ", ".join(column.aliases) or "—",
                }
                for column in spec.columns
            ]
        )

    school_year_id = None
    if spec.needs_school_year:
        with get_session() as session:
            school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if school_years:
            sy_by_id = {sy.id: sy for sy in school_years}
            school_year_id = st.selectbox(
                "School year this file belongs to",
                options=[sy.id for sy in school_years],
                format_func=lambda v: sy_by_id[v].name,
                help=spec.school_year_help or None,
                key=f"import_sy_{spec.job_type}",
            )
        else:
            st.warning("No school years yet — create one before importing.")

    uploaded = st.file_uploader(
        # CSV is still accepted so an older file already saved that way still
        # uploads, but it is never offered: Excel destroys a 12-digit LRN on
        # CSV export, and offering the format is what invites the mistake.
        "Excel file (.xlsx)", type=["xlsx", "xlsm", "csv"],
        # Per job type, and generation-carrying: a confirmed import must drop
        # its file, or the rerun validates the same rows against the ones it
        # just wrote — for learners that reads as "duplicate LRN" on every
        # row of a successful import (see generation_key).
        key=generation_key(_upload_form(spec), f"upload_{spec.job_type}"),
    )
    if uploaded is None:
        st.divider()
        st.subheader("Recent imports")
        with get_session() as session:
            _recent_jobs(session)
        return

    # Re-read on each rerun rather than stashing rows in session_state —
    # Streamlit keeps the uploaded file across reruns, and re-reading
    # avoids showing a preview that no longer matches the file.
    headers, rows = read_table(uploaded.getvalue(), uploaded.name)
    if not rows:
        st.error("No data rows found — is the first row the header?")
        return

    st.success(f"Read **{len(rows)}** row(s) and {len(headers)} column(s) from `{uploaded.name}`.")

    st.divider()
    st.subheader("Map columns")
    st.caption(
        "Pre-filled from the file's own headers where they're recognisable. "
        "Check them before validating."
    )
    suggested = suggest_mapping(headers, spec)
    mapping: dict[str, str] = {}
    columns = st.columns(2)
    for index, column in enumerate(spec.columns):
        with columns[index % 2]:
            options = [NOT_MAPPED] + headers
            default = suggested.get(column.field, NOT_MAPPED)
            chosen = st.selectbox(
                f"{column.label}{'' if column.required else '  (optional)'}",
                options=options,
                index=options.index(default) if default in options else 0,
                key=f"map_{spec.job_type}_{column.field}",
            )
            if chosen != NOT_MAPPED:
                mapping[column.field] = chosen

    absent = missing_required(mapping, spec)
    if absent:
        st.error(f"Map these required column(s) first: {', '.join(absent)}")
        return

    st.divider()
    st.subheader("Preview")
    mapped_rows = apply_mapping(rows, mapping)
    st.dataframe(
        pd.DataFrame([{k: v for k, v in r.items() if k != "__row__"} for r in mapped_rows[:PREVIEW_ROWS]]),
        hide_index=True,
        width="stretch",
    )
    if len(mapped_rows) > PREVIEW_ROWS:
        st.caption(f"Showing the first {PREVIEW_ROWS} of {len(mapped_rows)} rows.")

    st.divider()
    st.subheader("Validate")
    if st.button("Validate file", type="primary"):
        st.session_state[f"validated_{spec.job_type}"] = True

    if not st.session_state.get(f"validated_{spec.job_type}"):
        return

    with get_session() as session:
        extra = {"school_year_id": school_year_id} if spec.needs_school_year else {}
        result = spec.validate(session, mapped_rows, mapping, **extra)

        col1, col2, col3 = st.columns(3)
        col1.metric("Rows read", len(mapped_rows))
        col2.metric("Valid", len(result.parsed))
        col3.metric("Errors", len(result.errors))

        if result.errors:
            st.error(
                f"{len(result.errors)} problem(s) found. Nothing has been imported — fix "
                "the file and upload it again."
            )
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Row": e.row_number, "Column": e.column or "", "Problem": e.message}
                        for e in result.errors[:MAX_ERRORS_SHOWN]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            if len(result.errors) > MAX_ERRORS_SHOWN:
                st.caption(f"…and {len(result.errors) - MAX_ERRORS_SHOWN} more.")
            # Record the failed attempt too — a rejected import is still
            # something an administrator may need to account for.
            job = create_job(session, spec, uploaded.name, current_user.id, len(mapped_rows))
            record_validation(session, job, mapping, result)
            try_commit(session, "Validation failed — recorded, nothing imported.")
            st.session_state[f"validated_{spec.job_type}"] = False
            return

        st.success(f"All {len(result.parsed)} row(s) valid.")
        if st.button(f"Confirm import of {len(result.parsed)} row(s)", type="primary"):
            job = create_job(session, spec, uploaded.name, current_user.id, len(mapped_rows))
            record_validation(session, job, mapping, result)
            written = spec.commit(session, result.parsed, current_user.id)
            record_confirmation(session, job, written)
            # `import_jobs` already records the run in detail; this entry
            # puts it on the same timeline as every other sensitive change,
            # so the audit log alone answers "what happened to this data".
            audit_service.record(
                session,
                action=audit_service.DATA_IMPORTED,
                object_type="import_jobs",
                object_id=job.id,
                user_id=current_user.id,
                new={"job_type": spec.job_type, "file": uploaded.name, "rows_written": written},
            )
            if try_commit(session, f"Imported {written} row(s)."):
                # Runs in its own transaction, after the import has landed
                # — see ImportSpec.after_commit. Progress is shown because
                # for term grades this is the slow half.
                if spec.after_commit:
                    bar = st.progress(0.0, text="Recalculating averages…")
                    note = spec.after_commit(
                        session,
                        result.parsed,
                        lambda done, total: bar.progress(
                            done / total, text=f"Recalculating averages… {done} of {total}"
                        ),
                    )
                    bar.empty()
                    if note:
                        flash("info", note)
                st.session_state[f"validated_{spec.job_type}"] = False
                clear_text_fields(_upload_form(spec))
                st.rerun()
