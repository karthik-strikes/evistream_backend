"""
Unit tests for endnote_service.py's `.enlx` parsing — specifically the
identifier-mining added so a PDF-less EndNote reference gets the same
open-access fetch chance as a RIS/PubMed import (see import_tasks.py's
_resolve_oa_pdf). Builds a minimal real `.enlx` (sqlite refs table zipped as
sdb/sdb.eni) rather than mocking sqlite/zipfile, since that's the actual
on-disk shape the parser reads.
"""

import os
import sqlite3
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.endnote_service import parse_enlx  # noqa: E402

_COLUMNS = (
    "id", "title", "author", "year", "abstract", "secondary_title",
    "electronic_resource_number", "pages", "volume", "number", "url",
)


def _build_enlx(path: str, rows: list) -> None:
    db_path = path + ".sdb"
    conn = sqlite3.connect(db_path)
    conn.execute(f"CREATE TABLE refs ({', '.join(c + ' TEXT' for c in _COLUMNS)})")
    conn.execute("CREATE TABLE file_res (refs_id INTEGER, file_path TEXT, file_pos INTEGER)")
    for row in rows:
        placeholders = ", ".join("?" for _ in _COLUMNS)
        conn.execute(
            f"INSERT INTO refs ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
            [row.get(c) for c in _COLUMNS],
        )
    conn.commit()
    conn.close()

    with zipfile.ZipFile(path, "w") as zf:
        zf.write(db_path, "sdb/sdb.eni")
    os.unlink(db_path)


class TestDoiMining:
    def test_falls_back_to_doi_org_link_when_ern_empty(self, tmp_path):
        enlx = str(tmp_path / "lib.enlx")
        _build_enlx(enlx, [{
            "id": 1, "title": "A paper",
            "url": "https://doi.org/10.1016/j.example.2021.01.001",
        }])
        assert parse_enlx(enlx)[0].doi == "10.1016/j.example.2021.01.001"

    def test_electronic_resource_number_wins_over_url(self, tmp_path):
        enlx = str(tmp_path / "lib.enlx")
        _build_enlx(enlx, [{
            "id": 1, "title": "A paper",
            "electronic_resource_number": "10.1000/real",
            "url": "https://doi.org/10.9999/decoy",
        }])
        assert parse_enlx(enlx)[0].doi == "10.1000/real"

    def test_no_doi_anywhere_is_none(self, tmp_path):
        enlx = str(tmp_path / "lib.enlx")
        _build_enlx(enlx, [{"id": 1, "title": "A paper", "url": "https://publisher.example/landing"}])
        assert parse_enlx(enlx)[0].doi is None


class TestPmidPmcidMining:
    def test_pmid_mined_from_pubmed_url(self, tmp_path):
        enlx = str(tmp_path / "lib.enlx")
        _build_enlx(enlx, [{"id": 1, "title": "A paper", "url": "https://pubmed.ncbi.nlm.nih.gov/38472919/"}])
        assert parse_enlx(enlx)[0].pmid == "38472919"

    def test_pmcid_mined_from_pmc_url(self, tmp_path):
        enlx = str(tmp_path / "lib.enlx")
        _build_enlx(enlx, [{
            "id": 1, "title": "A paper",
            "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/",
        }])
        assert parse_enlx(enlx)[0].pmcid == "PMC1234567"

    def test_no_identifiers_when_url_absent(self, tmp_path):
        enlx = str(tmp_path / "lib.enlx")
        _build_enlx(enlx, [{"id": 1, "title": "A paper"}])
        rec = parse_enlx(enlx)[0]
        assert rec.pmid is None
        assert rec.pmcid is None
        assert rec.urls == []


class TestUrlListForFallbackFetch:
    def test_all_urls_kept_not_just_first(self, tmp_path):
        enlx = str(tmp_path / "lib.enlx")
        _build_enlx(enlx, [{
            "id": 1, "title": "A paper",
            "url": "https://publisher.example/landing\rhttps://publisher.example/paper.pdf",
        }])
        rec = parse_enlx(enlx)[0]
        assert rec.urls == ["https://publisher.example/landing", "https://publisher.example/paper.pdf"]
        assert rec.url == "https://publisher.example/landing"  # first-for-display unchanged

    def test_non_http_entry_dropped_from_fallback_list(self, tmp_path):
        """A stale local file:// path from the original desktop library isn't
        fetchable and must not reach fetch_direct_pdf."""
        enlx = str(tmp_path / "lib.enlx")
        _build_enlx(enlx, [{"id": 1, "title": "A paper", "url": "file:///Users/me/Desktop/paper.pdf"}])
        rec = parse_enlx(enlx)[0]
        assert rec.urls == []
        assert rec.url == "file:///Users/me/Desktop/paper.pdf"  # display field is unaffected

    def test_duplicate_urls_deduplicated(self, tmp_path):
        enlx = str(tmp_path / "lib.enlx")
        _build_enlx(enlx, [{
            "id": 1, "title": "A paper",
            "url": "https://publisher.example/paper.pdf\rhttps://publisher.example/paper.pdf",
        }])
        assert parse_enlx(enlx)[0].urls == ["https://publisher.example/paper.pdf"]
