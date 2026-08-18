"""LLM usage & cost endpoints.

Reads from `llm_history` (populated by utils/logging.py:log_all_lm_histories
for DSPy extraction and utils/langchain_cost_callback.py for LangChain codegen).

Codegen rows have cost=0 because LangChain doesn't compute USD; this endpoint
fills in cost via config/pricing.py at query time. Aggregations are over the
full table — there's no per-project filter yet (llm_history has no project_id).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from supabase import create_client

from utils.llm_call_labels import classify_step, label_run_calls, signature_field
from utils.table_schema import DISCOVER_THEN_FILL, field_key_columns, field_strategy
from app.config import settings
from app.dependencies import get_current_user
from config.pricing import compute_cost, is_priced, is_row_priced

logger = logging.getLogger(__name__)
router = APIRouter()

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


# Signature/step/paper derivation lives in utils/llm_call_labels.py: the flush in
# utils/logging.py stamps those labels into `llm_history.metadata` at write time,
# and this endpoint re-derives what it can for rows written before that existed.
# One implementation, two callers — the previous copy here could drift from the
# writer's idea of what a call was.
def _parse_signature(messages: Any, source_file: Optional[str]) -> Optional[str]:
    return signature_field(messages, source_file)


def _call_labels(row: Dict[str, Any]) -> Dict[str, Any]:
    """Labels for one call: from `metadata` when present, else derived on the fly.

    New rows carry paper/step/attempt/duration written at flush time. Legacy rows
    (every row before this shipped) have `{}` and no way to recover the paper, so
    they get the step and signature only — which is still more than the flat list
    they show today.
    """
    meta = row.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    if meta.get("step"):
        return meta

    messages = row.get("messages")
    derived: Dict[str, Any] = dict(meta)
    if messages:
        derived.setdefault("step", classify_step(messages))
        field = signature_field(messages, row.get("source_file"))
        if field:
            derived.setdefault("field_name", field)
    return derived


def _row_cost(row: Dict[str, Any]) -> float:
    """Return cost — use stored value if present, otherwise compute from tokens.

    Fallback compute path includes Anthropic prompt-cache token costs so that
    cached calls aren't over-estimated when LiteLLM didn't price them inline.
    """
    stored = float(row.get("cost") or 0)
    if stored > 0:
        return stored
    return compute_cost(
        row.get("model") or "",
        int(row.get("prompt_tokens") or 0),
        int(row.get("completion_tokens") or 0),
        int(row.get("cache_creation_input_tokens") or 0),
        int(row.get("cache_read_input_tokens") or 0),
    )


def _paginated(query_builder, page_size: int = 1000, safety_cap: int = 200_000) -> List[Dict[str, Any]]:
    """Page through a Supabase query until exhausted.

    PostgREST caps any single response at `db-max-rows` (default 1000) regardless
    of `.limit()`, so we need explicit `.range()` pagination to read past it. The
    supabase-py client mutates its query object in place, so `query_builder` must
    be a callable that returns a *fresh* query each page.
    """
    out: List[Dict[str, Any]] = []
    start = 0
    while True:
        try:
            resp = query_builder().range(start, start + page_size - 1).execute()
        except Exception as e:
            logger.error(f"_paginated page at offset {start} failed: {e}")
            break
        chunk = resp.data or []
        out.extend(chunk)
        if len(chunk) < page_size:
            break
        start += page_size
        if start >= safety_cap:
            logger.warning(f"_paginated: hit safety cap at {safety_cap} rows")
            break
    return out


def _fetch_rows(days: int) -> List[Dict[str, Any]]:
    """Pull all llm_history rows within the lookback window (paginated)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _paginated(
        lambda: (
            supabase.table("llm_history")
            .select("extraction_id,job_id,model,cost,prompt_tokens,completion_tokens,total_tokens,cache_creation_input_tokens,cache_read_input_tokens,cache_hit,source_file,schema_name,created_at,metadata")
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
        )
    )


@router.get("/summary")
async def get_usage_summary(
    days: int = Query(30, ge=1, le=365),
    user_id: UUID = Depends(get_current_user),
):
    """Aggregate totals over the last N days."""
    rows = _fetch_rows(days)

    total_calls = len(rows)
    total_prompt = sum(int(r.get("prompt_tokens") or 0) for r in rows)
    total_completion = sum(int(r.get("completion_tokens") or 0) for r in rows)
    total_tokens = sum(int(r.get("total_tokens") or 0) for r in rows)
    total_cost = sum(_row_cost(r) for r in rows)
    total_cache_creation = sum(int(r.get("cache_creation_input_tokens") or 0) for r in rows)
    total_cache_read = sum(int(r.get("cache_read_input_tokens") or 0) for r in rows)
    # Token-based hit rate is the right metric for Anthropic prompt caching:
    # what fraction of input bytes were served from cache vs paid full price.
    # The legacy boolean `cache_hit` column doesn't track Anthropic caching, so
    # we derive the rate from token counts instead.
    cache_denom = total_prompt + total_cache_read + total_cache_creation
    cache_rate = (total_cache_read / cache_denom * 100) if cache_denom else 0.0
    unpriced = sum(1 for r in rows if not is_row_priced(r))

    # Cache savings: what we would have paid if cache_read tokens had been
    # billed as fresh input instead of at the 0.1x cache-read rate. Per-row
    # because input rates differ by model.
    cache_savings = 0.0
    for r in rows:
        read_tokens = int(r.get("cache_read_input_tokens") or 0)
        if not read_tokens:
            continue
        full_price = compute_cost(r.get("model") or "", read_tokens, 0)
        cached_price = compute_cost(r.get("model") or "", 0, 0, 0, read_tokens)
        cache_savings += full_price - cached_price

    return {
        "window_days": days,
        "total_calls": total_calls,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_tokens,
        "total_cache_creation_input_tokens": total_cache_creation,
        "total_cache_read_input_tokens": total_cache_read,
        "total_cost_usd": round(total_cost, 4),
        "cache_savings_usd": round(cache_savings, 4),
        "cache_hit_rate_pct": round(cache_rate, 1),
        "unpriced_calls": unpriced,
    }


@router.get("/breakdown")
async def get_usage_breakdown(
    group_by: str = Query("model", pattern="^(model|source|day|schema)$"),
    days: int = Query(30, ge=1, le=365),
    user_id: UUID = Depends(get_current_user),
):
    """Aggregate rows grouped by model / source_file prefix / day / schema_name."""
    rows = _fetch_rows(days)

    def _key(r: Dict[str, Any]) -> str:
        if group_by == "model":
            return r.get("model") or "(unknown)"
        if group_by == "source":
            src = r.get("source_file") or "(none)"
            # collapse to prefix: "extraction:foo" / "codegen:decompose" → keep
            return src.split(":", 1)[0] if ":" in src else src
        if group_by == "day":
            ts = r.get("created_at") or ""
            return ts[:10] if ts else "(unknown)"
        return r.get("schema_name") or "(none)"

    buckets: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "cache_hits": 0}
    )

    for r in rows:
        k = _key(r)
        b = buckets[k]
        b["calls"] += 1
        b["prompt_tokens"] += int(r.get("prompt_tokens") or 0)
        b["completion_tokens"] += int(r.get("completion_tokens") or 0)
        b["total_tokens"] += int(r.get("total_tokens") or 0)
        b["cost_usd"] += _row_cost(r)
        if r.get("cache_hit"):
            b["cache_hits"] += 1

    out = []
    for k, b in buckets.items():
        b["cost_usd"] = round(b["cost_usd"], 4)
        b["cache_hit_rate_pct"] = round((b["cache_hits"] / b["calls"] * 100), 1) if b["calls"] else 0.0
        out.append({"key": k, **b})

    sort_key = "cost_usd" if group_by != "day" else "key"
    out.sort(key=lambda x: x[sort_key], reverse=(group_by != "day"))

    return {"group_by": group_by, "window_days": days, "rows": out}


@router.get("/by-run")
async def get_usage_by_run(
    days: int = Query(30, ge=1, le=365),
    user_id: UUID = Depends(get_current_user),
):
    """Per-extraction-run usage.

    Bridges llm_history rows to extractions by (schema_name, time window): each
    extraction defines an interval [created_at, next_extraction_of_same_schema)
    that captures all LLM calls made during it.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # 1. All extractions in window
    try:
        ext_resp = (
            supabase.table("extractions")
            .select("id,form_id,project_id,created_at")
            .gte("created_at", cutoff)
            .order("created_at", desc=False)
            .execute()
        )
        extractions = ext_resp.data or []
    except Exception as e:
        logger.error(f"extractions fetch failed: {e}")
        extractions = []

    if not extractions:
        return {"window_days": days, "rows": []}

    # 2. Forms + projects maps
    form_ids = list({e["form_id"] for e in extractions if e.get("form_id")})
    project_ids = list({e["project_id"] for e in extractions if e.get("project_id")})

    forms_map: Dict[str, Dict[str, Any]] = {}
    if form_ids:
        try:
            forms_resp = (
                supabase.table("forms")
                .select("id,form_name,schema_name,project_id,schema_def")
                .in_("id", form_ids)
                .execute()
            )
            for f in (forms_resp.data or []):
                forms_map[f["id"]] = f
        except Exception as e:
            logger.error(f"forms map failed: {e}")

    def _has_table_field(schema_def: Any) -> bool:
        """A form is 'table-typed' (expensive two-stage) if any signature has
        a row_then_columns output field with anchor columns."""
        if isinstance(schema_def, str):
            try:
                import json as _json
                schema_def = _json.loads(schema_def)
            except Exception:
                return False
        if not isinstance(schema_def, dict):
            return False
        for sig in (schema_def.get("signatures") or []):
            for of in (sig.get("output_fields") or []):
                if (
                    isinstance(of, dict)
                    and field_strategy(of) == DISCOVER_THEN_FILL
                    and field_key_columns(of)
                ):
                    return True
        return False

    project_name_map: Dict[str, str] = {}
    if project_ids:
        try:
            proj_resp = (
                supabase.table("projects")
                .select("id,name")
                .in_("id", project_ids)
                .execute()
            )
            for p in (proj_resp.data or []):
                project_name_map[p["id"]] = p.get("name") or "(unnamed)"
        except Exception as e:
            logger.error(f"projects map failed: {e}")

    # 3. llm_history rows in window
    history_rows = _fetch_rows(days)

    # 4. Build per-schema sorted timeline; assign each call to the latest extraction
    #    of the same schema whose created_at <= call.created_at.
    by_schema_extractions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in extractions:
        form = forms_map.get(e.get("form_id") or "")
        if not form or not form.get("schema_name"):
            continue
        by_schema_extractions[form["schema_name"]].append(e)
    for lst in by_schema_extractions.values():
        lst.sort(key=lambda x: x["created_at"])

    # Set of extraction_ids in this window — used to validate the direct foreign
    # key on llm_history before trusting it (a stale id outside the window would
    # otherwise drop the row from per-run aggregates).
    valid_ext_ids = {e["id"] for e in extractions}

    # job_id → extraction_id. This is the exact link, and the reason it goes via
    # `jobs` rather than `llm_history.extraction_id`: that column's FK references
    # `extraction_results(id)`, so the run's own id can never be stored in it
    # (insert fails with 23503) — which is why it is NULL in every row.
    job_to_ext: Dict[str, str] = {}
    try:
        job_rows = _paginated(
            lambda: (
                supabase.table("jobs")
                .select("id,input_data")
                .eq("job_type", "extraction")
                .gte("created_at", cutoff)
            )
        )
        for j in job_rows:
            eid = (j.get("input_data") or {}).get("extraction_id")
            if eid:
                job_to_ext[j["id"]] = str(eid)
    except Exception as e:
        logger.warning(f"job→extraction map failed, falling back to time windows: {e}")

    def _assign_extraction(call_row: Dict[str, Any]) -> Optional[str]:
        # Exact: the job that made the call knows its extraction.
        via_job = job_to_ext.get(call_row.get("job_id") or "")
        if via_job and via_job in valid_ext_ids:
            return via_job
        # Legacy rows carried the id nowhere else; kept in case the column is
        # ever repointed at `extractions`.
        direct = call_row.get("extraction_id")
        if direct and direct in valid_ext_ids:
            return direct

        # Fallback for legacy rows written before extraction_id was populated:
        # match by schema_name + latest extraction.created_at <= call.created_at.
        sn = call_row.get("schema_name")
        ts = call_row.get("created_at") or ""
        if not sn or not ts:
            return None
        candidates = by_schema_extractions.get(sn, [])
        if not candidates:
            return None
        chosen = None
        for ext in candidates:
            if ext["created_at"] <= ts:
                chosen = ext
            else:
                break
        return chosen["id"] if chosen else None

    # 5. Aggregate per extraction_id
    agg: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "cost_usd": 0.0, "models": set(),
            # From the write-time labels (utils/llm_call_labels.py); zero for rows
            # written before labelling shipped.
            "model_time_ms": 0.0, "wasted_calls": 0, "wasted_cost_usd": 0.0,
            "last_call_at": "",
        }
    )
    for r in history_rows:
        eid = _assign_extraction(r)
        if not eid:
            continue
        b = agg[eid]
        b["calls"] += 1
        b["prompt_tokens"] += int(r.get("prompt_tokens") or 0)
        b["completion_tokens"] += int(r.get("completion_tokens") or 0)
        b["total_tokens"] += int(r.get("total_tokens") or 0)
        b["cost_usd"] += _row_cost(r)
        if r.get("model"):
            b["models"].add(r["model"])
        meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        _ms = meta.get("duration_ms")
        if isinstance(_ms, (int, float)) and not isinstance(_ms, bool):
            b["model_time_ms"] += float(_ms)
        if meta.get("superseded"):
            b["wasted_calls"] += 1
            b["wasted_cost_usd"] += _row_cost(r)
        _ts = r.get("created_at") or ""
        if _ts > b["last_call_at"]:
            b["last_call_at"] = _ts

    # 6. extraction_results → pdf_count per extraction (count rows = paper instances in this run)
    ext_ids = [e["id"] for e in extractions]
    pdf_count_by_ext: Dict[str, int] = defaultdict(int)
    if ext_ids:
        res_rows = _paginated(
            lambda: (
                supabase.table("extraction_results")
                .select("extraction_id")
                .in_("extraction_id", ext_ids)
            )
        )
        for r in res_rows:
            pdf_count_by_ext[r["extraction_id"]] += 1

    # 7. Assemble output
    out = []
    for e in extractions:
        eid = e["id"]
        form = forms_map.get(e.get("form_id") or "")
        a = agg.get(eid)
        pdf_count = pdf_count_by_ext.get(eid, 0)
        total_tokens = int(a["total_tokens"]) if a else 0
        avg = round(total_tokens / pdf_count, 1) if pdf_count else None

        # Wall clock: run start → last recorded call. Deliberately separate from
        # model_time (the sum of call durations), which is larger because papers
        # run in parallel — reporting one as the other would misstate both.
        elapsed = None
        _last = (a or {}).get("last_call_at") or ""
        if _last and e.get("created_at"):
            try:
                elapsed = max(0.0, round(
                    (datetime.fromisoformat(_last.replace("Z", "+00:00"))
                     - datetime.fromisoformat(str(e["created_at"]).replace("Z", "+00:00"))
                     ).total_seconds(), 1))
            except (ValueError, TypeError):
                elapsed = None

        out.append({
            "extraction_id": eid,
            "started_at": e.get("created_at"),
            "elapsed_seconds": elapsed,
            "model_time_seconds": round((a or {}).get("model_time_ms", 0.0) / 1000.0, 1) or None,
            "wasted_calls": int((a or {}).get("wasted_calls", 0)),
            "wasted_cost_usd": round((a or {}).get("wasted_cost_usd", 0.0), 4),
            "project_id": e.get("project_id"),
            "project_name": project_name_map.get(e.get("project_id") or "") or "(unmatched)",
            "form_id": e.get("form_id"),
            "form_name": (form or {}).get("form_name") or "(unmatched)",
            "schema_name": (form or {}).get("schema_name"),
            "has_table_field": _has_table_field((form or {}).get("schema_def")),
            "pdf_count": pdf_count,
            "calls": (a or {}).get("calls", 0),
            "prompt_tokens": int((a or {}).get("prompt_tokens", 0)),
            "completion_tokens": int((a or {}).get("completion_tokens", 0)),
            "total_tokens": total_tokens,
            "cost_usd": round((a or {}).get("cost_usd", 0.0), 4),
            "avg_tokens_per_pdf": avg,
            "models": sorted((a or {}).get("models", [])),
        })

    out.sort(key=lambda x: x["started_at"] or "", reverse=True)
    return {"window_days": days, "rows": out}


@router.get("/by-form")
async def get_usage_by_form(
    days: int = Query(30, ge=1, le=365),
    user_id: UUID = Depends(get_current_user),
):
    """Per-(project, form) usage joining llm_history → forms → projects → extractions → extraction_results."""
    rows = _fetch_rows(days)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Group llm_history rows by schema_name (= task_name).
    by_schema: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "models": set()}
    )
    for r in rows:
        sn = r.get("schema_name")
        if not sn:
            continue
        b = by_schema[sn]
        b["calls"] += 1
        b["prompt_tokens"] += int(r.get("prompt_tokens") or 0)
        b["completion_tokens"] += int(r.get("completion_tokens") or 0)
        b["total_tokens"] += int(r.get("total_tokens") or 0)
        b["cost_usd"] += _row_cost(r)
        if r.get("model"):
            b["models"].add(r["model"])

    if not by_schema:
        return {"window_days": days, "rows": []}

    schema_names = list(by_schema.keys())

    # Forms: schema_name → form_id, form_name, project_id
    try:
        forms_resp = (
            supabase.table("forms")
            .select("id,form_name,project_id,schema_name")
            .in_("schema_name", schema_names)
            .execute()
        )
        forms_data = forms_resp.data or []
    except Exception as e:
        logger.error(f"forms join failed: {e}")
        forms_data = []

    form_by_schema = {f["schema_name"]: f for f in forms_data if f.get("schema_name")}
    project_ids = list({f["project_id"] for f in forms_data if f.get("project_id")})
    form_ids = [f["id"] for f in forms_data]

    # Projects: id → name
    project_name_map: Dict[str, str] = {}
    if project_ids:
        try:
            proj_resp = (
                supabase.table("projects")
                .select("id,name")
                .in_("id", project_ids)
                .execute()
            )
            for p in (proj_resp.data or []):
                project_name_map[p["id"]] = p.get("name") or "(unnamed)"
        except Exception as e:
            logger.error(f"projects join failed: {e}")

    # Extractions per form (within window) → extraction ids
    ext_ids_by_form: Dict[str, List[str]] = defaultdict(list)
    if form_ids:
        try:
            ext_resp = (
                supabase.table("extractions")
                .select("id,form_id,created_at")
                .in_("form_id", form_ids)
                .gte("created_at", cutoff)
                .execute()
            )
            for e in (ext_resp.data or []):
                ext_ids_by_form[e["form_id"]].append(e["id"])
        except Exception as e:
            logger.error(f"extractions join failed: {e}")

    # extraction_results → count paper×run instances per form
    # (one row per (document × extraction run); same paper run twice counts twice)
    pdf_runs_by_form: Dict[str, int] = {}
    unique_pdfs_by_form: Dict[str, int] = {}
    all_ext_ids = [eid for ids in ext_ids_by_form.values() for eid in ids]
    if all_ext_ids:
        res_rows = _paginated(
            lambda: (
                supabase.table("extraction_results")
                .select("extraction_id,document_id")
                .in_("extraction_id", all_ext_ids)
            )
        )
        ext_to_form = {eid: fid for fid, ids in ext_ids_by_form.items() for eid in ids}
        runs_by_form: Dict[str, int] = defaultdict(int)
        unique_docs_by_form: Dict[str, set] = defaultdict(set)
        for r in res_rows:
            fid = ext_to_form.get(r["extraction_id"])
            if fid:
                runs_by_form[fid] += 1
                if r.get("document_id"):
                    unique_docs_by_form[fid].add(r["document_id"])
        pdf_runs_by_form = dict(runs_by_form)
        unique_pdfs_by_form = {fid: len(docs) for fid, docs in unique_docs_by_form.items()}

    # Assemble output
    out = []
    for sn, agg in by_schema.items():
        form = form_by_schema.get(sn)
        form_id = form["id"] if form else None
        form_name = form["form_name"] if form else None
        project_id = form["project_id"] if form else None
        project_name = project_name_map.get(project_id) if project_id else None
        runs = len(ext_ids_by_form.get(form_id, [])) if form_id else 0
        pdf_runs = pdf_runs_by_form.get(form_id, 0) if form_id else 0
        unique_pdfs = unique_pdfs_by_form.get(form_id, 0) if form_id else 0
        total_tokens = int(agg["total_tokens"])
        avg_tokens_per_pdf_run = round(total_tokens / pdf_runs, 1) if pdf_runs else None
        avg_pdfs_per_run = round(pdf_runs / runs, 1) if runs else None

        out.append({
            "project_id": project_id,
            "project_name": project_name or "(unmatched)",
            "form_id": form_id,
            "form_name": form_name or "(unmatched)",
            "schema_name": sn,
            "calls": agg["calls"],
            "prompt_tokens": int(agg["prompt_tokens"]),
            "completion_tokens": int(agg["completion_tokens"]),
            "total_tokens": total_tokens,
            "cost_usd": round(agg["cost_usd"], 4),
            "runs": runs,
            "pdf_runs": pdf_runs,
            "unique_pdfs": unique_pdfs,
            "avg_pdfs_per_run": avg_pdfs_per_run,
            "avg_tokens_per_pdf_run": avg_tokens_per_pdf_run,
            "models": sorted(agg["models"]),
        })

    out.sort(key=lambda x: x["cost_usd"], reverse=True)
    return {"window_days": days, "rows": out}


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


@router.get("/by-project")
async def get_usage_by_project(
    days: int = Query(365, ge=1, le=365),
    project_id: Optional[str] = Query(None, description="Filter to one project; omit for all projects"),
    user_id: UUID = Depends(get_current_user),
):
    """Per-project cost & time rollup for AI extraction.

    Cost/tokens come from `llm_history`; runs come from the `jobs` table (one
    extraction job = one run). New rows carry `job_id` for EXACT per-run
    attribution; legacy rows (job_id NULL) still roll up to the right project
    via schema_name -> form -> project, but can't be split per-run (flagged
    `cost_exact=false` on those runs).

    Policy (locked with product):
      - Re-runs: every run's real spend is summed; `unique_pdfs` reported alongside.
      - Codegen (form building) is a SEPARATE line, not folded into extraction.
      - Failed / cancelled / retried runs count (real spend) and carry their status.
      - Per-model uses the ACTUAL model billed (llm_history.model), incl. fallback.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # 1. Extraction jobs in window (= runs)
    def _jobs_q():
        q = (
            supabase.table("jobs")
            .select("id,project_id,status,started_at,completed_at,created_at,input_data")
            .eq("job_type", "extraction")
            .gte("created_at", cutoff)
        )
        if project_id:
            q = q.eq("project_id", project_id)
        return q
    jobs = _paginated(_jobs_q)
    job_map = {j["id"]: j for j in jobs}

    # Codegen ("form building") jobs — one-time cost, attributed via job_id.
    # New codegen rows carry job_id; their schema_name is a FIELD name, so the
    # schema->form fallback can't attribute them — job_id is the only link.
    def _cg_q():
        q = (
            supabase.table("jobs")
            .select("id,project_id,input_data")
            .eq("job_type", "form_generation")
            .gte("created_at", cutoff)
        )
        if project_id:
            q = q.eq("project_id", project_id)
        return q
    codegen_job_map = {j["id"]: j for j in _paginated(_cg_q)}

    def _job_form_id(j: Dict[str, Any]) -> Optional[str]:
        return (j.get("input_data") or {}).get("form_id")

    # 2. llm_history in window (with job_id + source_file for attribution)
    hist = _paginated(
        lambda: (
            supabase.table("llm_history")
            .select("job_id,model,cost,prompt_tokens,completion_tokens,total_tokens,cache_creation_input_tokens,cache_read_input_tokens,source_file,schema_name,created_at")
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
        )
    )

    # 3. AI extraction_results in window — pdf counts & unique docs (exclude manual/consensus)
    def _res_q():
        q = (
            supabase.table("extraction_results")
            .select("job_id,project_id,form_id,document_id,extraction_type")
            .eq("extraction_type", "ai")
            .gte("created_at", cutoff)
        )
        if project_id:
            q = q.eq("project_id", project_id)
        return q
    results = _paginated(_res_q)

    # 4. Forms + projects lookup maps
    forms = _paginated(lambda: supabase.table("forms").select("id,form_name,project_id,schema_name"))
    form_by_id = {f["id"]: f for f in forms}
    form_by_schema = {f["schema_name"]: f for f in forms if f.get("schema_name")}
    projects = _paginated(lambda: supabase.table("projects").select("id,name"))
    project_name = {p["id"]: (p.get("name") or "(unnamed)") for p in projects}

    def _attribute(row: Dict[str, Any]):
        """Return (project_id, form_id, is_codegen) for an llm_history row."""
        is_codegen = (row.get("source_file") or "").startswith("codegen")
        jid = row.get("job_id")
        if jid and jid in job_map:
            j = job_map[jid]
            return j.get("project_id"), _job_form_id(j), is_codegen
        if jid and jid in codegen_job_map:
            j = codegen_job_map[jid]
            return j.get("project_id"), _job_form_id(j), is_codegen
        # Legacy extraction rows: schema_name -> form -> project. Legacy codegen
        # rows can't be attributed (schema_name is a field name) and fall through
        # -> unattributed (building cost $0 for forms built before this shipped).
        f = form_by_schema.get(row.get("schema_name") or "")
        if f:
            return f.get("project_id"), f.get("id"), is_codegen
        return None, None, is_codegen

    # ── Accumulators ──────────────────────────────────────────────────────
    def _new_proj():
        return {
            "extraction_cost_usd": 0.0, "codegen_cost_usd": 0.0,
            "total_tokens": 0,
            "models": defaultdict(lambda: {"cost_usd": 0.0, "tokens": 0, "calls": 0}),
            "forms": defaultdict(lambda: {"cost_usd": 0.0, "codegen_cost_usd": 0.0, "tokens": 0}),
        }
    proj: Dict[str, Dict[str, Any]] = defaultdict(_new_proj)
    cost_by_job: Dict[str, float] = defaultdict(float)
    tokens_by_job: Dict[str, int] = defaultdict(int)
    models_by_job: Dict[str, set] = defaultdict(set)

    for r in hist:
        pid, fid, is_codegen = _attribute(r)
        if not pid or (project_id and pid != project_id):
            continue
        cost = _row_cost(r)
        toks = int(r.get("total_tokens") or 0)
        p = proj[pid]
        if is_codegen:
            p["codegen_cost_usd"] += cost
            if fid:
                p["forms"][fid]["codegen_cost_usd"] += cost
        else:
            p["extraction_cost_usd"] += cost
            if fid:
                p["forms"][fid]["cost_usd"] += cost
                p["forms"][fid]["tokens"] += toks
        p["total_tokens"] += toks
        model = r.get("model") or "(unknown)"
        m = p["models"][model]
        m["cost_usd"] += cost
        m["tokens"] += toks
        m["calls"] += 1
        if fid:
            p["forms"][fid]  # ensure form bucket exists
        # exact per-run rollup
        jid = r.get("job_id")
        if jid and jid in job_map and not is_codegen:
            cost_by_job[jid] += cost
            tokens_by_job[jid] += toks
            if r.get("model"):
                models_by_job[jid].add(r["model"])

    # pdf counts / unique docs
    pdf_by_job: Dict[str, int] = defaultdict(int)
    unique_docs_proj: Dict[str, set] = defaultdict(set)
    pdf_runs_proj: Dict[str, int] = defaultdict(int)
    unique_docs_form: Dict[str, set] = defaultdict(set)
    for r in results:
        pid = r.get("project_id")
        if not pid or (project_id and pid != project_id):
            continue
        jid = r.get("job_id")
        if jid:
            pdf_by_job[jid] += 1
        doc = r.get("document_id")
        pdf_runs_proj[pid] += 1
        if doc:
            unique_docs_proj[pid].add(doc)
            if r.get("form_id"):
                unique_docs_form[r["form_id"]].add(doc)

    # ── Build run_list + per-project run stats from jobs ──────────────────
    runs_by_proj: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    duration_by_proj: Dict[str, float] = defaultdict(float)
    status_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for j in jobs:
        pid = j.get("project_id")
        if not pid:
            continue
        jid = j["id"]
        fid = _job_form_id(j)
        form = form_by_id.get(fid or "")
        start = _parse_iso(j.get("started_at"))
        end = _parse_iso(j.get("completed_at"))
        dur = (end - start).total_seconds() if (start and end and end >= start) else None
        if dur is not None:
            duration_by_proj[pid] += dur
        status = j.get("status") or "unknown"
        status_counts[pid][status] += 1
        has_cost_rows = jid in cost_by_job
        runs_by_proj[pid].append({
            "job_id": jid,
            "form_id": fid,
            "form_name": (form or {}).get("form_name") or "(unknown form)",
            "status": status,
            "started_at": j.get("started_at") or j.get("created_at"),
            "completed_at": j.get("completed_at"),
            "duration_seconds": round(dur, 1) if dur is not None else None,
            "pdf_count": pdf_by_job.get(jid, 0),
            "cost_usd": round(cost_by_job.get(jid, 0.0), 4),
            "total_tokens": tokens_by_job.get(jid, 0),
            "models": sorted(models_by_job.get(jid, [])),
            # False for legacy runs (pre job_id) — cost shown is a project-level share, not run-exact
            "cost_exact": has_cost_rows or status in ("pending", "processing"),
        })

    # ── Assemble output rows ──────────────────────────────────────────────
    all_pids = set(proj) | set(runs_by_proj) | set(pdf_runs_proj)
    if project_id:
        all_pids = {p for p in all_pids if p == project_id}

    out = []
    for pid in all_pids:
        p = proj.get(pid) or _new_proj()
        ext_cost = round(p["extraction_cost_usd"], 4)
        code_cost = round(p["codegen_cost_usd"], 4)
        runs = runs_by_proj.get(pid, [])
        runs.sort(key=lambda x: x["started_at"] or "", reverse=True)
        sc = status_counts.get(pid, {})

        models_list = [
            {"model": k, "cost_usd": round(v["cost_usd"], 4), "tokens": v["tokens"], "calls": v["calls"]}
            for k, v in p["models"].items()
        ]
        models_list.sort(key=lambda x: x["cost_usd"], reverse=True)

        forms_list = []
        for fid, fv in p["forms"].items():
            form = form_by_id.get(fid)
            forms_list.append({
                "form_id": fid,
                "form_name": (form or {}).get("form_name") or "(unknown form)",
                "cost_usd": round(fv["cost_usd"], 4),
                "codegen_cost_usd": round(fv["codegen_cost_usd"], 4),
                "total_tokens": fv["tokens"],
                "unique_pdfs": len(unique_docs_form.get(fid, set())),
            })
        forms_list.sort(key=lambda x: x["cost_usd"] + x["codegen_cost_usd"], reverse=True)

        out.append({
            "project_id": pid,
            "project_name": project_name.get(pid) or "(unknown project)",
            "extraction_cost_usd": ext_cost,
            "codegen_cost_usd": code_cost,
            "total_cost_usd": round(ext_cost + code_cost, 4),
            "total_tokens": p["total_tokens"],
            "total_duration_seconds": round(duration_by_proj.get(pid, 0.0), 1),
            "runs": len(runs),
            "successful_runs": sc.get("completed", 0),
            "failed_runs": sc.get("failed", 0),
            "cancelled_runs": sc.get("cancelled", 0),
            "running_runs": sc.get("processing", 0) + sc.get("pending", 0),
            "unique_pdfs": len(unique_docs_proj.get(pid, set())),
            "pdf_runs": pdf_runs_proj.get(pid, 0),
            "models": models_list,
            "forms": forms_list,
            "run_list": runs,
        })

    out.sort(key=lambda x: x["total_cost_usd"], reverse=True)
    return {"window_days": days, "rows": out}


@router.get("/calls")
async def get_usage_calls(
    schema_name: Optional[str] = Query(None),
    extraction_id: Optional[str] = Query(None, description="Exact run filter — preferred over since/until"),
    job_id: Optional[str] = Query(None, description="Exact job filter (pilot/codegen runs have no extraction_id)"),
    source_prefix: Optional[str] = Query(None, description="Filter source_file LIKE 'codegen' or 'extraction'"),
    since: Optional[str] = Query(None, description="ISO timestamp lower bound (inclusive) — legacy rows only"),
    until: Optional[str] = Query(None, description="ISO timestamp upper bound (exclusive)"),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(500, ge=1, le=5000),
    user_id: UUID = Depends(get_current_user),
):
    """Return individual llm_history rows for drill-down (newest first).

    `extraction_id` / `job_id` are exact: they select the calls a run actually
    made. `since`/`until` is the old time-window bridge, kept because rows
    written before `extraction_id` was stamped have no other way to be grouped —
    it silently includes any concurrent run of the same form, which is why it is
    no longer the default.

    `messages` (the full prompt, tens of KB per row) is fetched only for rows
    whose labels were not stamped at write time.
    """
    cutoff = since or (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # An extraction is addressed through its job(s): `llm_history.extraction_id`
    # cannot hold an extraction id (its FK points at `extraction_results`), so
    # `job_id` is the run key that is actually stored. A retry creates a new
    # extraction, so this is normally one job — but read them all rather than
    # assume.
    job_ids: List[str] = [job_id] if job_id else []
    if extraction_id and not job_id:
        try:
            j_resp = (
                supabase.table("jobs")
                .select("id")
                .eq("input_data->>extraction_id", extraction_id)
                .execute()
            )
            job_ids = [j["id"] for j in (j_resp.data or [])]
        except Exception as e:
            logger.warning(f"extraction→job lookup failed for {extraction_id}: {e}")
    exact = bool(job_ids)

    _COLS = (
        "id,call_timestamp,created_at,model,prompt_tokens,completion_tokens,total_tokens,"
        "cache_creation_input_tokens,cache_read_input_tokens,cost,cache_hit,source_file,"
        "schema_name,extraction_id,job_id,metadata"
    )

    def _query(use_exact: bool):
        q = (
            supabase.table("llm_history")
            .select(_COLS)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if use_exact and job_ids:
            q = q.in_("job_id", job_ids)
        else:
            q = q.gte("created_at", cutoff)
            if until:
                q = q.lt("created_at", until)
            if schema_name:
                q = q.eq("schema_name", schema_name)
        if source_prefix:
            q = q.like("source_file", f"{source_prefix}%")
        return q.execute().data or []

    try:
        rows = _query(exact)
        if exact and not rows and (since or schema_name):
            # Runs from before `extraction_id` was stamped cannot be found by it.
            # Fall back to the old window bridge so an old run still opens, and
            # report exact=False so the UI can say the grouping is approximate.
            exact = False
            rows = _query(False)
    except Exception as e:
        logger.error(f"llm_history calls query failed: {e}")
        rows = []

    # Second pass for unlabeled (legacy) rows only — `messages` is the expensive
    # column, so it is never fetched for rows labelled at write time.
    unlabeled_ids = [
        r["id"] for r in rows
        if r.get("id") and not (isinstance(r.get("metadata"), dict) and r["metadata"].get("step"))
    ]
    messages_by_id: Dict[str, Any] = {}
    legacy_labels: Dict[str, Dict[str, Any]] = {}
    if unlabeled_ids:
        try:
            m_resp = (
                supabase.table("llm_history")
                .select("id,messages,assistant_response")
                .in_("id", unlabeled_ids[:500])
                .execute()
            )
            legacy_rows = [m for m in (m_resp.data or []) if m.get("messages")]
            for m in legacy_rows:
                messages_by_id[m["id"]] = m.get("messages")

            # Run the same run-level pass the writer uses. Everything except the
            # paper name and the call duration is recoverable from the stored
            # prompt, so an old run still shows its retries and refills — the two
            # things a reader most wants explained.
            ts_by_id = {r["id"]: (r.get("call_timestamp") or r.get("created_at") or "") for r in rows}
            legacy_rows.sort(key=lambda m: ts_by_id.get(m["id"], ""))
            entries = [
                {
                    "messages": m.get("messages"),
                    "assistant_response": m.get("assistant_response"),
                    "timestamp": ts_by_id.get(m["id"], ""),
                }
                for m in legacy_rows
            ]
            for m, lab in zip(legacy_rows, label_run_calls(entries)):
                legacy_labels[m["id"]] = lab
        except Exception as e:
            logger.warning(f"llm_history legacy labelling failed: {e}")

    # Resolve real document names. The label's own filename is the basename of the
    # markdown path, which is a content hash — fine as provenance, useless to read.
    doc_names: Dict[str, str] = {}
    doc_ids = {
        (r.get("metadata") or {}).get("document_id")
        for r in rows if isinstance(r.get("metadata"), dict)
    }
    doc_ids.discard(None)
    if doc_ids:
        try:
            d_resp = (
                supabase.table("documents")
                .select("id,filename,title")
                .in_("id", list(doc_ids))
                .execute()
            )
            for d in (d_resp.data or []):
                doc_names[d["id"]] = d.get("filename") or d.get("title") or ""
        except Exception as e:
            logger.warning(f"document name lookup failed: {e}")

    # Which fields run the keyed (Rigorous) pipeline in this run. The form's own
    # schema is the authority, because a scalar field's single call and a record
    # discovery that found nothing look identical from the prompt alone — both
    # send only the paper. Guessing "any single call in a run with table fields is
    # record discovery" mislabels every scalar field on a mixed form.
    keyed_fields: set = set()
    if exact:
        try:
            form_id = None
            if extraction_id:
                e_rows = supabase.table("extractions").select("form_id").eq("id", extraction_id).limit(1).execute().data
                form_id = (e_rows or [{}])[0].get("form_id")
            elif job_ids:
                j_rows = supabase.table("jobs").select("input_data").eq("id", job_ids[0]).limit(1).execute().data
                form_id = ((j_rows or [{}])[0].get("input_data") or {}).get("form_id")
            if form_id:
                f_rows = supabase.table("forms").select("schema_def").eq("id", form_id).limit(1).execute().data
                schema_def = (f_rows or [{}])[0].get("schema_def")
                if isinstance(schema_def, str):
                    import json as _json
                    schema_def = _json.loads(schema_def)
                for sig in ((schema_def or {}).get("signatures") or []):
                    for of in (sig.get("output_fields") or []):
                        if isinstance(of, dict) and of.get("name") and field_strategy(of) == DISCOVER_THEN_FILL:
                            keyed_fields.add(of["name"])
        except Exception as e:
            logger.warning(f"keyed-field lookup failed: {e}")

    # Labels first, then counts: "this paper produced only record discovery for
    # this field" is what identifies a paper the pipeline stopped on.
    row_labels: List[Dict[str, Any]] = []
    per_paper_field: Dict[Any, int] = defaultdict(int)
    for r in rows:
        lab = legacy_labels.get(r.get("id")) or _call_labels(r)
        row_labels.append(lab)
        per_paper_field[(lab.get("document_id"), lab.get("field_name"))] += 1

    enriched = []
    for r, labels in zip(rows, row_labels):
        cost = float(r.get("cost") or 0)
        if cost == 0:
            cost = compute_cost(
                r.get("model") or "",
                int(r.get("prompt_tokens") or 0),
                int(r.get("completion_tokens") or 0),
                int(r.get("cache_creation_input_tokens") or 0),
                int(r.get("cache_read_input_tokens") or 0),
            )
        if r.get("id") in messages_by_id:
            r = {**r, "messages": messages_by_id[r["id"]]}

        # A keyed field whose paper produced nothing but the first pass: record
        # discovery came back empty and the pipeline stopped there.
        _step = labels.get("step")
        _field = labels.get("field_name")
        _stopped = False
        if _step == "extract" and _field in keyed_fields:
            _step = "record_discovery"
            _stopped = per_paper_field.get((labels.get("document_id"), _field), 0) <= 1

        # Same formula as /summary's cache_savings_usd: what these input tokens
        # would have cost at the full rate instead of the 0.1x cache-read rate.
        _read = int(r.get("cache_read_input_tokens") or 0)
        _saved = 0.0
        if _read:
            _model = r.get("model") or ""
            _saved = compute_cost(_model, _read, 0) - compute_cost(_model, 0, 0, 0, _read)
        enriched.append({
            "id": r.get("id"),
            "timestamp": r.get("call_timestamp") or r.get("created_at"),
            "model": r.get("model"),
            "prompt_tokens": int(r.get("prompt_tokens") or 0),
            "completion_tokens": int(r.get("completion_tokens") or 0),
            "total_tokens": int(r.get("total_tokens") or 0),
            "cache_creation_input_tokens": int(r.get("cache_creation_input_tokens") or 0),
            "cache_read_input_tokens": int(r.get("cache_read_input_tokens") or 0),
            "cost_usd": round(cost, 6),
            "cache_savings_usd": round(_saved, 6),
            "cache_hit": bool(r.get("cache_hit")),
            "source_file": r.get("source_file"),
            "schema_name": r.get("schema_name"),
            "signature": labels.get("field_name") or _parse_signature(r.get("messages"), r.get("source_file")),
            # What the call was for (empty for pre-labelling rows)
            "step": _step,
            "stopped": _stopped,
            "document_id": labels.get("document_id"),
            "filename": doc_names.get(labels.get("document_id") or "") or labels.get("filename"),
            "field_name": labels.get("field_name"),
            "n_records": labels.get("n_records"),
            "attempt": labels.get("attempt"),
            "attempts_total": labels.get("attempts_total"),
            "superseded": bool(labels.get("superseded")),
            "superseded_reason": labels.get("superseded_reason"),
            "response_shape": labels.get("response_shape"),
            "duration_ms": labels.get("duration_ms"),
            "transport": labels.get("transport"),
            "num_turns": labels.get("num_turns"),
        })
    return {
        "window_days": days,
        "schema_name": schema_name,
        "extraction_id": extraction_id,
        "exact": exact,
        "rows": enriched,
    }
