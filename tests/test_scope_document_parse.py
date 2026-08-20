"""Reading a review protocol out of an uploaded file.

Two failures are pinned here, and the first one is the expensive one:

  1. A .docx protocol states its eligibility criteria in a TABLE. python-docx's
     `.paragraphs` skips table content entirely, so reading only paragraphs
     drops the single most important section in the file — and the suggester
     would report a confident, half-empty scope.
  2. Truncation is never silent. A file cut by the character budget says so in
     the per-file report, naming what was lost.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from utils.scope_document import (  # noqa: E402
    UnsupportedDocument,
    assemble_document,
    extract_text,
    normalize_for_match,
)


def _docx_bytes() -> bytes:
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("Review protocol: periodontitis")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Inclusion criteria"
    table.cell(0, 1).text = "Exclusion criteria"
    table.cell(1, 0).text = "Adults with chronic periodontitis"
    table.cell(1, 1).text = "Uncontrolled diabetes"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _pdf_bytes(text: str) -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


class TestDocx:
    def test_table_cells_reach_the_text(self):
        text = extract_text("protocol.docx", _docx_bytes())
        assert "Adults with chronic periodontitis" in text
        assert "Uncontrolled diabetes" in text

    def test_paragraphs_reach_the_text_too(self):
        assert "Review protocol: periodontitis" in extract_text("protocol.docx", _docx_bytes())


class TestPdf:
    def test_text_layer_is_read(self):
        text = extract_text("protocol.pdf", _pdf_bytes("Primary outcome: probing pocket depth"))
        assert "probing pocket depth" in text.lower()

    def test_a_pdf_with_no_text_layer_reads_empty(self):
        """The endpoint turns this into a 422 that says 'scanned PDF', rather
        than an empty chip list that reads as 'no scope in this document'."""
        fitz = pytest.importorskip("fitz")
        doc = fitz.open()
        doc.new_page()
        data = doc.tobytes()
        doc.close()
        assert extract_text("scan.pdf", data).strip() == ""


class TestPlainText:
    def test_markdown_passes_through(self):
        assert extract_text("scope.md", b"# PICO\n- Adults") == "# PICO\n- Adults"

    def test_undecodable_bytes_do_not_raise(self):
        assert extract_text("scope.txt", b"caf\xe9 latte")


class TestRejections:
    def test_legacy_doc_is_rejected_with_something_actionable(self):
        with pytest.raises(UnsupportedDocument) as exc:
            extract_text("protocol.doc", b"\xd0\xcf\x11\xe0")
        assert ".docx or PDF" in str(exc.value)

    def test_unknown_extension_is_rejected(self):
        with pytest.raises(UnsupportedDocument):
            extract_text("protocol.pages", b"x")


class TestAssemble:
    def test_files_are_labelled_so_the_model_can_name_its_source(self):
        text, report = assemble_document([("a.md", b"alpha"), ("b.md", b"beta")])
        assert "--- a.md ---" in text and "--- b.md ---" in text
        assert [r["filename"] for r in report] == ["a.md", "b.md"]
        assert all(r["truncated"] is False for r in report)

    def test_truncation_is_reported_not_swallowed(self):
        text, report = assemble_document([("long.md", b"x" * 100)], max_total_chars=40)
        assert report[0] == {
            "filename": "long.md", "chars_total": 100, "chars_read": 40,
            "truncated": True, "empty": False,
        }
        assert len(text) == len("--- long.md ---\n") + 40

    def test_budget_is_spent_in_order_so_the_cut_lands_at_the_end(self):
        _, report = assemble_document(
            [("first.md", b"x" * 30), ("second.md", b"y" * 30)], max_total_chars=40
        )
        assert report[0]["truncated"] is False and report[0]["chars_read"] == 30
        assert report[1]["truncated"] is True and report[1]["chars_read"] == 10

    def test_an_empty_file_is_flagged_rather_than_dropped(self):
        text, report = assemble_document([("blank.md", b"   ")])
        assert report[0]["empty"] is True
        assert text == ""


class TestNormalizeForMatch:
    def test_punctuation_and_line_breaks_collapse(self):
        assert normalize_for_match("Probing   pocket\ndepth (PPD)!") == "probing pocket depth ppd"

    def test_empty_input_is_safe(self):
        assert normalize_for_match(None) == ""
