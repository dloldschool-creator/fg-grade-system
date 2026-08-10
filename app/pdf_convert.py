"""XLSX → PDF via headless LibreOffice, per CLAUDE.md's stack decision:
fill the official DepEd template with `openpyxl` (exact layout preserved),
then flatten to PDF with `soffice --headless --convert-to pdf`.

LibreOffice is an external program, not a Python package, so it can be
absent. That's treated as a normal, reportable state rather than an
error: `find_soffice()` returns None and callers offer the XLSX (itself a
perfectly good deliverable — it opens in Excel and prints identically)
while explaining what to install for one-click PDF. Nothing here ever
installs software on the user's machine.
"""

import os
import shutil
import subprocess
import tempfile

# Windows installs LibreOffice outside PATH more often than not, so the
# usual locations are probed before giving up.
_WINDOWS_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)

CONVERSION_TIMEOUT_SECONDS = 120


def find_soffice() -> str | None:
    """Path to the LibreOffice binary, or None when it isn't installed."""
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for candidate in _WINDOWS_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def is_pdf_available() -> bool:
    return find_soffice() is not None


class PdfConversionError(RuntimeError):
    pass


def xlsx_to_pdf(xlsx_bytes: bytes, *, basename: str = "report") -> bytes:
    """Converts a workbook to PDF. Raises PdfConversionError if
    LibreOffice is missing or the conversion fails — callers are expected
    to check `is_pdf_available()` first and offer the XLSX instead."""
    soffice = find_soffice()
    if soffice is None:
        raise PdfConversionError(
            "LibreOffice was not found. Install it to enable PDF export; the "
            "Excel download works without it."
        )

    with tempfile.TemporaryDirectory() as workdir:
        source = os.path.join(workdir, f"{basename}.xlsx")
        with open(source, "wb") as handle:
            handle.write(xlsx_bytes)

        # -env:UserInstallation gives this run its own profile directory,
        # so converting never collides with a LibreOffice window the user
        # already has open (which otherwise makes the headless call exit
        # immediately without producing anything).
        profile_dir = os.path.join(workdir, "profile")
        result = subprocess.run(
            [
                soffice,
                f"-env:UserInstallation=file:///{profile_dir.replace(os.sep, '/')}",
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf:calc_pdf_Export",
                "--outdir",
                workdir,
                source,
            ],
            capture_output=True,
            timeout=CONVERSION_TIMEOUT_SECONDS,
        )
        produced = os.path.join(workdir, f"{basename}.pdf")
        if not os.path.exists(produced):
            raise PdfConversionError(
                "LibreOffice did not produce a PDF: "
                f"{(result.stderr or result.stdout).decode(errors='replace')[:400]}"
            )
        with open(produced, "rb") as handle:
            return handle.read()
