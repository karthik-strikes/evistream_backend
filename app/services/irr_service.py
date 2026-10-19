"""Service for computing inter-rater reliability (IRR) metrics."""

import logging
from supabase import create_client, Client
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)


def get_supabase() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _cohens_kappa(ratings_1: List[str], ratings_2: List[str]) -> Optional[float]:
    """Compute Cohen's kappa for two raters on categorical data."""
    if len(ratings_1) != len(ratings_2) or len(ratings_1) == 0:
        return None

    n = len(ratings_1)
    categories = sorted(set(ratings_1) | set(ratings_2))
    if len(categories) < 2:
        return 1.0  # Perfect agreement on single category

    # Build confusion matrix
    matrix: Dict[str, Dict[str, int]] = {c: {c2: 0 for c2 in categories} for c in categories}
    for r1, r2 in zip(ratings_1, ratings_2):
        matrix[r1][r2] += 1

    # Observed agreement
    observed = sum(matrix[c][c] for c in categories) / n

    # Expected agreement
    expected = 0
    for c in categories:
        p1 = sum(matrix[c][c2] for c2 in categories) / n
        p2 = sum(matrix[c2][c] for c2 in categories) / n
        expected += p1 * p2

    if expected == 1:
        return 1.0

    kappa = (observed - expected) / (1 - expected)
    return round(kappa, 4)


def _percent_agreement(values_1: List[Any], values_2: List[Any]) -> Optional[float]:
    """Compute percent agreement between two sets of values."""
    if len(values_1) != len(values_2) or len(values_1) == 0:
        return None

    agreements = sum(
        1 for v1, v2 in zip(values_1, values_2)
        if str(v1).strip().lower() == str(v2).strip().lower()
    )
    return round(agreements / len(values_1), 4)


def _icc(values_1: List[float], values_2: List[float]) -> Optional[float]:
    """Compute intraclass correlation coefficient (ICC) for numeric data. Simplified ICC(2,1)."""
    if len(values_1) != len(values_2) or len(values_1) < 3:
        return None

    n = len(values_1)
    mean_1 = sum(values_1) / n
    mean_2 = sum(values_2) / n
    grand_mean = (mean_1 + mean_2) / 2

    # Between-subjects variance
    subject_means = [(v1 + v2) / 2 for v1, v2 in zip(values_1, values_2)]
    ms_between = sum((sm - grand_mean) ** 2 for sm in subject_means) * 2 / (n - 1)

    # Within-subjects variance
    ms_within = sum((v1 - v2) ** 2 for v1, v2 in zip(values_1, values_2)) / (2 * n)

    if ms_between + ms_within == 0:
        return 1.0

    icc_val = (ms_between - ms_within) / (ms_between + ms_within)
    return round(max(-1, min(1, icc_val)), 4)


async def compute_irr(
    project_id: UUID,
    form_id: UUID,
    metric_type: str = "percent_agreement",
) -> Dict[str, Any]:
    """Compute IRR metrics for a project+form."""
    supabase = get_supabase()

    # Get all manual results with reviewer roles
    results = supabase.table("extraction_results")\
        .select("document_id, extracted_data, reviewer_role")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .eq("extraction_type", "manual")\
        .in_("reviewer_role", ["reviewer_1", "reviewer_2"])\
        .execute()

    # Group by document
    by_doc: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for r in (results.data or []):
        doc_id = r["document_id"]
        role = r["reviewer_role"]
        if doc_id not in by_doc:
            by_doc[doc_id] = {}
        by_doc[doc_id][role] = r.get("extracted_data", {})

    # Only use documents where both reviewers completed
    paired_docs = [
        doc_id for doc_id, roles in by_doc.items()
        if "reviewer_1" in roles and "reviewer_2" in roles
    ]

    if not paired_docs:
        return {"overall": None, "by_field": {}, "sample_size": 0}

    # Collect field-level values
    all_fields: set = set()
    for doc_id in paired_docs:
        all_fields.update(by_doc[doc_id].get("reviewer_1", {}).keys())
        all_fields.update(by_doc[doc_id].get("reviewer_2", {}).keys())

    field_metrics = {}
    overall_r1 = []
    overall_r2 = []

    for field in sorted(all_fields):
        r1_vals = []
        r2_vals = []
        for doc_id in paired_docs:
            v1 = by_doc[doc_id].get("reviewer_1", {}).get(field)
            v2 = by_doc[doc_id].get("reviewer_2", {}).get(field)
            if v1 is not None and v2 is not None:
                r1_vals.append(str(v1))
                r2_vals.append(str(v2))
                overall_r1.append(str(v1))
                overall_r2.append(str(v2))

        if not r1_vals:
            continue

        if metric_type == "cohens_kappa":
            field_metrics[field] = _cohens_kappa(r1_vals, r2_vals)
        elif metric_type == "percent_agreement":
            field_metrics[field] = _percent_agreement(r1_vals, r2_vals)
        elif metric_type == "icc":
            try:
                numeric_1 = [float(v) for v in r1_vals]
                numeric_2 = [float(v) for v in r2_vals]
                field_metrics[field] = _icc(numeric_1, numeric_2)
            except ValueError:
                field_metrics[field] = None

    # Overall metric
    if metric_type == "cohens_kappa":
        overall = _cohens_kappa(overall_r1, overall_r2)
    elif metric_type == "percent_agreement":
        overall = _percent_agreement(overall_r1, overall_r2)
    elif metric_type == "icc":
        try:
            overall = _icc([float(v) for v in overall_r1], [float(v) for v in overall_r2])
        except ValueError:
            overall = None
    else:
        overall = None

    # Cache the result
    try:
        supabase.table("irr_metrics").insert({
            "project_id": str(project_id),
            "form_id": str(form_id),
            "metric_type": metric_type,
            "scope": "overall",
            "value": overall,
            "sample_size": len(paired_docs),
            "metadata": {"by_field": {k: v for k, v in field_metrics.items() if v is not None}},
        }).execute()
    except Exception:
        logger.exception("Failed to cache IRR metric")

    return {
        "overall": overall,
        "by_field": field_metrics,
        "sample_size": len(paired_docs),
        "metric_type": metric_type,
    }


async def get_cached_metrics(
    project_id: UUID,
    form_id: UUID,
) -> List[Dict[str, Any]]:
    """Get cached IRR metrics."""
    supabase = get_supabase()
    result = supabase.table("irr_metrics")\
        .select("*")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .order("computed_at", desc=True)\
        .limit(10)\
        .execute()
    return result.data or []
