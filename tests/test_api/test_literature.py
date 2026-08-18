"""
Route-level tests for /api/v1/literature/search — the unified
ClinicalTrials.gov + PubMed fan-out endpoint.

Mocks both underlying services' fetch_search functions to test: scope
correctly gates which upstream(s) get called, round-robin interleaving,
and graceful partial-failure degradation (never a hard error just because
one of two sources is down).
"""

import os
import sys
from unittest.mock import AsyncMock, patch
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.dependencies import get_current_user
from app.main import app

FAKE_USER_ID = uuid4()


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


def _ct_result(n, total=None, next_page_token=None):
    return {
        "total": total if total is not None else n,
        "results": [{"nctId": f"NCT0000000{i}"} for i in range(n)],
        "nextPageToken": next_page_token,
    }


def _pm_result(n, total=None):
    return {"total": total if total is not None else n, "results": [{"pmid": str(1000 + i)} for i in range(n)]}


class TestScopeGating:
    def test_scope_all_calls_both_sources(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(2))
        ) as ct_mock, patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(2))
        ) as pm_mock:
            resp = client.get("/api/v1/literature/search?term=pain&scope=all")
        assert resp.status_code == 200
        ct_mock.assert_called_once()
        pm_mock.assert_called_once()

    def test_scope_ctgov_never_calls_pubmed(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(3))
        ) as ct_mock, patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(3))
        ) as pm_mock:
            resp = client.get("/api/v1/literature/search?term=pain&scope=ctgov")
        assert resp.status_code == 200
        ct_mock.assert_called_once()
        pm_mock.assert_not_called()
        assert resp.json()["counts"]["pubmed"] is None

    def test_scope_pubmed_never_calls_ctgov(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(3))
        ) as ct_mock, patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(3))
        ) as pm_mock:
            resp = client.get("/api/v1/literature/search?term=pain&scope=pubmed")
        assert resp.status_code == 200
        pm_mock.assert_called_once()
        ct_mock.assert_not_called()
        assert resp.json()["counts"]["ctgov"] is None

    def test_invalid_scope_returns_400(self, client):
        resp = client.get("/api/v1/literature/search?term=pain&scope=nonsense")
        assert resp.status_code == 400


class TestInterleaving:
    def test_results_are_round_robin_merged_not_grouped(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(3))
        ), patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(2))
        ):
            resp = client.get("/api/v1/literature/search?term=pain&scope=all")
        sources = [r["source"] for r in resp.json()["results"]]
        # ctgov=3, pubmed=2 -> ctgov, pubmed, ctgov, pubmed, ctgov (round robin, tail from the longer list)
        assert sources == ["ctgov", "pubmed", "ctgov", "pubmed", "ctgov"]

    def test_each_result_tagged_with_its_source(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(1))
        ), patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(1))
        ):
            resp = client.get("/api/v1/literature/search?term=pain&scope=all")
        results = resp.json()["results"]
        assert results[0]["source"] == "ctgov" and results[0]["nctId"] == "NCT00000000"
        assert results[1]["source"] == "pubmed" and results[1]["pmid"] == "1000"


class TestPartialFailure:
    def test_ctgov_down_still_returns_pubmed_results(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search",
            new=AsyncMock(side_effect=HTTPException(status_code=502, detail="down")),
        ), patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(2))
        ):
            resp = client.get("/api/v1/literature/search?term=pain&scope=all")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 2
        assert all(r["source"] == "pubmed" for r in body["results"])
        assert body["errors"] == {"ctgov": "ClinicalTrials.gov is unreachable."}
        assert body["message"] == "ClinicalTrials.gov is unreachable — showing PubMed results only."

    def test_pubmed_down_still_returns_ctgov_results(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(2))
        ), patch(
            "app.api.v1.literature.pubmed_service.fetch_search",
            new=AsyncMock(side_effect=HTTPException(status_code=502, detail="down")),
        ):
            resp = client.get("/api/v1/literature/search?term=pain&scope=all")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 2
        assert body["message"] == "PubMed is unreachable — showing trial results only."

    def test_both_down_returns_empty_results_not_a_500(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search",
            new=AsyncMock(side_effect=HTTPException(status_code=502, detail="down")),
        ), patch(
            "app.api.v1.literature.pubmed_service.fetch_search",
            new=AsyncMock(side_effect=HTTPException(status_code=502, detail="down")),
        ):
            resp = client.get("/api/v1/literature/search?term=pain&scope=all")
        assert resp.status_code == 200
        body = resp.json()
        assert body["results"] == []
        assert set(body["errors"].keys()) == {"ctgov", "pubmed"}
        assert "Check your connection" in body["message"]

    def test_no_errors_key_present_when_both_succeed(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(1))
        ), patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(1))
        ):
            resp = client.get("/api/v1/literature/search?term=pain&scope=all")
        body = resp.json()
        assert body["errors"] == {}
        assert body["message"] is None


class TestIdDetection:
    """Pasting an exact NCT ID or PMID must do a precise single-record
    lookup instead of a generic term search — mirrors the shared design
    mockup's own searchCtgov()/searchPubmed() ID-detection logic."""

    def test_nct_id_term_uses_direct_study_lookup_not_term_search(self, client):
        with patch(
            "app.api.v1.literature.ct_service.get_study_normalized",
            new=AsyncMock(return_value={"nctId": "NCT04812345", "title": {"brief": "x"}}),
        ) as get_mock, patch("app.api.v1.literature.ct_service.fetch_search", new=AsyncMock()) as search_mock, patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(0))
        ):
            resp = client.get("/api/v1/literature/search?term=NCT04812345&scope=all")
        assert resp.status_code == 200
        get_mock.assert_called_once_with("NCT04812345")
        search_mock.assert_not_called()
        body = resp.json()
        assert body["counts"]["ctgov"] == 1
        assert body["results"][0]["source"] == "ctgov"
        assert body["results"][0]["nctId"] == "NCT04812345"

    def test_nct_id_term_searches_pubmed_via_secondary_source_id(self, client):
        """An NCT ID as the PubMed-side term should search via [si] (papers
        referencing this trial), not a literal-text term search."""
        with patch(
            "app.api.v1.literature.ct_service.get_study_normalized",
            new=AsyncMock(return_value={"nctId": "NCT04812345", "title": {"brief": "x"}}),
        ), patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(1))
        ) as pm_mock:
            resp = client.get("/api/v1/literature/search?term=NCT04812345&scope=all")
        assert resp.status_code == 200
        pm_mock.assert_called_once()
        called_term = pm_mock.call_args[0][0]
        assert called_term == "NCT04812345[si]"

    def test_nct_id_not_found_returns_empty_not_error(self, client):
        with patch(
            "app.api.v1.literature.ct_service.get_study_normalized",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="not found")),
        ), patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(0))
        ):
            resp = client.get("/api/v1/literature/search?term=NCT00000000&scope=all")
        assert resp.status_code == 200
        assert resp.json()["counts"]["ctgov"] == 0
        assert resp.json()["errors"] == {}

    def test_pmid_term_uses_direct_article_lookup_not_esearch(self, client):
        with patch(
            "app.api.v1.literature.pubmed_service.get_article_normalized",
            new=AsyncMock(return_value={"pmid": "34878953", "title": "x"}),
        ) as get_mock, patch("app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock()) as search_mock, patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(0))
        ):
            resp = client.get("/api/v1/literature/search?term=34878953&scope=all")
        assert resp.status_code == 200
        get_mock.assert_called_once_with("34878953")
        search_mock.assert_not_called()
        body = resp.json()
        assert body["counts"]["pubmed"] == 1
        assert body["results"][0]["source"] == "pubmed"
        assert body["results"][0]["pmid"] == "34878953"

    def test_pmid_not_found_returns_empty_not_error(self, client):
        with patch(
            "app.api.v1.literature.pubmed_service.get_article_normalized",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="not found")),
        ), patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(0))
        ):
            resp = client.get("/api/v1/literature/search?term=99999999&scope=all")
        assert resp.status_code == 200
        assert resp.json()["counts"]["pubmed"] == 0
        assert resp.json()["errors"] == {}


class TestPagination:
    """Load-more support: ctgovPageToken/pubmedOffset thread through to the
    upstream calls, and nextCtgovPageToken/nextPubmedOffset are computed so
    the frontend knows whether/how to fetch the next page — see
    LiteratureSearchDrawer's loadMore()."""

    def test_ctgov_page_token_passed_through_to_upstream(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(2))
        ) as ct_mock, patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(0))
        ):
            resp = client.get("/api/v1/literature/search?term=pain&scope=ctgov&ctgovPageToken=abc123")
        assert resp.status_code == 200
        called_params = ct_mock.call_args[0][0]
        assert called_params["pageToken"] == "abc123"

    def test_ctgov_next_page_token_surfaces_in_response(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search",
            new=AsyncMock(return_value=_ct_result(2, next_page_token="def456")),
        ), patch("app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(0))):
            resp = client.get("/api/v1/literature/search?term=pain&scope=ctgov")
        assert resp.json()["nextCtgovPageToken"] == "def456"

    def test_ctgov_no_next_page_token_when_upstream_reports_none(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(2))
        ), patch("app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(0))):
            resp = client.get("/api/v1/literature/search?term=pain&scope=ctgov")
        assert resp.json()["nextCtgovPageToken"] is None

    def test_pubmed_offset_passed_through_as_retstart(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(0))
        ), patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(2, total=50))
        ) as pm_mock:
            resp = client.get("/api/v1/literature/search?term=pain&scope=pubmed&pubmedOffset=15")
        assert resp.status_code == 200
        assert pm_mock.call_args.kwargs["retstart"] == 15

    def test_pubmed_next_offset_advances_by_results_returned(self, client):
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(0))
        ), patch(
            "app.api.v1.literature.pubmed_service.fetch_search",
            new=AsyncMock(return_value=_pm_result(15, total=1497000)),
        ):
            resp = client.get("/api/v1/literature/search?term=oral&scope=pubmed&pageSize=15")
        assert resp.json()["nextPubmedOffset"] == 15

    def test_pubmed_next_offset_is_none_once_exhausted(self, client):
        """total=5, offset=0, 5 results returned -> nextOffset would be 5,
        which is NOT < total(5), so there's no next page."""
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(0))
        ), patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(5, total=5))
        ):
            resp = client.get("/api/v1/literature/search?term=rare+disease&scope=pubmed")
        assert resp.json()["nextPubmedOffset"] is None

    def test_exact_id_lookup_never_reports_a_next_page(self, client):
        """A single NCT ID/PMID lookup is one record — there is no page 2."""
        with patch(
            "app.api.v1.literature.ct_service.get_study_normalized",
            new=AsyncMock(return_value={"nctId": "NCT04812345", "title": {"brief": "x"}}),
        ), patch("app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(0))):
            resp = client.get("/api/v1/literature/search?term=NCT04812345&scope=all")
        assert resp.json()["nextCtgovPageToken"] is None

    def test_missing_pagination_params_default_to_first_page(self, client):
        """No ctgovPageToken/pubmedOffset supplied (a fresh search) must not
        pass a stale/garbage token upstream."""
        with patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(1))
        ) as ct_mock, patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(1))
        ) as pm_mock:
            resp = client.get("/api/v1/literature/search?term=pain&scope=all")
        assert resp.status_code == 200
        assert "pageToken" not in ct_mock.call_args[0][0]
        assert pm_mock.call_args.kwargs["retstart"] == 0


class TestIdDetectionKeywordGuard:
    def test_plain_keyword_term_does_not_trigger_id_lookup(self, client):
        with patch(
            "app.api.v1.literature.ct_service.get_study_normalized", new=AsyncMock()
        ) as get_mock, patch(
            "app.api.v1.literature.ct_service.fetch_search", new=AsyncMock(return_value=_ct_result(1))
        ), patch(
            "app.api.v1.literature.pubmed_service.get_article_normalized", new=AsyncMock()
        ) as pm_get_mock, patch(
            "app.api.v1.literature.pubmed_service.fetch_search", new=AsyncMock(return_value=_pm_result(1))
        ):
            resp = client.get("/api/v1/literature/search?term=antibiotic+prophylaxis&scope=all")
        assert resp.status_code == 200
        get_mock.assert_not_called()
        pm_get_mock.assert_not_called()
