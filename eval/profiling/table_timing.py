"""Timing instrumentation for table (keyed) extraction — where the seconds go.

Answers one question: for ONE table field on ONE paper, how long does each
operation of the pipeline take, and how much of the wall clock is LLM latency
versus our own Python?

Why a module and not just notebook cells
----------------------------------------
The instrumentation has to survive `asyncio.gather`. Two sources of truth are
combined per LLM call:

* **our wall clock** — `time.perf_counter()` around the awaited call, attributed
  to the enclosing operation through a `contextvars` span stack. Tasks created by
  `gather` inherit the context at creation, so a span opened before the fan-out
  is visible inside every branch, and a span opened inside a branch stays local
  to it. That is what makes an overlap-aware waterfall possible.
* **litellm's own `_response_ms`** — already stamped on every response object and
  kept in DSPy's history (see `utils/llm_call_labels.duration_ms`). It excludes
  our queueing/parse time, so `wall_ms - api_ms` is the honest overhead figure.

Steps are named per the Aug 2026 terminology in CLAUDE.md — record discovery,
recall audit, slot filling, refill — and are read from the SIGNATURE CLASS NAME
(`…RecordDiscovery` / `…RecallAudit` / `…RowSlotFill` / `…SetSlotFill`), which is
how `runtime_builders` names them, not from call ordering. `classify_step` from
`utils/llm_call_labels` is run over the same prompts as a cross-check, so a
mismatch between this profiler and production cost telemetry is visible instead
of silent.

Nothing here writes to the database or mutates a form. The patches are installed
inside a context manager and removed on exit.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import re
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path("/home/ubuntu/evistream")
BACKEND_ROOT = REPO_ROOT / "backend"
PROFILING_DIR = BACKEND_ROOT / "eval" / "profiling"
OUTPUTS_DIR = PROFILING_DIR / "outputs"

logger = logging.getLogger(__name__)


# ── bootstrap ──────────────────────────────────────────────────────────────

def bootstrap(
    secret_name: str = "evistream/production",
    disable_dspy_cache: bool = True,
    quiet_http: bool = True,
) -> Dict[str, Any]:
    """Put the backend on sys.path, load prod secrets, configure DSPy.

    Order matters: secrets must be in `os.environ` before anything that reads
    `config.models` at import time, so this loads them before importing DSPy
    wiring. Returns a dict describing the runtime that will be measured — model,
    adapter, token ceilings, and the two env flags that change the shape of the
    pipeline (`EXTRACTION_BATCH_VALUES`, `EXTRACTION_PROMPT_CACHE`).

    `disable_dspy_cache` defaults to True: DSPy caches LM responses, and a cached
    response returns in microseconds, which would make a re-run of the same paper
    look ~1000x faster than production. Timing a cache hit is not timing the
    pipeline.
    """
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    os.environ.setdefault("AWS_SECRETS_NAME", secret_name)
    from utils.secrets_loader import load_secrets
    load_secrets()

    if quiet_http:
        for noisy in ("httpx", "httpcore", "botocore", "boto3", "urllib3", "LiteLLM"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    import dspy
    from utils.lm_config import get_dspy_model
    from config.models import (
        DEFAULT_MODEL, MAX_TOKENS, EXTRACTION_PROMPT_CACHE,
    )

    lm = get_dspy_model()
    # Production never calls dspy.configure(lm=...) — ModelRouter uses
    # dspy.context() per coroutine. Configure it here anyway so a bare
    # (router-disabled) run still has an LM.
    dspy.configure(lm=lm)

    cache_state = "left as-is"
    if disable_dspy_cache:
        try:
            dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)
            cache_state = "disabled (disk+memory)"
        except Exception:
            try:
                lm.cache = False
                cache_state = "disabled on LM instance"
            except Exception as exc:  # pragma: no cover
                cache_state = f"could not disable: {exc}"

    # Read the flag from the module that acts on it, not from os.environ with a
    # guessed default: `EXTRACTION_BATCH_VALUES` defaults to "1" in
    # runtime_builders, so reporting an absent env var as "0" would describe the
    # opposite pipeline shape from the one about to be measured.
    from dspy_components import runtime_builders as _rb

    info = {
        "model": getattr(lm, "model", DEFAULT_MODEL),
        "max_tokens": getattr(lm, "kwargs", {}).get("max_tokens", MAX_TOKENS),
        "adapter": type(dspy.settings.adapter).__name__ if dspy.settings.adapter else "None",
        "dspy_response_cache": cache_state,
        "EXTRACTION_PROMPT_CACHE": bool(EXTRACTION_PROMPT_CACHE),
        "slot_filling": "set-at-a-time" if _rb._SET_AT_A_TIME else "row-at-a-time",
        "EXTRACTION_BATCH_VALUES": os.environ.get("EXTRACTION_BATCH_VALUES", "(unset → 1)"),
        "supabase": bool(os.environ.get("SUPABASE_URL")),
        "dspy_version": dspy.__version__,
    }
    return info


def supabase():
    """The prod Supabase client (service role)."""
    from utils.supabase_client import get_supabase_client
    client = get_supabase_client()
    if client is None or not client.is_available():
        raise RuntimeError("Supabase unavailable — did bootstrap() load secrets?")
    return client.client


# ── prod pickers: projects → forms → table fields → documents ─────────────

def list_projects(limit: int = 200) -> "Any":
    import pandas as pd
    sb = supabase()
    rows = (
        sb.table("projects")
        .select("id,name,created_at,archived_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["archived"] = df.get("archived_at").notna() if "archived_at" in df else False
        df = df[["id", "name", "created_at", "archived"]]
    return df


def list_table_forms(
    project_id: Optional[str] = None,
    statuses: Sequence[str] = ("active",),
    include_single_pass: bool = True,
) -> "Any":
    """Every table field on prod forms, with the strategy that will actually run.

    `keyed_pipeline` mirrors the activation test in
    `runtime_builders.build_schema_classes` exactly — single-output signature +
    resolved strategy `discover_then_fill` + a composite key + subform fields.
    A field that fails any of those runs single-pass no matter what the UI shows,
    and would be profiled as one call, so the column is the thing to read first.
    """
    import pandas as pd
    from utils.table_schema import (
        AGENTIC, DISCOVER_THEN_FILL, field_key_columns, field_strategy, resolve_strategy,
    )

    sb = supabase()
    q = sb.table("forms").select(
        "id,form_name,project_id,status,schema_name,schema_def,updated_at"
    )
    if project_id:
        q = q.eq("project_id", project_id)
    if statuses:
        q = q.in_("status", list(statuses))
    forms = q.execute().data or []

    proj_names: Dict[str, str] = {}
    pids = sorted({f["project_id"] for f in forms if f.get("project_id")})
    for i in range(0, len(pids), 50):
        chunk = pids[i:i + 50]
        for p in (sb.table("projects").select("id,name").in_("id", chunk).execute().data or []):
            proj_names[p["id"]] = p["name"]

    rows: List[Dict[str, Any]] = []
    for f in forms:
        sd = f.get("schema_def") or {}
        form_mode = resolve_strategy(sd.get("table_extraction_mode"))
        for sig in sd.get("signatures", []) or []:
            outs = sig.get("output_fields", []) or []
            for of in outs:
                subs = of.get("subform_fields") or []
                if not subs:
                    continue
                strategy = field_strategy(of)
                key_cols = field_key_columns(of)
                single_output = len(outs) == 1
                effective = strategy or form_mode
                keyed = bool(
                    single_output
                    and strategy == DISCOVER_THEN_FILL
                    and key_cols
                    and subs
                )
                agentic = bool(
                    single_output and (
                        strategy == AGENTIC or (not strategy and form_mode == AGENTIC)
                    )
                )
                if not include_single_pass and not keyed:
                    continue
                rows.append({
                    "project": proj_names.get(f.get("project_id"), ""),
                    "project_id": f.get("project_id"),
                    "form_id": f["id"],
                    "form_name": f.get("form_name"),
                    "schema_name": f.get("schema_name"),
                    "sig_class": sig.get("class_name"),
                    "field": of.get("name"),
                    "strategy_raw": of.get("extraction_strategy"),
                    "strategy": effective or "unset→single_pass",
                    "runs_as": "keyed" if keyed else ("agentic" if agentic else "single_pass"),
                    "keyed_pipeline": keyed,
                    "n_cols": len(subs),
                    "n_key": len(key_cols),
                    "n_attr": len(subs) - len(key_cols),
                    "key_columns": ", ".join(key_cols),
                    "single_output_sig": single_output,
                    "updated_at": f.get("updated_at"),
                })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["keyed_pipeline", "n_attr"], ascending=[False, False])
    return df.reset_index(drop=True)


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def match_project(forms: "Any", name: Optional[str]) -> "Any":
    """Filter the form table by project name — exact match wins, else substring.

    Exact-first matters here: prod has both `Ibuprofen` and `Ibuprofen (Testing)`,
    and a plain substring filter silently spans the two, which then makes a form
    name ambiguous for no reason the user can see.
    """
    if not name:
        return forms
    want = name.strip().lower()
    exact = forms[forms.project.str.lower() == want]
    if len(exact):
        return exact
    loose = forms[forms.project.str.lower().str.contains(want, regex=False)]
    if loose.empty:
        raise ValueError(
            f"no project matching {name!r}. Pick one of: {sorted(forms.project.unique())}"
        )
    return loose


def resolve_selection(
    forms: "Any",
    form: Optional[str] = None,
    field: Optional[str] = None,
) -> Tuple["Any", List[str]]:
    """One row of the form table from loose input, plus warnings worth printing.

    `form` accepts either a `form_id` UUID or a (partial, case-insensitive) form
    NAME — the name is what the UI shows, so that is what people paste. Raises
    when nothing matches, and when several distinct forms match, rather than
    quietly profiling whichever sorted first: prod carries near-duplicate forms
    (`… v2` vs `… v2 (Desc only)`, and the same form cloned into a Testing
    project) whose extraction strategies differ.
    """
    cand = forms
    if form:
        f = str(form).strip()
        if _UUID_RE.match(f):
            cand = cand[cand.form_id == f]
        else:
            # Exact name first, as for projects: prod names forms in families
            # ("… v2" and "… v2 (Desc only)"), so a substring-only rule makes a
            # fully-typed name ambiguous with its own longer sibling.
            exact = cand[cand.form_name.str.lower() == f.lower()]
            cand = exact if len(exact) else cand[
                cand.form_name.str.lower().str.contains(f.lower(), na=False, regex=False)
            ]
        if cand.empty:
            raise ValueError(
                f"no form matching {form!r}. Names available: "
                f"{sorted(forms.form_name.unique())[:12]}"
            )
    if field:
        want = str(field).strip().lower()
        exact = cand[cand.field.str.lower() == want]
        narrowed = exact if len(exact) else cand[
            cand.field.str.lower().str.contains(want, na=False, regex=False)
        ]
        if narrowed.empty:
            raise ValueError(
                f"form has no table field matching {field!r}. Fields: "
                f"{sorted(cand.field.unique())}"
            )
        cand = narrowed

    if not form and not field:
        keyed = cand[cand.keyed_pipeline]
        cand = keyed if len(keyed) else cand

    distinct = cand.drop_duplicates(subset=["form_id", "field"])
    if len(distinct) > 1 and (form or field):
        raise ValueError(
            "ambiguous selection — "
            f"{len(distinct)} forms match. Paste one of these form_id values into "
            "FORM_ID:\n"
            + distinct[["project", "form_name", "field", "runs_as", "form_id"]]
              .to_string(index=False)
        )

    row = cand.iloc[0]
    warnings: List[str] = []
    if row.runs_as == "agentic":
        alt = forms[(forms.form_name == row.form_name) & forms.keyed_pipeline]
        warnings.append(
            f"{row.form_name!r} / {row.field} runs as AGENTIC (Claude Agent SDK). Its turns are "
            "not DSPy forwards, so you get the outer span only — no record-discovery / "
            "slot-fill breakdown."
        )
        if len(alt):
            a = alt.iloc[0]
            warnings.append(
                f"For a per-step breakdown of the same table, use the keyed twin: "
                f"project {a.project!r}, FORM_ID = \"{a.form_id}\""
            )
    elif row.runs_as == "single_pass":
        warnings.append(
            f"{row.form_name!r} / {row.field} runs SINGLE-PASS: one call for the whole table, "
            "so there are no per-operation steps to break down."
        )
    return row, warnings


def list_documents(
    project_id: str,
    limit: int = 100,
    only_with_markdown: bool = True,
) -> "Any":
    import pandas as pd
    sb = supabase()
    q = sb.table("documents").select(
        "id,filename,title,page_count,processing_status,s3_markdown_path,"
        "s3_blocks_path,blocks_status,parse_quality_score,created_at"
    ).eq("project_id", project_id).order("created_at", desc=True).limit(limit)
    rows = q.execute().data or []
    if only_with_markdown:
        rows = [r for r in rows if r.get("s3_markdown_path")]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["has_blocks"] = df["s3_blocks_path"].notna()
        df = df[[
            "id", "filename", "title", "page_count", "processing_status",
            "parse_quality_score", "has_blocks", "created_at",
        ]]
    return df.reset_index(drop=True)


def resolve_documents(
    docs: "Any",
    selectors: Optional[Any],
    n_default: int = 1,
) -> Tuple[List[str], List[str]]:
    """Document ids from loose input: UUIDs, filenames/titles, or a mix.

    `docs` is the frame `list_documents` returned. A prod document's `filename` is
    usually the paper's full title, so a fragment is what people actually have to
    hand — accept it, but refuse a fragment that matches two papers rather than
    profiling whichever sorted first.

    Returns (ids, labels). With no selectors, takes the first `n_default` rows,
    which is how the notebook runs untouched.
    """
    if selectors is None or (isinstance(selectors, (list, tuple)) and not selectors):
        head = docs.head(n_default)
        return head.id.tolist(), head.filename.fillna("").tolist()
    if isinstance(selectors, str):
        selectors = [selectors]          # a bare string is one paper, not N chars

    ids: List[str] = []
    labels: List[str] = []
    for sel in selectors:
        s = str(sel).strip()
        if _UUID_RE.match(s):
            hit = docs[docs.id == s]
            if hit.empty:
                raise ValueError(
                    f"document {s} is not in this project (or has no markdown in S3). "
                    "Check the table above."
                )
        else:
            hay = (
                docs.filename.fillna("").str.lower() + " ⟂ " + docs.title.fillna("").str.lower()
            )
            exact = docs[docs.filename.fillna("").str.lower() == s.lower()]
            hit = exact if len(exact) else docs[hay.str.contains(s.lower(), regex=False)]
            if hit.empty:
                raise ValueError(f"no document matching {s!r} in this project")
            if len(hit) > 1:
                raise ValueError(
                    f"{s!r} matches {len(hit)} documents — narrow it or paste an id:\n"
                    + hit[["id", "filename"]].head(10).to_string(index=False)
                )
        row = hit.iloc[0]
        if row.id in ids:
            continue                     # same paper named two ways
        ids.append(row.id)
        labels.append(row.filename or "")
    return ids, labels


def fetch_papers(doc_ids: Sequence[str], dest_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Download prod markdown to disk and build the paper dicts run_batch uses.

    Mirrors `app/workers/extraction_tasks` (S3 → temp file) and
    `app/services/extraction_service` (record_context preamble) so the bytes the
    model sees here are the bytes production sends. `path` is kept because
    `label_run_calls` uses it for provenance.
    """
    import tempfile
    from app.config import settings
    from app.services.storage_service import storage_service
    from utils import record_context

    sb = supabase()
    docs = (
        sb.table("documents")
        .select("id,filename,title,page_count,s3_markdown_path,s3_blocks_path")
        .in_("id", list(doc_ids))
        .execute()
        .data
        or []
    )
    by_id = {d["id"]: d for d in docs}
    missing = [d for d in doc_ids if d not in by_id]
    if missing:
        raise ValueError(f"document(s) not found: {missing}")

    out_dir = Path(dest_dir or tempfile.mkdtemp(prefix="evistream_timing_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    papers: List[Dict[str, Any]] = []
    for doc_id in doc_ids:  # preserve caller order
        doc = by_id[doc_id]
        key = doc.get("s3_markdown_path")
        if not key:
            raise ValueError(f"document {doc_id} ({doc.get('filename')}) has no s3_markdown_path")
        resp = storage_service.s3_client.get_object(Bucket=settings.S3_BUCKET, Key=key)
        raw = resp["Body"].read().decode("utf-8", errors="replace")
        content = record_context.preamble_for(raw) + raw
        local = out_dir / Path(key).name
        local.write_text(content, encoding="utf-8")
        fname = doc.get("filename") or Path(key).name
        papers.append({
            "doc_id": doc_id,
            "markdown_content": content,
            "path": str(local),
            "filename": fname,
            # Short handle for tables, chart axes and log lines. `filename` on prod
            # documents is often the paper's full title, which is 200+ chars and
            # unreadable as a row label.
            "label": (fname[:44] + "…") if len(fname) > 45 else fname,
            "title": doc.get("title"),
            "page_count": doc.get("page_count"),
            "chars": len(content),
            "est_tokens": len(content) // 4,
        })
    return papers


def load_schema_def(form_id: str) -> Dict[str, Any]:
    sb = supabase()
    rows = (
        sb.table("forms")
        .select("id,form_name,schema_name,schema_def,metadata")
        .eq("id", form_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise ValueError(f"form {form_id} not found")
    sd = rows[0].get("schema_def")
    if not sd:
        raise ValueError(f"form {form_id} ({rows[0].get('form_name')}) has no schema_def — not active?")
    return sd


# ── the recorder ───────────────────────────────────────────────────────────

# Sub-module attribute → operation. This is the PRIMARY source of the step: the
# extractor's own attribute names are unambiguous, whereas `ChainOfThought`
# rebuilds its signature via `Signature.prepend`, which returns a class named
# "StringSignature" — so the synthesized `…RecordDiscovery` / `…RecallAudit`
# names are gone by the time a call is made, and reading them labelled all three
# operations "extract" on the first live run.
STEP_BY_ATTR = {
    "record_discovery": "record_discovery",
    "recall_auditor": "recall_audit",
    "set_slot_filler": "slot_fill_set",
    "row_slot_filler": "slot_fill_row",
}

# Fallback only, for extractors this profiler didn't stamp (e.g. a sub-module
# built inside another module).
STEP_BY_SIG_SUFFIX = {
    "RecordDiscovery": "record_discovery",
    "RecallAudit": "recall_audit",
    "SetSlotFill": "slot_fill_set",
    "RowSlotFill": "slot_fill_row",
}

# What `utils.llm_call_labels.classify_step` reports for each operation. Record
# discovery is deliberately "extract" there: its prompt carries none of the three
# keyed marker fields, and production tells it apart from a genuine single-pass
# call only by comparing every call of the run (`label_run_calls`). A mismatch
# outside this table means the profiler and the cost telemetry disagree.
EXPECTED_PROMPT_STEP = {
    "record_discovery": "extract",
    "recall_audit": "recall_audit",
    "slot_fill_set": "slot_fill",
    "slot_fill_row": "slot_fill_row",
    "single_pass": "extract",
}

# This profiler's step name → the name `llm_history` stores. Only one differs.
# NOT the same mapping as EXPECTED_PROMPT_STEP: that one is about a single
# prompt read in isolation, where record discovery is indistinguishable from a
# single-pass extract. Stored rows are labelled run-wide by `label_run_calls`,
# so `record_discovery` IS a real value there and must not be folded into
# `extract` — doing so compares live record-discovery latency against the
# form's scalar signatures.
PROD_STEP_NAME = {"slot_fill_set": "slot_fill"}

# Method → operation span. These give the *structure* around the calls: which
# calls belong to one batch, which are a refill round, which are the row-at-a-time
# fallback after a set call failed twice.
METHOD_SPANS = {
    "_audit_record_set": "recall_audit",
    "_fill_slots_set": "slot_fill_set_batch",
    "_fill_slots_set_once": "slot_fill_set_attempt",
    "_fill_slots_row_warmed": "slot_fill_row_group",
    "_fill_slots_row": "slot_fill_row_call",
    "_refill_incomplete_records": "refill",
}

_span_stack: contextvars.ContextVar[Tuple[str, ...]] = contextvars.ContextVar(
    "evistream_timing_span_stack", default=()
)


@dataclass
class Span:
    span_id: str
    parent_id: Optional[str]
    name: str
    paper: str
    field: str
    t0: float
    t1: float = 0.0
    depth: int = 0
    meta: Dict[str, Any] = dc_field(default_factory=dict)

    @property
    def seconds(self) -> float:
        return max(0.0, self.t1 - self.t0)


@dataclass
class Call:
    call_id: str
    span_id: Optional[str]
    span_name: str
    step: str
    step_from_prompt: str
    paper: str
    field: str
    model: str
    t0: float
    t1: float
    wall_ms: int
    api_ms: Optional[int]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0
    finish_reason: str = ""
    truncated: bool = False
    shape: str = ""
    n_records: Optional[int] = None
    error: str = ""


class Profiler:
    """Span + call recorder. One per profiling run."""

    def __init__(self) -> None:
        self.origin = time.perf_counter()
        self.spans: List[Span] = []
        self._by_id: Dict[str, Span] = {}
        self.calls: List[Call] = []
        self.current_paper = ""
        self.current_field = ""

    # -- spans --
    @contextlib.asynccontextmanager
    async def span(self, name: str, **meta: Any):
        stack = _span_stack.get()
        sp = Span(
            span_id=uuid.uuid4().hex[:12],
            parent_id=stack[-1] if stack else None,
            name=name,
            paper=self.current_paper,
            field=self.current_field,
            t0=time.perf_counter(),
            depth=len(stack),
            meta=meta,
        )
        self.spans.append(sp)
        self._by_id[sp.span_id] = sp
        token = _span_stack.set(stack + (sp.span_id,))
        try:
            yield sp
        finally:
            sp.t1 = time.perf_counter()
            _span_stack.reset(token)

    def _current_span(self) -> Optional[Span]:
        stack = _span_stack.get()
        return self._by_id.get(stack[-1]) if stack else None

    def record_call(self, call: Call) -> None:
        self.calls.append(call)

    # -- frames --
    def calls_df(self):
        import pandas as pd
        rows = []
        for c in self.calls:
            d = dict(c.__dict__)
            d["start_s"] = round(c.t0 - self.origin, 3)
            d["end_s"] = round(c.t1 - self.origin, 3)
            d["wall_s"] = round(c.wall_ms / 1000.0, 3)
            d["api_s"] = round(c.api_ms / 1000.0, 3) if c.api_ms is not None else None
            d["overhead_s"] = (
                round((c.wall_ms - c.api_ms) / 1000.0, 3) if c.api_ms is not None else None
            )
            rows.append(d)
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values("start_s").reset_index(drop=True)
            df.insert(0, "seq", range(1, len(df) + 1))
        return df

    def spans_df(self):
        import pandas as pd
        rows = []
        for s in self.spans:
            rows.append({
                "span_id": s.span_id,
                "parent_id": s.parent_id,
                "name": s.name,
                "paper": s.paper,
                "field": s.field,
                "depth": s.depth,
                "start_s": round(s.t0 - self.origin, 3),
                "end_s": round(s.t1 - self.origin, 3),
                "seconds": round(s.seconds, 3),
                **{f"meta_{k}": v for k, v in s.meta.items()},
            })
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values("start_s").reset_index(drop=True)
        return df


# ── patching ───────────────────────────────────────────────────────────────

def _usage_of(entry: Dict[str, Any]) -> Dict[str, int]:
    """Token counts from a DSPy history entry, incl. both cache-read spellings.

    Mirrors `utils/logging.py`: Anthropic reports `cache_read_input_tokens`,
    OpenAI-shaped responses hide the same number under
    `prompt_tokens_details.cached_tokens`.
    """
    usage = entry.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    cache_read = usage.get("cache_read_input_tokens") or 0
    if not cache_read:
        details = usage.get("prompt_tokens_details") or {}
        if isinstance(details, dict):
            cache_read = details.get("cached_tokens") or 0
    return {
        "prompt_tokens": usage.get("prompt_tokens") or 0,
        "completion_tokens": usage.get("completion_tokens") or 0,
        "cache_read": cache_read or 0,
        "cache_write": usage.get("cache_creation_input_tokens") or 0,
    }


def _finish_reason(entry: Dict[str, Any]) -> str:
    resp = entry.get("response")
    try:
        return str(resp.choices[0].finish_reason or "")
    except Exception:
        return ""


def _step_of(cot_instance: Any) -> str:
    """Which operation this DSPy module performs — never inferred from ordering.

    Reads the `_evi_step` stamp `instrument()` puts on the extractor's
    sub-modules. Falls back to the signature class name, then to the signature's
    field set, so an unstamped module still lands somewhere honest.
    """
    stamped = getattr(cot_instance, "_evi_step", None)
    if stamped:
        return stamped

    sig = getattr(cot_instance, "signature", None) or getattr(
        cot_instance, "extended_signature", None
    )
    name = getattr(sig, "__name__", "") or ""
    for suffix, step in STEP_BY_SIG_SUFFIX.items():
        if name.endswith(suffix):
            return step

    fields = set(getattr(sig, "input_fields", {}) or {}) | set(
        getattr(sig, "output_fields", {}) or {}
    )
    if "candidate_row_plan" in fields or "missing_rows" in fields:
        return "recall_audit"
    if "rows_to_fill" in fields or "filled_rows" in fields:
        return "slot_fill_set"
    if "row_anchor" in fields:
        return "slot_fill_row"
    return "single_pass"


@contextlib.contextmanager
def instrument(profiler: Profiler, extractor_cls: Optional[type] = None):
    """Install timing patches; remove them on exit.

    Three patches:

    1. `runtime_builders.async_dspy_forward` — one span per DSPy forward, named
       from the signature class, so record discovery (which is inline in
       `__call__`, not a method) gets a span like everything else. This is also
       where litellm's `_response_ms` and the token/cache counts are harvested
       from DSPy history.
    2. The keyed extractor's methods (`METHOD_SPANS`) — the structure around the
       calls: batches, refill rounds, the row-at-a-time fallback.
    3. Nothing at the LM layer. DSPy's own JSONAdapter retry runs *inside* one
       forward, so its second call lands in history and is attributed to the same
       span — visible as two history entries for one forward, which is exactly
       what a parse-failure retry is.
    """
    import dspy
    from dspy_components import runtime_builders as rb
    from utils.llm_call_labels import classify_step, response_shape, duration_ms

    orig_forward = rb.async_dspy_forward
    patched_methods: List[Tuple[type, str, Any]] = []

    async def timed_forward(cot_instance, **inputs):
        step = _step_of(cot_instance)
        lm = dspy.settings.lm
        hist = getattr(lm, "history", None)
        n_before = len(hist) if isinstance(hist, list) else 0

        async with profiler.span(f"call:{step}") as sp:
            t0 = time.perf_counter()
            err = ""
            try:
                out = await orig_forward(cot_instance, **inputs)
                return out
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                t1 = time.perf_counter()
                # `lm` may have been swapped by dspy.context() inside
                # ModelRouter; read the live one for history.
                live_lm = dspy.settings.lm
                live_hist = getattr(live_lm, "history", None)
                entries: List[Dict[str, Any]] = []
                if isinstance(live_hist, list):
                    if live_lm is lm and len(live_hist) >= n_before:
                        entries = list(live_hist[n_before:])
                    else:
                        entries = list(live_hist[-4:])
                    # Under gather, other tasks' entries can interleave. Keep
                    # only entries whose prompt actually carries one of THIS
                    # call's input values (identity of the messages list is not
                    # stable across the adapter's rebuild).
                    probe = None
                    for key in ("rows_to_fill", "candidate_row_plan", "row_anchor"):
                        v = inputs.get(key)
                        if isinstance(v, str) and len(v) > 24:
                            probe = v[:120]
                            break
                    if probe:
                        from utils.llm_call_labels import messages_text
                        kept = [e for e in entries if probe in messages_text(e.get("messages"))]
                        entries = kept or entries

                parent = profiler._by_id.get(sp.parent_id) if sp.parent_id else None
                if not entries:
                    profiler.record_call(Call(
                        call_id=uuid.uuid4().hex[:12], span_id=sp.span_id,
                        span_name=parent.name if parent else "", step=step,
                        step_from_prompt="", paper=profiler.current_paper,
                        field=profiler.current_field,
                        model=str(getattr(live_lm, "model", "")),
                        t0=t0, t1=t1, wall_ms=int(round((t1 - t0) * 1000)),
                        api_ms=None, error=err or "no history entry captured",
                    ))
                    return
                # Several entries for one forward = DSPy retried the parse
                # (JSONAdapter fallback). Each is a real API call, so each is
                # recorded; only the last one's output was used.
                share = (t1 - t0) / len(entries)
                for i, entry in enumerate(entries):
                    u = _usage_of(entry)
                    api_ms = duration_ms(entry)
                    resp_text = ""
                    try:
                        resp_text = entry["response"].choices[0].message.content or ""
                    except Exception:
                        pass
                    from config.pricing import compute_cost
                    model = str(entry.get("model") or getattr(live_lm, "model", ""))
                    profiler.record_call(Call(
                        call_id=uuid.uuid4().hex[:12],
                        span_id=sp.span_id,
                        span_name=parent.name if parent else "",
                        step=step,
                        step_from_prompt=classify_step(entry.get("messages")),
                        paper=profiler.current_paper,
                        field=profiler.current_field,
                        model=model,
                        t0=t0 + i * share,
                        t1=t0 + (i + 1) * share,
                        wall_ms=int(round(share * 1000)),
                        api_ms=api_ms,
                        cost=compute_cost(
                            model, u["prompt_tokens"], u["completion_tokens"],
                            u["cache_write"], u["cache_read"],
                        ),
                        finish_reason=_finish_reason(entry),
                        truncated=_finish_reason(entry) in ("length", "max_tokens"),
                        shape=response_shape(resp_text),
                        error=err if i == len(entries) - 1 else "superseded_by_retry",
                        **u,
                    ))

    rb.async_dspy_forward = timed_forward

    if extractor_cls is not None:
        # Stamp each sub-module with the operation it performs, at construction
        # time. Doing it here rather than reading the signature name is what makes
        # record discovery distinguishable from a single-pass extract at all.
        orig_init = extractor_cls.__init__

        def stamping_init(self, *args, __orig=orig_init, **kwargs):
            __orig(self, *args, **kwargs)
            for attr, step in STEP_BY_ATTR.items():
                mod = getattr(self, attr, None)
                if mod is not None:
                    try:
                        object.__setattr__(mod, "_evi_step", step)
                    except Exception:
                        pass

        patched_methods.append((extractor_cls, "__init__", orig_init))
        extractor_cls.__init__ = stamping_init

        for meth, span_name in METHOD_SPANS.items():
            orig = getattr(extractor_cls, meth, None)
            if orig is None or not asyncio.iscoroutinefunction(orig):
                continue

            def make(orig=orig, span_name=span_name):
                async def wrapper(self, *args, **kwargs):
                    meta: Dict[str, Any] = {}
                    # Cheap, shape-agnostic size hints for the span label.
                    for a in args:
                        if isinstance(a, list):
                            meta.setdefault("n", len(a))
                    if "idxs" in kwargs and isinstance(kwargs["idxs"], list):
                        meta["n"] = len(kwargs["idxs"])
                    async with profiler.span(span_name, **meta):
                        return await orig(self, *args, **kwargs)
                return wrapper

            patched_methods.append((extractor_cls, meth, orig))
            setattr(extractor_cls, meth, make())

    try:
        yield profiler
    finally:
        rb.async_dspy_forward = orig_forward
        for cls, meth, orig in patched_methods:
            setattr(cls, meth, orig)


# ── the run ────────────────────────────────────────────────────────────────

@dataclass
class ProfileRun:
    profiler: Profiler
    results: Dict[str, Any]
    field: str
    sig_class: str
    runs_as: str
    model: str
    papers: List[Dict[str, Any]]
    errors: Dict[str, str] = dc_field(default_factory=dict)


def _find_field(schema_def: Dict[str, Any], field_name: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    for sig in schema_def.get("signatures", []) or []:
        for of in sig.get("output_fields", []) or []:
            if of.get("name") == field_name:
                return sig, of
    raise ValueError(f"field {field_name!r} not found in schema_def")


async def profile_field(
    schema_def: Dict[str, Any],
    field_name: str,
    papers: Sequence[Dict[str, Any]],
    use_model_router: bool = True,
    model_override: Optional[str] = None,
    parallel_papers: bool = False,
) -> ProfileRun:
    """Run ONE table field over `papers` with full timing instrumentation.

    The extractor comes from `build_schema_classes`, i.e. the same selection
    production makes — so a field whose strategy resolves to single-pass is
    measured as single-pass here too, rather than being forced down the keyed
    path and reported as something the live system never runs.

    `parallel_papers=False` by default: papers in sequence keep the waterfall
    readable and the per-step numbers uncontended. Flip it on to measure how
    much the paper-level fan-out actually overlaps.
    """
    from dspy_components.runtime_builders import build_schema_classes
    from utils.table_schema import field_key_columns, field_strategy

    sig_def, field_def = _find_field(schema_def, field_name)
    sig_class = sig_def["class_name"]
    factories = build_schema_classes(schema_def)
    if sig_class not in factories:
        raise ValueError(f"no extractor built for signature {sig_class}")
    extractor_cls = factories[sig_class]
    runs_as = (
        "keyed" if getattr(extractor_cls, "_is_keyed_pipeline", False)
        else ("agentic" if "Agentic" in extractor_cls.__name__ else "single_pass")
    )

    prof = Profiler()
    prof.current_field = field_name
    results: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    model_used = model_override or ""

    async def one_paper(paper: Dict[str, Any]):
        nonlocal model_used
        label = paper.get("label") or paper.get("filename") or paper["doc_id"][:8]
        prof.current_paper = label
        extractor = extractor_cls()
        async with prof.span("paper", doc_id=paper["doc_id"], chars=paper["chars"]):
            try:
                if use_model_router:
                    from utils.circuit_breaker import ModelRouter
                    router = ModelRouter.get_instance()
                    out = await router.run_with_routing(
                        async_callable=extractor,
                        operation_name=f"Extractor:{sig_class}",
                        override_primary_model=model_override,
                        markdown_content=paper["markdown_content"],
                    )
                else:
                    out = await extractor(markdown_content=paper["markdown_content"])
                results[label] = out
            except Exception as exc:
                errors[label] = f"{type(exc).__name__}: {exc}"
                logger.exception("profiling run failed for %s", label)

    with instrument(prof, extractor_cls):
        if parallel_papers:
            # Each paper gets its own task, so its spans stay in its own context.
            await asyncio.gather(*[one_paper(p) for p in papers])
        else:
            for p in papers:
                await one_paper(p)

    import dspy
    model_used = model_used or str(getattr(dspy.settings.lm, "model", ""))
    return ProfileRun(
        profiler=prof, results=results, field=field_name, sig_class=sig_class,
        runs_as=runs_as, model=model_used, papers=list(papers), errors=errors,
    )


# ── analysis ───────────────────────────────────────────────────────────────

def _union_seconds(intervals: Iterable[Tuple[float, float]]) -> float:
    """Wall-clock covered by at least one interval — concurrency-aware total."""
    iv = sorted((a, b) for a, b in intervals if b > a)
    if not iv:
        return 0.0
    total = 0.0
    cur_a, cur_b = iv[0]
    for a, b in iv[1:]:
        if a > cur_b:
            total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    return total + (cur_b - cur_a)


def step_summary(run: ProfileRun) -> "Any":
    """Per-operation totals: calls, latency, tokens, cache, cost, share of wall.

    `api_s_sum` adds every call's latency and so exceeds the wall clock wherever
    calls ran concurrently; `busy_s` is the union of their intervals, i.e. the
    real wall-clock contribution. The gap between the two IS the parallelism.
    """
    import pandas as pd
    df = run.profiler.calls_df()
    if df.empty:
        return df
    total_wall = _union_seconds([(c.t0, c.t1) for c in run.profiler.calls]) or 1e-9
    rows = []
    for step, g in df.groupby("step"):
        calls = [c for c in run.profiler.calls if c.step == step]
        busy = _union_seconds([(c.t0, c.t1) for c in calls])
        api = [c.api_ms / 1000.0 for c in calls if c.api_ms is not None]
        rows.append({
            "step": step,
            "calls": len(calls),
            "busy_s": round(busy, 2),
            "api_s_sum": round(sum(api), 2),
            "api_s_median": round(statistics.median(api), 2) if api else None,
            "api_s_max": round(max(api), 2) if api else None,
            "share_of_wall": f"{100 * busy / total_wall:.0f}%",
            "in_tok": int(g["prompt_tokens"].sum()),
            "out_tok": int(g["completion_tokens"].sum()),
            "cache_read": int(g["cache_read"].sum()),
            "cache_write": int(g["cache_write"].sum()),
            "cost_usd": round(float(g["cost"].sum()), 4),
            "retries": int((g["error"] == "superseded_by_retry").sum()),
            "truncated": int(g["truncated"].sum()),
        })
    out = pd.DataFrame(rows).sort_values("busy_s", ascending=False).reset_index(drop=True)
    return out


def paper_summary(run: ProfileRun) -> "Any":
    """Per-paper wall clock split into LLM time and our own Python time."""
    import pandas as pd
    prof = run.profiler
    rows = []
    for sp in prof.spans:
        if sp.name != "paper":
            continue
        calls = [c for c in prof.calls if sp.t0 <= c.t0 <= sp.t1 and c.paper == sp.paper]
        busy = _union_seconds([(c.t0, c.t1) for c in calls])
        api_sum = sum(c.api_ms / 1000.0 for c in calls if c.api_ms is not None)
        rows.append({
            "paper": sp.paper,
            "chars": sp.meta.get("chars"),
            "wall": _mmss(sp.seconds),
            "wall_s": round(sp.seconds, 2),
            "llm_busy_s": round(busy, 2),
            "non_llm_s": round(max(0.0, sp.seconds - busy), 2),
            "api_s_sum": round(api_sum, 2),
            "parallel_speedup": round(api_sum / busy, 2) if busy else None,
            "calls": len(calls),
            "cost_usd": round(sum(c.cost for c in calls), 4),
            "rows_out": _row_count(run.results.get(sp.paper), run.field),
            "error": run.errors.get(sp.paper, ""),
        })
    return pd.DataFrame(rows)


def _row_count(result: Any, field: str) -> Optional[int]:
    if not isinstance(result, dict):
        return None
    env = result.get(field)
    if isinstance(env, dict):
        val = env.get("value")
        return len(val) if isinstance(val, list) else 0
    return len(env) if isinstance(env, list) else None


def _mmss(seconds: float) -> str:
    """m:ss for reading at a glance — a 3-minute step is hard to see in `198.773`."""
    if seconds is None:
        return ""
    m, s = divmod(float(seconds), 60.0)
    return f"{int(m)}:{s:04.1f}"


def phase_summary(run: ProfileRun) -> "Any":
    """Span-level view: the operation structure, with nesting preserved.

    Call spans are hidden EXCEPT the ones sitting directly under the paper — i.e.
    record discovery, which `__call__` performs inline rather than in a method and
    which therefore has no method span of its own. Without that exception the
    single largest block of the run is missing from this table and shows only as an
    unexplained gap before the recall audit.
    """
    import pandas as pd
    df = run.profiler.spans_df()
    if df.empty:
        return df
    paper_ids = {s.span_id for s in run.profiler.spans if s.name == "paper"}
    keep = df.apply(
        lambda r: (not r["name"].startswith("call:")) or (r["parent_id"] in paper_ids),
        axis=1,
    )
    df = df[keep].copy()
    df["name"] = df["name"].str.replace("^call:", "", regex=True)
    df["label"] = df.apply(lambda r: "  " * int(r["depth"]) + r["name"], axis=1)
    df["elapsed"] = df["seconds"].map(_mmss)
    total = df.loc[df["name"] == "paper", "seconds"].sum() or None
    if total:
        df["share"] = df["seconds"].map(lambda s: f"{100 * s / total:.0f}%")
    cols = ["label", "paper", "elapsed", "seconds", "start_s", "end_s"]
    if "share" in df.columns:
        cols.insert(3, "share")
    if "meta_n" in df.columns:
        cols.insert(2, "meta_n")
    return df[cols].reset_index(drop=True)


def label_check(run: ProfileRun) -> "Any":
    """Calls where this profiler and production's cost labeller disagree.

    Expected agreement is defined by `EXPECTED_PROMPT_STEP`, not by string
    equality: record discovery legitimately classifies as `extract` from the
    prompt alone. Anything outside that table means the two disagree about what
    ran, and the profiler's step names should not be trusted until it's explained.
    """
    import pandas as pd
    df = run.profiler.calls_df()
    if df.empty:
        return df
    df = df[df["step_from_prompt"] != ""].copy()
    df["expected"] = df["step"].map(EXPECTED_PROMPT_STEP)
    bad = df[df["expected"].notna() & (df["expected"] != df["step_from_prompt"])]
    return bad[["seq", "paper", "step", "expected", "step_from_prompt", "shape"]].reset_index(drop=True)


def waterfall(run: ProfileRun, paper: Optional[str] = None, figsize=(13, None)):
    """Gantt of every LLM call (and the enclosing operations) on one paper.

    Overlapping bars are the `asyncio.gather` fan-outs; a staircase means the
    step is sequential and its latency lands on the critical path in full.
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    prof = run.profiler
    papers = [s for s in prof.spans if s.name == "paper"]
    if paper is None and papers:
        paper = papers[0].paper
    calls = [c for c in prof.calls if c.paper == paper]
    spans = [
        s for s in prof.spans
        if s.paper == paper and not s.name.startswith("call:") and s.name != "paper"
    ]
    if not calls:
        raise ValueError(f"no calls recorded for paper {paper!r}")

    t0 = min([c.t0 for c in calls] + [s.t0 for s in spans])
    colors = {
        "record_discovery": "#4C6FFF",
        "recall_audit": "#00A88F",
        "slot_fill_set": "#F2A93B",
        "slot_fill_row": "#E4572E",
        "extract": "#7A5AF8",
    }
    n_lanes = len(spans) + len(calls) + 1
    fig, ax = plt.subplots(figsize=(figsize[0], figsize[1] or max(3.0, 0.34 * n_lanes)))

    lane = 0
    labels: List[str] = []
    for s in sorted(spans, key=lambda s: (s.t0, s.depth)):
        ax.broken_barh(
            [(s.t0 - t0, max(s.seconds, 0.01))], (lane - 0.35, 0.7),
            facecolors="#D9DEE8", edgecolors="#9AA4B8", linewidth=0.6,
        )
        n = s.meta.get("n")
        labels.append(f"{'· ' * s.depth}{s.name}" + (f" (n={n})" if n else ""))
        lane += 1

    for c in sorted(calls, key=lambda c: c.t0):
        dur = max((c.t1 - c.t0), 0.01)
        ax.broken_barh(
            [(c.t0 - t0, dur)], (lane - 0.35, 0.7),
            facecolors=colors.get(c.step, "#888"), edgecolors="none",
        )
        api = f"{c.api_ms / 1000:.1f}s api" if c.api_ms else "no api ms"
        ax.text(
            c.t0 - t0 + dur + 0.35, lane, f"{dur:.1f}s  ({api})",
            va="center", fontsize=7.5, color="#333",
        )
        tag = "retry" if c.error == "superseded_by_retry" else ""
        labels.append(f"{c.step}{' ' + tag if tag else ''}")
        lane += 1

    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    # Headroom for the per-bar duration annotations, which sit to the RIGHT of
    # each bar and would otherwise be clipped on the longest call.
    span = max([c.t1 for c in calls] + [s.t1 for s in spans]) - t0
    ax.set_xlim(-0.02 * span, span * 1.26)
    ax.set_xlabel("seconds from first call")
    ax.set_title(
        f"{run.field} — {paper}\n{run.runs_as} pipeline · {run.model}",
        fontsize=10, loc="left",
    )
    ax.grid(axis="x", alpha=0.25, linestyle=":")
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    present = [s for s in colors if any(c.step == s for c in calls)]
    # Above the axes, right-aligned beside the left-aligned title. Any in-axes
    # corner overlaps a bar once a run has more than a couple of calls, and
    # anything below the axes lands on the x-label, since the axes height scales
    # with the number of lanes.
    ax.legend(
        handles=[mpatches.Patch(color=colors[s], label=s) for s in present],
        loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=max(1, len(present)),
        fontsize=8, frameon=False,
    )
    plt.tight_layout()
    return fig


def overhead_report(run: ProfileRun) -> str:
    """Plain-English accounting of the wall clock."""
    prof = run.profiler
    calls = prof.calls
    if not calls:
        return "No calls recorded."
    paper_spans = [s for s in prof.spans if s.name == "paper"]
    wall = sum(s.seconds for s in paper_spans)
    busy = _union_seconds([(c.t0, c.t1) for c in calls])
    api_sum = sum(c.api_ms / 1000.0 for c in calls if c.api_ms is not None)
    lines = [
        f"papers                 : {len(paper_spans)}",
        f"LLM calls              : {len(calls)}"
        f"  (retried parses: {sum(1 for c in calls if c.error == 'superseded_by_retry')})",
        f"wall clock (sum/paper) : {wall:.1f}s",
        f"LLM busy (union)       : {busy:.1f}s  ({100 * busy / wall:.0f}% of wall)"
        if wall else "",
        f"LLM latency (sum)      : {api_sum:.1f}s  "
        f"→ {api_sum / busy:.2f}x concurrency" if busy else "",
        f"our Python / parse     : {max(0.0, wall - busy):.1f}s",
        f"cost                   : ${sum(c.cost for c in calls):.4f}",
    ]
    return "\n".join(l for l in lines if l)


# ── prod: what past jobs actually took ────────────────────────────────────

def prod_step_summary(
    schema_name: Optional[str] = None,
    job_id: Optional[str] = None,
    days: int = 30,
    limit: int = 4000,
) -> "Any":
    """Per-step latency from `llm_history` — real production runs, no LLM spend.

    `metadata.duration_ms` is written at flush time by `utils/logging.py` for
    rows created after Aug 12 2026. Older rows have no duration; the step is
    still recoverable by re-classifying the stored prompt, so those rows count
    toward call volume but not latency.
    """
    import pandas as pd
    from datetime import datetime, timedelta, timezone
    from utils.llm_call_labels import classify_step

    sb = supabase()
    q = sb.table("llm_history").select(
        "id,created_at,job_id,schema_name,model,messages,metadata,"
        "prompt_tokens,completion_tokens,cache_read_input_tokens,"
        "cache_creation_input_tokens,cost,source_file"
    ).order("created_at", desc=True).limit(limit)
    if job_id:
        q = q.eq("job_id", job_id)
    if schema_name:
        q = q.eq("schema_name", schema_name)
    if days:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        q = q.gte("created_at", since)
    rows = q.execute().data or []

    recs = []
    for r in rows:
        md = r.get("metadata") or {}
        step = md.get("step")
        if not step:
            msgs = r.get("messages")
            if isinstance(msgs, str):
                try:
                    msgs = json.loads(msgs)
                except Exception:
                    msgs = None
            step = classify_step(msgs) if msgs else "unknown"
        recs.append({
            "created_at": r.get("created_at"),
            "job_id": r.get("job_id"),
            "schema_name": r.get("schema_name"),
            "model": r.get("model"),
            "step": step,
            "duration_ms": md.get("duration_ms"),
            "document_id": md.get("document_id"),
            "n_records": md.get("n_records"),
            "superseded": bool(md.get("superseded")),
            "prompt_tokens": r.get("prompt_tokens") or 0,
            "completion_tokens": r.get("completion_tokens") or 0,
            "cache_read": r.get("cache_read_input_tokens") or 0,
            "cache_write": r.get("cache_creation_input_tokens") or 0,
            "cost": r.get("cost") or 0.0,
        })
    df = pd.DataFrame(recs)
    if df.empty:
        return df

    def agg(g):
        d = g["duration_ms"].dropna()
        return pd.Series({
            "calls": len(g),
            "with_timing": len(d),
            "median_s": round(d.median() / 1000, 2) if len(d) else None,
            "p95_s": round(d.quantile(0.95) / 1000, 2) if len(d) else None,
            "max_s": round(d.max() / 1000, 2) if len(d) else None,
            "sum_s": round(d.sum() / 1000, 1) if len(d) else None,
            "retries": int(g["superseded"].sum()),
            "cache_read": int(g["cache_read"].sum()),
            "cache_write": int(g["cache_write"].sum()),
            "cost_usd": round(float(g["cost"].sum()), 3),
        })

    return (
        df.groupby("step", dropna=False)
        .apply(agg, include_groups=False)
        .sort_values("sum_s", ascending=False)
        .reset_index()
    )


def prod_jobs(
    project_id: Optional[str] = None,
    job_type: str = "extraction",
    limit: int = 25,
) -> "Any":
    """Recent extraction jobs with queue wait and run time.

    `queue_s` is `started_at - created_at` — the Celery wait, which the in-process
    profiler above cannot see and which is often the bigger half of "why did this
    take so long".
    """
    import pandas as pd
    sb = supabase()
    q = sb.table("jobs").select(
        "id,project_id,job_type,status,created_at,started_at,completed_at,"
        "progress,error_message,input_data"
    ).order("created_at", desc=True).limit(limit)
    if project_id:
        q = q.eq("project_id", project_id)
    if job_type:
        q = q.eq("job_type", job_type)
    rows = q.execute().data or []
    out = []
    for r in rows:
        c, s, e = r.get("created_at"), r.get("started_at"), r.get("completed_at")
        ts = {k: pd.to_datetime(v, utc=True, errors="coerce") for k, v in
              (("c", c), ("s", s), ("e", e))}
        inp = r.get("input_data") or {}
        out.append({
            "job_id": r["id"],
            "status": r.get("status"),
            "created_at": c,
            "queue_s": round((ts["s"] - ts["c"]).total_seconds(), 1)
            if ts["s"] is not None and ts["c"] is not None and pd.notna(ts["s"]) else None,
            "run_s": round((ts["e"] - ts["s"]).total_seconds(), 1)
            if ts["e"] is not None and ts["s"] is not None
            and pd.notna(ts["e"]) and pd.notna(ts["s"]) else None,
            "n_docs": len(inp.get("document_ids") or []) or None,
            "form_id": inp.get("form_id"),
            "error": (r.get("error_message") or "")[:80],
        })
    return pd.DataFrame(out)


# ── saving ────────────────────────────────────────────────────────────────

def save_outputs(run: ProfileRun, tag: str, stamp: Optional[str] = None) -> Path:
    """Write per-call / per-step / per-paper CSVs + a JSON header. Returns the dir."""
    from datetime import datetime
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUTPUTS_DIR / f"{stamp}_{tag}"
    out.mkdir(parents=True, exist_ok=True)
    run.profiler.calls_df().to_csv(out / "calls.csv", index=False)
    run.profiler.spans_df().to_csv(out / "spans.csv", index=False)
    step_summary(run).to_csv(out / "step_summary.csv", index=False)
    paper_summary(run).to_csv(out / "paper_summary.csv", index=False)
    (out / "run.json").write_text(json.dumps({
        "field": run.field,
        "sig_class": run.sig_class,
        "runs_as": run.runs_as,
        "model": run.model,
        "papers": [
            {k: p[k] for k in ("doc_id", "filename", "chars", "page_count") if k in p}
            for p in run.papers
        ],
        "errors": run.errors,
        "overhead": overhead_report(run),
    }, indent=2, default=str), encoding="utf-8")
    return out


__all__ = [
    "bootstrap", "supabase", "list_projects", "list_table_forms", "list_documents",
    "fetch_papers", "load_schema_def", "Profiler", "instrument", "profile_field",
    "ProfileRun", "step_summary", "paper_summary", "phase_summary", "waterfall",
    "overhead_report", "label_check", "prod_step_summary", "prod_jobs", "save_outputs",
]
