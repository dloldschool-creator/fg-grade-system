"""The SF2 download buttons, shared by the Attendance and SF2 pages.

Advisers print their SF2 from the bottom of the Attendance page, once the
month is finalized, and never see the SF2 page itself (2026-10-17). Before
that they went straight to the SF2 page expecting to encode there, as the
Excel workbook's SF2 sheet had trained them to. Registrars and the School
Head keep the SF2 page. Both places build the file the same way, here.

Not a page, so it lives outside `app/admin_pages/`: the import-order test
treats every module in that folder as one.
"""

import logging

import streamlit as st

from app.sf2_report import build_sf2_workbook, workbook_to_bytes
from app.xlsx_render import workbook_to_pdf

_LOGGER = logging.getLogger(__name__)


def deferred_workbook_builders(build_workbook, failure_message: str, *failure_args):
    """Two no-argument callables for `st.download_button(data=...)`, shared
    by every form that offers an Excel and a PDF download (SF2 here, SF9).

    `build_workbook(session)` returns the openpyxl workbook. Streamlit runs
    the callables on a separate thread *after* the script run, when the
    page's own session is already closed — so each opens its own, and the
    closure should hold only ids and numbers, never the session or ORM
    objects.
    """

    def _workbook():
        from app.database import SessionLocal

        session = SessionLocal()
        try:
            return build_workbook(session)
        except Exception:
            # Streamlit logs the traceback and tells the browser the file
            # failed, but not *which* form; name it so the log can be read.
            _LOGGER.exception(failure_message, *failure_args)
            raise
        finally:
            session.close()

    def build_xlsx() -> bytes:
        return workbook_to_bytes(_workbook())

    def build_pdf() -> bytes:
        return workbook_to_pdf(_workbook())

    return build_xlsx, build_pdf


def deferred_builders(section_id, school_year_id, year: int, month: int):
    """The SF2's Excel and PDF builders for one section-month."""
    return deferred_workbook_builders(
        lambda session: build_sf2_workbook(session, section_id, school_year_id, year, month),
        "SF2 download failed for section %s, %04d-%02d", section_id, year, month,
    )


def render_sf2_downloads(section, school_year_id, year: int, month: int) -> None:
    """The Excel and PDF buttons for one section-month."""
    stem = f"SF2_{section.name.replace(' ', '')}_{year}-{month:02d}"
    # Deferred: a callable `data` is built only when its button is
    # clicked. Passing bytes would build the workbook and the PDF on
    # every rerun of the page — each widget, the reprint confirmation
    # included — for a download nobody asked for.
    build_xlsx, build_pdf = deferred_builders(section.id, school_year_id, year, month)
    st.caption(
        "The file is built when you click, so it can take a few seconds. If it "
        "fails, try once more; if it keeps failing, tell the ICT coordinator."
    )

    col_a, col_b = st.columns(2)
    with col_a:
        st.download_button(
            "Download SF2 (Excel)",
            data=build_xlsx,
            file_name=f"{stem}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with col_b:
        st.download_button(
            "Download SF2 (PDF)",
            data=build_pdf,
            file_name=f"{stem}.pdf",
            mime="application/pdf",
        )
