"""
PDF annotation stripper.

User-uploaded PDFs frequently carry markup from prior readers — yellow
highlights, underlines, sticky notes, stamps. These render as a confusing
"second layer" of marks on top of our own source-text highlights in the
review viewer, and there is no way to suppress them at render time (pdfjs's
`annotationMode: 0` only handles the overlay layer, not appearance streams
already baked into the page content).

This module produces a *clean* copy of the PDF with author markups removed
before it's served to the viewer. The viewer code is unchanged — it just
fetches the cleaned file instead.

Two-pass strategy:

1. **PyMuPDF (fitz):** open the PDF, walk every page, delete every annotation
   whose type is in `ANNOT_TYPES_TO_STRIP`. Saves the result to memory.
   Fast (~50 ms per page) and handles 90%+ of real-world cases where
   highlights are stored as proper `/Annot` objects.

2. **Ghostscript (optional, only if pikepdf-style strip was a no-op):**
   shell out to `gs -dPrinted=true ...`. The PDF spec marks highlight
   annotations as "do not show when printing"; ghostscript rebuilds the
   PDF as if printing, dropping print-invisible annotations. Catches cases
   where a previous tool flattened annotations into something pdfjs still
   surfaces.

If both passes fail (e.g., highlights drawn as literal rectangles in the
content stream by the PDF's author), we return the input unchanged — the
viewer falls back to showing the original. Callers should detect this via
the `was_cleaned` flag in the result.
"""

from __future__ import annotations

import io
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# PyMuPDF annotation subtype constants we want to remove. Keep links and
# widget (form field) annotations — those are functional, not decoration.
#
# Reference: PDF spec 12.5.6 (annotation subtypes) and fitz.PDF_ANNOT_*.
ANNOT_TYPES_TO_STRIP = {
    fitz.PDF_ANNOT_TEXT,         # sticky-note pop-ups
    fitz.PDF_ANNOT_FREE_TEXT,    # author free-text boxes
    fitz.PDF_ANNOT_LINE,
    fitz.PDF_ANNOT_SQUARE,
    fitz.PDF_ANNOT_CIRCLE,
    fitz.PDF_ANNOT_POLYGON,
    fitz.PDF_ANNOT_POLY_LINE,
    fitz.PDF_ANNOT_HIGHLIGHT,    # the main offender
    fitz.PDF_ANNOT_UNDERLINE,
    fitz.PDF_ANNOT_SQUIGGLY,
    fitz.PDF_ANNOT_STRIKE_OUT,
    fitz.PDF_ANNOT_STAMP,
    fitz.PDF_ANNOT_CARET,
    fitz.PDF_ANNOT_INK,
    fitz.PDF_ANNOT_POPUP,
    fitz.PDF_ANNOT_FILE_ATTACHMENT,
    fitz.PDF_ANNOT_SOUND,
    fitz.PDF_ANNOT_MOVIE,
    fitz.PDF_ANNOT_REDACT,
}


@dataclass
class CleanResult:
    pdf_bytes: bytes
    annotations_removed: int
    used_ghostscript: bool
    was_cleaned: bool  # True if any markup was actually removed


def _strip_with_fitz(pdf_bytes: bytes) -> tuple[bytes, int]:
    """First pass — PyMuPDF annotation removal. Returns (new_bytes, count)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    removed = 0
    try:
        for page in doc:
            # Collect annots first; iterating + deleting in one pass is unsafe.
            to_delete = []
            for annot in page.annots() or []:
                try:
                    subtype = annot.type[0]
                except Exception:
                    continue
                if subtype in ANNOT_TYPES_TO_STRIP:
                    to_delete.append(annot)
            for annot in to_delete:
                page.delete_annot(annot)
                removed += 1
        out = doc.tobytes(garbage=4, deflate=True, clean=True)
        return out, removed
    finally:
        doc.close()


def _flatten_with_ghostscript(pdf_bytes: bytes, timeout: int = 30) -> bytes | None:
    """Second-pass flatten via Ghostscript. Returns None on failure.

    The `-dPrinted=true` flag tells gs to render the document as if printing,
    which by spec hides annotations marked "do not print" (highlights default
    to that). The output is a freshly written PDF with the dropped annotations
    no longer in the file at all.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as in_f:
        in_f.write(pdf_bytes)
        in_path = in_f.name
    out_path = in_path + ".clean.pdf"
    try:
        proc = subprocess.run(
            [
                "gs",
                "-dBATCH",
                "-dNOPAUSE",
                "-dQUIET",
                "-dPrinted=true",
                "-dPDFSETTINGS=/prepress",
                "-sDEVICE=pdfwrite",
                f"-sOutputFile={out_path}",
                in_path,
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning(
                "Ghostscript flatten failed (rc=%s): %s",
                proc.returncode,
                proc.stderr.decode("utf-8", errors="replace")[:500],
            )
            return None
        return Path(out_path).read_bytes()
    except subprocess.TimeoutExpired:
        logger.warning("Ghostscript flatten timed out")
        return None
    except Exception as e:
        logger.warning("Ghostscript flatten errored: %s", e)
        return None
    finally:
        for p in (in_path, out_path):
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


def clean_pdf_bytes(pdf_bytes: bytes, *, use_ghostscript: bool = True) -> CleanResult:
    """Produce a marker-free copy of a PDF.

    Args:
        pdf_bytes: the original PDF as bytes.
        use_ghostscript: if True and the fitz pass removed nothing, also try
            the ghostscript flatten as a fallback. Default True.

    Returns:
        CleanResult with cleaned bytes (or the original if nothing could be
        removed), the count of stripped annotations, and flags indicating
        which passes did anything.
    """
    if not pdf_bytes:
        raise ValueError("empty pdf_bytes")

    # Pass 1: PyMuPDF strip
    try:
        cleaned, removed = _strip_with_fitz(pdf_bytes)
    except Exception as e:
        logger.exception("PyMuPDF annotation strip failed: %s", e)
        cleaned, removed = pdf_bytes, 0

    # Pass 2: ghostscript flatten — only if fitz removed nothing, since the
    # gs pass is more expensive and the common case is "fitz handles it."
    used_gs = False
    if use_ghostscript and removed == 0:
        flat = _flatten_with_ghostscript(cleaned)
        if flat is not None and len(flat) > 0:
            cleaned = flat
            used_gs = True

    return CleanResult(
        pdf_bytes=cleaned,
        annotations_removed=removed,
        used_ghostscript=used_gs,
        was_cleaned=(removed > 0 or used_gs),
    )
