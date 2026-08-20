"""
The consensus dashboard's per-document summary, as a pure function.

Extracted out of `app/api/v1/results.py:get_consensus_summary` so it can be
tested at all. The endpoint module cannot be imported in a test: it pulls in
`storage_service`, whose module-level singleton calls `sts:GetCallerIdentity` at
import time, so the whole module errors out without real AWS credentials. Every
defect this file fixes was previously unreachable by any test — the endpoint had
none.

`results.py` keeps the I/O and the cache; this holds the arithmetic. Inputs are
the raw query rows, deliberately 1:1 with what Supabase returns, so a test can
hand-write them.

Four things this fixes relative to the inline version it replaces:

  * **Newest row wins per extraction type.** The old loop kept the *first* row it
    saw while ordering `created_at` ascending, so it showed the oldest AI run —
    while the comment claimed "last write wins" and while the review screen, which
    reads `GET /results` ordered `created_at desc`, showed the newest. A document
    re-extracted after a prompt fix reported the pre-fix numbers on the dashboard
    and the post-fix numbers on the review screen.
  * **In-progress drafts are not results.** `_partial` rows were skipped in the
    role path but not here, so saving a half-filled form set `has_manual` and put
    the document into the AI-vs-manual agreement math.
  * **Manual comparison prefers the R1 row.** The old code compared AI against
    whichever manual row happened to be oldest. On a dual-reviewer document that
    could be either reviewer, so the number moved depending on who saved first.
  * **The stored consensus triple is used whole.** Only `agreement_pct` was
    overridden from the reviewed row, leaving `disputed_fields` and `total_fields`
    at their string-match values — one row showing a human percentage next to
    machine conflict counts.
"""

from typing import Any, Dict, Iterable, List, Optional

from utils import value_compare
from utils.study_label import build_label_map

# Roles that count as a reviewer having done the work.
_REVIEWER_ROLES = ("reviewer_1", "reviewer_2")


def _is_partial(extracted: Optional[Dict[str, Any]]) -> bool:
    """An in-progress manual draft, flagged inside the payload by
    `api/v1/results.py` (`extracted_data["_partial"] = True`)."""
    return not extracted or extracted.get("_partial") is True


def _resolve_reviewer_roles(
    role_rows: Iterable[Dict[str, Any]],
    assignment_rows: Iterable[Dict[str, Any]],
) -> tuple:
    """Which documents have a completed R1 / R2 result.

    Swap-aware: when a project owner moves someone from R2 to R1, their saved work
    should count under the role they hold *now*, and work by someone no longer
    assigned to the document at all is stranded and counts for nobody. Preserved
    verbatim from the endpoint — this is subtle and load-bearing.
    """
    current_assignee = {
        (a["document_id"], a["reviewer_role"]): a.get("reviewer_user_id")
        for a in (assignment_rows or [])
    }
    user_current_role: Dict[tuple, str] = {}
    for (doc_id, role), assignee in current_assignee.items():
        if role in _REVIEWER_ROLES and assignee:
            user_current_role[(doc_id, str(assignee))] = role

    docs_with_role_assignments = {
        doc_id for (doc_id, role), assignee in current_assignee.items()
        if role in _REVIEWER_ROLES and assignee
    }

    r1_doc_ids, r2_doc_ids = set(), set()
    for r in (role_rows or []):
        role = r.get("reviewer_role")
        if role not in _REVIEWER_ROLES:
            continue
        if _is_partial(r.get("extracted_data")):
            continue
        doc_id = r["document_id"]
        extracted_by = str(r.get("extracted_by") or "")

        if doc_id in docs_with_role_assignments and extracted_by:
            effective_role = user_current_role.get((doc_id, extracted_by))
            if effective_role is None:
                continue  # person no longer assigned to this doc — stranded row
        else:
            effective_role = role

        (r1_doc_ids if effective_role == "reviewer_1" else r2_doc_ids).add(doc_id)

    return r1_doc_ids, r2_doc_ids


def _group_extractions(extraction_rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """`document_id → {"ai": {...}, "manual": {...}}`, newest row winning.

    Rows must arrive ordered by `created_at` ascending, so plain assignment is
    last-write-wins. Manual rows are bucketed by role first and collapsed to a
    single `manual` afterwards, preferring reviewer_1 over an untagged row, so the
    AI-vs-manual number does not depend on which reviewer saved first.
    """
    staged: Dict[str, Dict[str, Any]] = {}
    for r in (extraction_rows or []):
        doc_id = r["document_id"]
        etype = r.get("extraction_type", "ai")
        extracted = r.get("extracted_data") or {}
        if etype == "consensus":
            continue  # legacy rows — consensus_results is the source of truth now
        bucket = staged.setdefault(doc_id, {})
        if etype == "manual":
            if _is_partial(extracted):
                continue
            role = r.get("reviewer_role")
            key = "manual_r1" if role == "reviewer_1" else (
                "manual_r2" if role == "reviewer_2" else "manual_untagged"
            )
            bucket[key] = extracted
        else:
            bucket["ai"] = value_compare.normalize_ai_keys(extracted)

    out: Dict[str, Dict[str, Any]] = {}
    for doc_id, bucket in staged.items():
        resolved: Dict[str, Any] = {}
        if "ai" in bucket:
            resolved["ai"] = bucket["ai"]
        manual = (
            bucket.get("manual_r1")
            or bucket.get("manual_untagged")
            or bucket.get("manual_r2")
        )
        if manual is not None:
            resolved["manual"] = manual
        out[doc_id] = resolved
    return out


def build_consensus_summary(
    documents: List[Dict[str, Any]],
    extraction_rows: Iterable[Dict[str, Any]],
    role_rows: Iterable[Dict[str, Any]],
    consensus_rows: Iterable[Dict[str, Any]],
    adjudication_rows: Iterable[Dict[str, Any]],
    assignment_rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the `{summary, documents}` payload for the consensus dashboard.

    `documents` sets the output order. `extraction_rows` and `role_rows` must be
    ordered by `created_at` ascending.
    """
    consensus_map = {r["document_id"]: r for r in (consensus_rows or [])}
    adjudication_map = {r["document_id"]: r for r in (adjudication_rows or [])}
    r1_doc_ids, r2_doc_ids = _resolve_reviewer_roles(role_rows, assignment_rows)
    doc_data = _group_extractions(extraction_rows)

    # One label map for the whole project — see app/services/document_labels.py
    # for why this is never computed over a subset.
    labels = build_label_map(documents)

    documents_out = []
    for doc in documents:
        doc_id = doc["id"]
        data = doc_data.get(doc_id, {})
        has_ai = "ai" in data
        has_manual = "manual" in data
        has_consensus = doc_id in consensus_map

        agreement_pct = None
        disputed_fields = None
        total_fields = None

        if has_ai and has_manual:
            ai_vals, manual_vals = data["ai"], data["manual"]
            all_fields = value_compare.comparable_fields(ai_vals, manual_vals)
            total = len(all_fields)
            if total > 0:
                matching = sum(
                    1 for f in all_fields
                    if value_compare.values_agree(ai_vals.get(f), manual_vals.get(f))
                )
                agreement_pct = round(matching / total * 100)
                disputed_fields = total - matching
                total_fields = total

        # A reviewed document reports what the human decided, not what string
        # matching guessed — and all three numbers come from the same place, so
        # the row cannot show a human percentage beside machine conflict counts.
        if has_consensus:
            row = consensus_map[doc_id]
            if row.get("agreement_pct") is not None:
                agreement_pct = row["agreement_pct"]
            if row.get("disputed_count") is not None:
                disputed_fields = row["disputed_count"]
            if row.get("total_fields") is not None:
                total_fields = row["total_fields"]

        adj = adjudication_map.get(doc_id)
        adjudication_status = adj.get("status") if adj else None
        r1_r2_agreement_pct = None
        if adj and adj.get("agreement_pct") is not None:
            r1_r2_agreement_pct = float(adj["agreement_pct"])

        documents_out.append({
            "document_id": doc_id,
            "filename": doc["filename"],
            # What the screen actually prints: "Raslan 2021", not the 200-char
            # article title an EndNote import leaves in `filename`. Computed
            # here rather than in the caller because the a/b/c suffix needs the
            # whole project's documents, which is exactly what `documents` is.
            "study_label": labels.get(doc_id, doc["filename"]),
            "ref_id": doc.get("ref_id"),
            "has_ai": has_ai,
            "has_manual": has_manual,
            "has_consensus": has_consensus,
            "agreement_pct": agreement_pct,
            "disputed_fields": disputed_fields,
            "total_fields": total_fields,
            "has_r1": doc_id in r1_doc_ids,
            "has_r2": doc_id in r2_doc_ids,
            # Deliberately "a row exists", NOT "it is finished". The review screen
            # uses this as the gate for fetching a saved adjudication back into
            # the form (consensus/page.tsx), so narrowing it would silently drop
            # an adjudicator's in-progress resolutions on reload. The two fields
            # below carry the finished-ness instead.
            "has_adjudication": adj is not None,
            "adjudication_status": adjudication_status,
            "has_adjudication_completed": adjudication_status == "completed",
            "r1_r2_agreement_pct": r1_r2_agreement_pct,
        })

    agreements = [d["agreement_pct"] for d in documents_out if d["agreement_pct"] is not None]

    return {
        "summary": {
            "total_docs": len(documents),
            "ai_done": sum(1 for d in documents_out if d["has_ai"]),
            "manual_done": sum(1 for d in documents_out if d["has_manual"]),
            "consensus_done": sum(1 for d in documents_out if d["has_consensus"]),
            "avg_agreement_pct": (
                round(sum(agreements) / len(agreements)) if agreements else None
            ),
            "r1_done": sum(1 for d in documents_out if d["has_r1"]),
            "r2_done": sum(1 for d in documents_out if d["has_r2"]),
            "adjudication_done": sum(1 for d in documents_out if d["has_adjudication"]),
            # Documents actually finished. A dual-reviewer submit writes BOTH a
            # consensus row and an adjudication row, so consensus_done +
            # adjudication_done counts every such document twice — which is what
            # the dashboard's "Consensus" tile was showing. The two counts above
            # are kept because they mean different things on their own.
            "docs_done": sum(
                1 for d in documents_out
                if d["has_consensus"] or d["has_adjudication_completed"]
            ),
        },
        "documents": documents_out,
    }
