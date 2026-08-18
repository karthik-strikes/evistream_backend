"""
Route-level tests for /api/v1/trials/*.

Uses FastAPI's TestClient (sync, drives async routes internally — no
pytest-asyncio needed) with `get_current_user` overridden via
`app.dependency_overrides`, and mocks the ClinicalTrials.gov service +
Supabase/storage calls via unittest.mock, matching this repo's existing
mocking convention (tests/test_two_stage_extractor.py).
"""

import os
import sys
from unittest.mock import MagicMock, patch
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from app.dependencies import get_current_user
from app.main import app

FAKE_USER_ID = uuid4()
FAKE_PROJECT_ID = str(uuid4())


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


def _normalized_stub(nct_id="NCT04812345"):
    return {
        "nctId": nct_id,
        "sourceUrl": f"https://clinicaltrials.gov/study/{nct_id}",
        "title": {"brief": "Test Trial", "official": "Test Trial Official"},
        "status": {"overall": "COMPLETED", "hasResults": True},
        "phase": ["PHASE4"],
        "sponsor": {"lead": "Acme Sponsor"},
        "results": {"outcomeMeasures": [{"title": "Primary"}]},
        "interventions": [],
        "arms": [],
        "outcomes": {"primary": [], "secondary": []},
        "locations": [],
        "references": [],
        "documents": [],
        "design": {},
        "eligibility": {},
        "enrollment": {},
    }


class TestNctValidation:
    def test_invalid_nct_id_returns_400(self, client):
        resp = client.get("/api/v1/trials/notanid")
        assert resp.status_code == 400

    def test_invalid_nct_id_on_raw_returns_400(self, client):
        resp = client.get("/api/v1/trials/notanid/raw")
        assert resp.status_code == 400


class TestGetTrial:
    def test_upstream_404_propagates(self, client):
        with patch(
            "app.api.v1.clinical_trials.ct_service.get_study_normalized",
            side_effect=HTTPException(status_code=404, detail="Trial NCT00000000 not found."),
        ):
            resp = client.get("/api/v1/trials/NCT00000000")
        assert resp.status_code == 404

    def test_upstream_timeout_maps_to_502(self, client):
        with patch(
            "app.api.v1.clinical_trials.ct_service.get_study_normalized",
            side_effect=HTTPException(status_code=502, detail="ClinicalTrials.gov is unreachable."),
        ):
            resp = client.get("/api/v1/trials/NCT04307940")
        assert resp.status_code == 502

    def test_no_results_trial_returns_200_with_null_results(self, client):
        stub = _normalized_stub()
        stub["status"]["hasResults"] = False
        stub["results"] = None
        with patch("app.api.v1.clinical_trials.ct_service.get_study_normalized", return_value=stub):
            resp = client.get("/api/v1/trials/NCT04812345")
        assert resp.status_code == 200
        assert resp.json()["results"] is None


class TestSearchRouteOrdering:
    def test_search_resolves_to_search_handler_not_400(self, client):
        """Proves /search (a literal path) is matched before /{nct_id} —
        if route order were wrong, this would 400 from the NCT-regex guard."""
        with patch(
            "app.api.v1.clinical_trials.ct_service.fetch_search",
            return_value={"total": 0, "nextPageToken": None, "results": []},
        ):
            resp = client.get("/api/v1/trials/search?term=lung+cancer")
        assert resp.status_code == 200
        assert resp.json() == {"total": 0, "nextPageToken": None, "results": []}

    def test_phase_param_is_translated_to_aggfilters_not_filter_phase(self, client):
        """Regression test: filter.phase is not a real upstream param (see
        clinical_trials_service.phase_agg_filter's docstring — confirmed
        against the live API, it 400s). The route must translate the
        frontend's PHASE1,PHASE2 style param into aggFilters=phase:1 2."""
        captured = {}

        async def fake_fetch_search(params):
            captured.update(params)
            return {"total": 0, "nextPageToken": None, "results": []}

        with patch("app.api.v1.clinical_trials.ct_service.fetch_search", side_effect=fake_fetch_search):
            resp = client.get("/api/v1/trials/search?term=pain&phase=PHASE1,PHASE2&status=COMPLETED")

        assert resp.status_code == 200
        assert "filter.phase" not in captured
        assert captured.get("aggFilters") == "phase:1 2"
        assert captured.get("filter.overallStatus") == "COMPLETED"


class TestImportTrial:
    def test_first_import_succeeds_and_inserts(self, client):
        stub = _normalized_stub()
        mock_supabase = MagicMock()
        # No existing row (dedup check) -> insert succeeds
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        inserted_row = {"id": str(uuid4()), "nct_id": "NCT04812345", "filename": "Test Trial"}
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [inserted_row]

        with patch("app.api.v1.clinical_trials.supabase", mock_supabase), patch(
            "app.api.v1.clinical_trials.check_project_access"
        ), patch(
            "app.api.v1.clinical_trials.ct_service.get_study_normalized", return_value=stub
        ), patch(
            "app.api.v1.clinical_trials.storage_service.upload_markdown", return_value="markdown/proj/hash.md"
        ), patch(
            "app.services.activity_service.log_activity"
        ):
            resp = client.post(
                "/api/v1/trials/NCT04812345/import", json={"project_id": FAKE_PROJECT_ID}
            )

        assert resp.status_code == 201
        assert resp.json() == inserted_row
        mock_supabase.table.return_value.insert.assert_called_once()
        inserted_payload = mock_supabase.table.return_value.insert.call_args[0][0]
        assert inserted_payload["nct_id"] == "NCT04812345"
        assert inserted_payload["source_type"] == "ctgov"
        assert inserted_payload["s3_pdf_path"] is None
        assert inserted_payload["processing_status"] == "completed"

    def test_duplicate_nct_id_returns_duplicate_without_second_insert(self, client):
        stub = _normalized_stub()
        mock_supabase = MagicMock()
        existing_row = {"id": str(uuid4()), "nct_id": "NCT04812345", "filename": "Already Imported"}
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            existing_row
        ]

        with patch("app.api.v1.clinical_trials.supabase", mock_supabase), patch(
            "app.api.v1.clinical_trials.check_project_access"
        ), patch(
            "app.api.v1.clinical_trials.ct_service.get_study_normalized", return_value=stub
        ):
            resp = client.post(
                "/api/v1/trials/NCT04812345/import", json={"project_id": FAKE_PROJECT_ID}
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["duplicate"] is True
        assert body["document"] == existing_row
        mock_supabase.table.return_value.insert.assert_not_called()

    def test_invalid_nct_id_returns_400(self, client):
        resp = client.post("/api/v1/trials/notanid/import", json={"project_id": FAKE_PROJECT_ID})
        assert resp.status_code == 400

    def test_no_project_access_returns_403(self, client):
        with patch(
            "app.api.v1.clinical_trials.check_project_access",
            side_effect=HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access"),
        ):
            resp = client.post(
                "/api/v1/trials/NCT04812345/import", json={"project_id": FAKE_PROJECT_ID}
            )
        assert resp.status_code == 403
