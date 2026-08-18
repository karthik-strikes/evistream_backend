"""Agentic table-field extraction (Claude Agent SDK).

Runtime counterpart of the standard DSPy table extractors in
``dspy_components.runtime_builders``. Selected per form via
``schema_def["table_extraction_mode"] == "agentic"``; see
``build_schema_classes``.

One ``query()`` session per (paper, table field). The agent commits a row plan
before extracting any value, greps the paper to prove a value is absent rather
than assuming it, verifies its quotes through the backend's own
``utils.source_linker``, and repairs missing rows with an explicit
justified-withdrawal escape so it is never pressured to invent rows.

The emitted envelope is byte-compatible with what ``_TwoStageExtractor`` and the
single-call extractor produce::

    {field: {"value": [ {col: {"value", "source_text", "status"}}, ... ],
             "source_text": ..., "status": ...}}

Empty table -> ``{"value": "NR", "source_text": "NR", "status": "not_reported"}``.

Requires ``claude-agent-sdk`` and ANTHROPIC_API_KEY (injected into os.environ by
``utils.secrets_loader``). Claude-only: the job-creation path forces a Claude
model when a form is in agentic mode.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field as dc_field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# One absence vocabulary across both table strategies — see utils/absence.py.
from utils.table_schema import field_key_columns
from utils import absence

# Traces are opt-in and land outside the repo; extraction runs on a server where
# the working tree is not writable by convention.
AGENTIC_TRACE_DIR = Path(
    os.getenv("EVISTREAM_AGENTIC_TRACE_DIR") or (Path(tempfile.gettempdir()) / "evistream_agentic_traces")
)

# The repo's deterministic quote->page/bbox resolver. Our output must survive it,
# so we grade ourselves with the exact same code the backend uses.
try:
    from utils.source_linker import (
        build_source_index,
        locate_source,
        locate_value,
        parse_page_boundaries,
        enrich_extraction_results,
    )
    _HAVE_SOURCE_LINKER = True
except Exception as _e:  # pragma: no cover - surfaced at runtime, not import time
    _HAVE_SOURCE_LINKER = False
    _SOURCE_LINKER_ERR = _e

# Optional: reuse the exact prompt composer the DSPy path uses for a table field.
try:
    from dspy_components.runtime_builders import _compose_field_desc
    _HAVE_COMPOSER = True
except Exception:
    _HAVE_COMPOSER = False

from claude_agent_sdk import (  # noqa: E402
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    ToolAnnotations,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

# SystemMessage is used on the MCP docs page but is absent from the Message union
# in the Python reference; import defensively so a version skew can't break us.
try:
    from claude_agent_sdk import SystemMessage  # type: ignore
except ImportError:  # pragma: no cover
    class SystemMessage:  # type: ignore
        subtype: str = ""
        data: Dict[str, Any] = {}

try:
    from claude_agent_sdk import TextBlock, ToolResultBlock, UserMessage  # type: ignore
except ImportError:  # pragma: no cover
    class TextBlock: text: str = ""          # type: ignore
    class ToolResultBlock: content: Any = ""  # type: ignore
    class UserMessage: content: List[Any] = []  # type: ignore

# get_session_messages()/list_sessions() let you replay a finished session's full
# transcript from disk after the fact - useful when a trace file was not requested.
try:
    from claude_agent_sdk import get_session_messages, list_sessions  # type: ignore
except ImportError:  # pragma: no cover
    get_session_messages = list_sessions = None  # type: ignore

# Defined up here (not in the adapter section) so the MCP tool handlers and the
# repair loop can narrate what the agent is doing. An agentic run takes minutes;
# without these the worker log goes silent between "extractor built" and "done".
import logging  # noqa: E402

logger = logging.getLogger(__name__)


def _tag(aud: "SessionAudit") -> str:
    return f"agentic[{aud.paper}/{aud.field_name}]"


# ═════════════════════════════════════════════════════════════════════════════
# 1. CONFIG KNOBS  (tune these first; defaults are the recommended starting set)
# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class Config:
    # --- model / reasoning -------------------------------------------------
    model: str = "claude-sonnet-5"
    fallback_model: Optional[str] = None
    # effort is the single biggest accuracy/cost dial. Thinking tokens bill as
    # OUTPUT ($15/MTok on Sonnet), so buy depth only where rows are many.
    # Keyed on COLUMN count, which is known exactly from schema_def, not on the
    # row guess. Keying it on guessed rows inverted the dial: a wider table (more
    # cells, more attribution risk) was handed *less* reasoning, because the
    # guess drops from 8 rows to 4 the moment a table exceeds 10 columns.
    effort_small: str = "medium"          # narrow tables
    effort_large: str = "high"            # wide tables: attribution errors dominate
    effort_col_switch: int = 8
    # Adaptive thinking: the model decides when to think; we never read the text,
    # so omit the summaries. NOTE: omitted summaries are not *free* - the tokens
    # are still generated and billed. Set {"type":"disabled"} to hard-stop that.
    thinking: Dict[str, Any] = dc_field(
        default_factory=lambda: {"type": "adaptive", "display": "omitted"}
    )

    # --- limits ------------------------------------------------------------
    # Turns are cheap (a turn re-reads the cached prefix at 0.1x) but unbounded
    # loops are not. Budget is the real backstop.
    max_turns_base: int = 10
    max_turns_per_4_rows: int = 2
    max_turns_cap: int = 24
    # Budget scales on CELLS (rows x value columns), because output volume — the
    # expensive part — is per cell, not per row. The old row-only formula gave an
    # 18-column table a smaller cap than a 9-column one.
    #
    # These are a runaway backstop, NOT a target. A cap you expect to hit is a
    # failure generator: hitting it produces no answer at all, so every dollar
    # spent up to that point is wasted. Measured reference: a 9-col x 6-row run
    # cost $0.539 with no repair round.
    budget_usd_base: float = 0.80          # per-session floor: harness preamble + planning
    budget_usd_per_cell: float = 0.012     # rows x attr_columns
    budget_usd_session_cap: float = 2.00   # ceiling for any ONE session
    # Ceiling for the WHOLE extraction — main pass plus every resume and repair
    # leg combined. Without this, each leg got a fresh allowance and the total
    # was unbounded (a real run reached $1.90 across two sessions).
    budget_usd_extraction_cap: float = 3.00
    api_timeout_ms: int = 180_000
    session_timeout_s: int = 420

    # --- verification ------------------------------------------------------
    sample_value_cells: int = 12          # in-loop verify_quotes sample size
    require_all_anchor_quotes: bool = True
    grounding_threshold: float = 0.65     # matches source_linker.locate_source
    # 2, not 1: rows and quotes are now separate repair kinds, and a row shortfall
    # takes the first round. ~$0.027 per round on a cached prefix.
    max_repair_rounds: int = 2
    repair_min_failed_cells: int = 1

    # --- structure ---------------------------------------------------------
    # Emitting the table twice (prose + structured output) doubles the dominant
    # cost line. Only offer the pre-check tool on tables small enough that a
    # second emission is affordable.
    envelope_precheck_max_rows: int = 8
    use_recall_audit_subagent: bool = False  # fresh-context independent row count
    recall_audit_model: str = "sonnet"
    recall_audit_effort: str = "medium"
    strict_nr_union_schema: bool = False   # oneOf["NR", array] instead of array+[]

    # --- infra -------------------------------------------------------------
    concurrency: int = 4
    retries: int = 1
    prompt_cache_1h: bool = True           # sibling field-sessions land >5min apart
    log_tool_calls: bool = True

    # --- observability -----------------------------------------------------
    # trace=True writes a step-by-step JSONL of what the model thought, which
    # tools it called with what arguments, what came back, and the per-step token
    # cost. It also flips thinking display to "summarized" so the reasoning is
    # actually delivered instead of discarded.
    trace: bool = False
    trace_max_chars: int = 4000            # per record, keeps trace files readable


CFG = Config()


# ═════════════════════════════════════════════════════════════════════════════
# 2. JSON SCHEMA GENERATOR  (schema_def.subform_fields -> output_format schema)
# ═════════════════════════════════════════════════════════════════════════════
_NR = absence.NR_LABEL
# Model-facing status enum. "extracted" was the historical spelling of
# "reported"; it stays accepted on the read side via absence.LEGACY_STATUS_ALIASES
# so cached responses and older prompts still validate.
_STATUS_VALUES = [
    absence.REPORTED,
    absence.NOT_REPORTED,
    absence.NOT_APPLICABLE,
    absence.PARTIAL,
]


def _cell_value_schema(col: Dict[str, Any]) -> Dict[str, Any]:
    """JSON Schema for one cell's `value`, derived from the column definition.

    Deliberately uses only the narrow keyword set (`type`, `enum`, `description`)
    that the platform's structured-output validator accepts everywhere. Numbers
    accept string-or-number because the repo stores "1.67" as a string (see the
    `examples` blocks in the compiled schemas) and adjudication `_canon` treats
    "3" == 3.
    """
    ftype = (col.get("field_type") or "string").lower()
    opts = [str(o) for o in (col.get("options") or [])]

    if opts:
        if col.get("multiple"):
            # Multi-select: a JSON array of option strings (mirrors the
            # `multiple: true` plumbing in _compose_field_desc).
            return {
                "type": "array",
                "items": {"enum": opts + absence.schema_extras(opts)},
                "description": "every option that applies; [] never - use NR",
            }
        return {"enum": opts + absence.schema_extras(opts)}

    if ftype in ("number", "integer", "float", "numeric"):
        return {
            "type": ["string", "number"],
            "description": 'numeric as printed in the paper, or "NR"',
        }
    if ftype in ("boolean", "bool"):
        return {"enum": ["yes", "no", _NR]}
    return {"type": "string"}


def build_output_schema(
    field_def: Dict[str, Any],
    strict_nr_union: bool = False,
) -> Dict[str, Any]:
    """Generate the draft-07 json_schema for one table field's envelope.

    `field_def` is a compiled `output_fields[i]` entry from a form's `schema_def`
    (e.g. the `outcomes` field of dynamic_6c8eecce_ContinuousOutcomesV2.json).

    Two modes for the not-reported case:
      * default (`strict_nr_union=False`): `value` is always an array. An empty
        table is `[]`, and `normalize_envelope()` rewrites `[]` into the
        `{"value":"NR","source_text":"NR","status":"not_reported"}` shape - the
        same conversion the keyed extractor does when record discovery yields nothing.
        Keeps the schema inside the safest keyword subset.
      * `strict_nr_union=True`: `value` is `oneOf[{"const":"NR"}, array]`. Fewer
        moving parts downstream, but leans on `oneOf`/`const` support in the
        structured-output validator.
    """
    fname = field_def["name"]
    cols = field_def.get("subform_fields") or []
    anchors = set(field_key_columns(field_def))

    row_props: Dict[str, Any] = {}
    for col in cols:
        cname = col["field_name"]
        desc = (col.get("field_description") or "").strip()
        role = "ROW IDENTITY" if cname in anchors else "MEASURED VALUE"
        row_props[cname] = {
            "type": "object",
            "description": f"[{role}] {desc}"[:900],
            "properties": {
                "value": _cell_value_schema(col),
                "source_text": {
                    "type": "string",
                    "description": (
                        "VERBATIM span copied from the paper (<=30 words) that "
                        "grounds this cell. It may come from ANY part of the text: "
                        "a prose sentence, a table row copied exactly as printed "
                        'including labels and pipes ("| 6 months | 43.20 '
                        '(12.50-72.60) |"), a figure caption, or a table footnote. '
                        "Never paraphrase. If the number exists only inside a "
                        "figure image, there is nothing to quote and the cell is "
                        '{"value":"NR","source_text":"NR"}.'
                    ),
                },
            },
            "required": ["value", "source_text"],
            "additionalProperties": False,
        }

    row_schema = {
        "type": "object",
        "properties": row_props,
        "required": [c["field_name"] for c in cols],   # every column, every row
        "additionalProperties": False,                 # no invented columns
    }
    rows_schema = {
        "type": "array",
        "items": row_schema,
        "description": (
            f"one object per distinct row. Row identity is "
            f"({', '.join(sorted(anchors)) or 'see field rules'}); "
            "never merge two identities into one row, never duplicate one."
        ),
    }

    if strict_nr_union:
        value_schema: Dict[str, Any] = {
            "oneOf": [rows_schema, {"const": _NR}],
            "description": 'the row array, or "NR" if the paper reports no such table',
        }
    else:
        value_schema = dict(rows_schema)
        value_schema["description"] += ' Use [] if the paper reports no such table.'

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            fname: {
                "type": "object",
                "properties": {
                    "value": value_schema,
                    "source_text": {
                        "type": "string",
                        "description": (
                            "ONE verbatim sentence (<=30 words) locating this data in "
                            "the paper: a table caption, the sentence that introduces "
                            "the results, or - when the data is reported only in prose "
                            "- the first sentence that reports it. "
                            '"NR" if the paper reports none of this data.'
                        ),
                    },
                    "status": {"enum": _STATUS_VALUES},
                },
                "required": ["value", "source_text", "status"],
                "additionalProperties": False,
            }
        },
        "required": [fname],
        "additionalProperties": False,
    }


def pick_table_field(schema_def: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (parent_sig_def, table_output_field) for the first table field found."""
    for sig in schema_def.get("signatures", []):
        for out in sig.get("output_fields", []):
            if out.get("subform_fields"):
                return sig, out
    raise ValueError("no output field with subform_fields in this schema_def")


# ═════════════════════════════════════════════════════════════════════════════
# 3. IN-PROCESS MCP TOOLS
# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class SessionAudit:
    """Per-session verification ledger. Makes 'the agent checked its work'
    an observable fact in the cost report rather than a claim in a prompt."""
    run_id: str
    paper: str
    field_name: str
    markdown: str = ""
    schema: Dict[str, Any] = dc_field(default_factory=dict)
    plan_rows: List[Dict[str, Any]] = dc_field(default_factory=list)
    plan_evidence: str = ""
    plan_committed: bool = False
    verify_calls: int = 0
    quotes_checked: int = 0
    quotes_failed: int = 0
    quote_failures: List[str] = dc_field(default_factory=list)
    envelope_checks: int = 0
    tool_calls: List[str] = dc_field(default_factory=list)
    # columns whose values are normalised codes -> exempt from the
    # "value must appear inside the quote" check
    normalized_cols: set = dc_field(default_factory=set)
    tracer: Any = None
    _index: Any = None

    def index(self):
        if self._index is None and _HAVE_SOURCE_LINKER:
            self._index = build_source_index(
                self.markdown, parse_page_boundaries(self.markdown)
            )
        return self._index


# run_id -> SessionAudit. The agent echoes run_id on every tool call, so handlers
# resolve state without relying on which asyncio task the SDK invokes them from.
AUDITS: Dict[str, SessionAudit] = {}


def _err(msg: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def _ok(msg: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}]}


def _audit(run_id: str) -> Optional[SessionAudit]:
    return AUDITS.get(run_id)


@tool(
    "commit_row_plan",
    "Register the complete row inventory for this table BEFORE extracting any "
    "measured values. Call exactly once. Returns the registered count and flags "
    "duplicate row identities. You cannot finish without calling this.",
    {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "RUN_ID from the prompt"},
            "rows": {
                "type": "array",
                "description": "one object per row: the row-identity (anchor) "
                               "column values only, as plain strings",
                "items": {"type": "object"},
            },
            "evidence": {
                "type": "string",
                "description": "where the rows came from: table captions and/or "
                               "line numbers you grepped, e.g. 'Table 2 L106-L131'",
            },
        },
        "required": ["run_id", "rows", "evidence"],
    },
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def commit_row_plan(args: Dict[str, Any]) -> Dict[str, Any]:
    aud = _audit(args["run_id"])
    if aud is None:
        return _err(f"unknown run_id {args['run_id']!r}; copy RUN_ID from the prompt")
    rows = args.get("rows") or []
    if not isinstance(rows, list):
        return _err("`rows` must be an array of objects")

    seen, dupes, norm = set(), [], []
    for r in rows:
        if not isinstance(r, dict):
            return _err(f"each row must be an object of anchor values, got {type(r).__name__}")
        flat = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in r.items()}
        key = json.dumps(flat, sort_keys=True, default=str)
        (dupes if key in seen else norm).append(flat)
        seen.add(key)

    aud.plan_rows = norm
    aud.plan_evidence = str(args.get("evidence") or "")
    aud.plan_committed = True
    aud.tool_calls.append("commit_row_plan")

    msg = [f"Registered {len(norm)} distinct rows."]
    if dupes:
        msg.append(f"DROPPED {len(dupes)} duplicate row identities: {dupes[:3]}")
    msg.append(
        "Your final structured output must contain exactly these "
        f"{len(norm)} rows - no more, no fewer. If you now find a row you missed, "
        "say so explicitly in your reply and include it anyway."
    )
    return _ok(" ".join(msg))


@tool(
    "verify_quotes",
    "Check candidate source_text quotes against the paper using the SAME resolver "
    "the production pipeline uses. Batch every quote for a row (or a block of rows) "
    "into ONE call. A quote that fails here will not resolve to a page/bbox "
    "downstream, so fix it before you commit it.",
    {
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "quotes": {
                "type": "array",
                "description": "quotes to check, each labelled with the cell it grounds",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "e.g. 'row3.mean_arm1'"},
                        "quote": {"type": "string"},
                        "value": {"type": "string", "description": "the cell value this quote must contain"},
                    },
                    "required": ["label", "quote"],
                },
            },
        },
        "required": ["run_id", "quotes"],
    },
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def verify_quotes(args: Dict[str, Any]) -> Dict[str, Any]:
    aud = _audit(args["run_id"])
    if aud is None:
        return _err(f"unknown run_id {args['run_id']!r}")
    if not _HAVE_SOURCE_LINKER:
        return _err(f"source_linker unavailable: {_SOURCE_LINKER_ERR}")

    idx = aud.index()
    aud.verify_calls += 1
    aud.tool_calls.append("verify_quotes")

    lines, n_bad = [], 0
    for item in (args.get("quotes") or [])[:200]:
        label = str(item.get("label") or "?")
        quote = str(item.get("quote") or "")
        val = item.get("value")
        aud.quotes_checked += 1

        if quote.strip().upper() in ("NR", ""):
            lines.append(f"{label}: NR (skipped)")
            continue

        loc = locate_source(quote, idx, threshold=CFG.grounding_threshold)
        if loc is None:
            n_bad += 1
            aud.quotes_failed += 1
            aud.quote_failures.append(label)
            lines.append(
                f"{label}: FAIL - not found in the paper. You paraphrased or "
                "stitched sentences. Copy a contiguous span byte-for-byte."
            )
            continue

        tag = "EXACT" if loc.confidence >= 0.999 else f"fuzzy {loc.confidence:.2f}"
        note = f"{label}: OK ({tag}, p{loc.page}, {loc.section or 'no section'})"
        col = label.rsplit(".", 1)[-1]
        if col in aud.normalized_cols:
            # Normalised select value: the quote only has to contain the phrase
            # the code was derived from, not the code itself.
            lines.append(note)
            continue
        if val not in (None, "", "NR"):
            hay = re.sub(r"\s+", " ", quote).lower()
            if str(val).strip().lower() not in hay:
                vloc = locate_value(str(val), idx)
                if vloc is None:
                    n_bad += 1
                    aud.quotes_failed += 1
                    aud.quote_failures.append(label)
                    note = (
                        f"{label}: FAIL - value {val!r} is absent from both the quote "
                        "and the paper. Either the value is wrong or this cell is NR."
                    )
                else:
                    note += (
                        f" | WARN: {val!r} is not inside the quote (it is on p{vloc.page}). "
                        "Re-quote the span that actually prints this value."
                    )
        lines.append(note)

    header = f"{len(lines)} quotes checked, {n_bad} unusable.\n"
    return {"content": [{"type": "text", "text": header + "\n".join(lines)}],
            "is_error": n_bad > 0}


@tool(
    "check_envelope",
    "Dry-run your candidate envelope against the exact output schema plus the "
    "repo's cell-shape rules. Returns precise errors. Use this ONLY for small "
    "tables - on a tall table re-emitting the whole payload costs more than it "
    "saves.",
    {
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "envelope_json": {
                "type": "string",
                "description": "the full candidate envelope, JSON-encoded as a string",
            },
        },
        "required": ["run_id", "envelope_json"],
    },
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def check_envelope(args: Dict[str, Any]) -> Dict[str, Any]:
    aud = _audit(args["run_id"])
    if aud is None:
        return _err(f"unknown run_id {args['run_id']!r}")
    aud.envelope_checks += 1
    aud.tool_calls.append("check_envelope")
    try:
        candidate = json.loads(args["envelope_json"])
    except json.JSONDecodeError as e:
        return _err(f"not valid JSON: {e}")

    problems = validate_envelope(candidate, aud.field_name, aud.schema)
    if aud.plan_committed:
        rows = candidate.get(aud.field_name, {}).get("value")
        if isinstance(rows, list) and len(rows) != len(aud.plan_rows):
            problems.append(
                f"row count {len(rows)} != committed plan {len(aud.plan_rows)}"
            )
    if problems:
        return _err("Envelope rejected:\n- " + "\n- ".join(problems[:25]))
    return _ok("Envelope valid. Emit it as your structured output verbatim.")


EVISTREAM_SERVER = create_sdk_mcp_server(
    name="evistream",
    version="1.0.0",
    tools=[commit_row_plan, verify_quotes, check_envelope],
)

MCP_TOOL_NAMES = [
    "mcp__evistream__commit_row_plan",
    "mcp__evistream__verify_quotes",
    "mcp__evistream__check_envelope",
]


# ═════════════════════════════════════════════════════════════════════════════
# 4. PYTHON-SIDE VALIDATION + GROUNDING SWEEP
# ═════════════════════════════════════════════════════════════════════════════
def validate_envelope(
    env: Dict[str, Any], field_name: str, schema: Dict[str, Any]
) -> List[str]:
    """Structural + repo-contract validation. Cheap, exhaustive, runs in Python."""
    problems: List[str] = []
    if not isinstance(env, dict) or field_name not in env:
        return [f"top-level object must have exactly one key: {field_name!r}"]

    outer = env[field_name]
    if not isinstance(outer, dict):
        return [f"{field_name} must be an object {{value, source_text, status}}"]
    for k in ("value", "source_text", "status"):
        if k not in outer:
            problems.append(f"{field_name}.{k} missing")
    if absence.normalize_status(outer.get("status")) is None:
        problems.append(f"status must be one of {_STATUS_VALUES}, got {outer.get('status')!r}")

    rows = outer.get("value")
    if rows == _NR:
        return problems
    if not isinstance(rows, list):
        problems.append(f"{field_name}.value must be an array (or \"NR\")")
        return problems

    row_schema = (
        schema.get("properties", {}).get(field_name, {})
        .get("properties", {}).get("value", {})
    )
    if "items" not in row_schema and "oneOf" in row_schema:
        row_schema = next((b for b in row_schema["oneOf"] if b.get("type") == "array"), {})
    cols = list(row_schema.get("items", {}).get("properties", {}).keys())
    col_specs = row_schema.get("items", {}).get("properties", {})

    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"row {i} is {type(row).__name__}, must be an object")
            continue
        for extra in set(row) - set(cols):
            problems.append(f"row {i}: unknown column {extra!r}")
        for c in cols:
            if c not in row:
                problems.append(f"row {i}: column {c!r} missing")
                continue
            cell = row[c]
            if not (isinstance(cell, dict) and "value" in cell and "source_text" in cell):
                problems.append(
                    f'row {i}.{c}: must be {{"value":...,"source_text":...}}, '
                    f"got {type(cell).__name__}"
                )
                continue
            allowed = col_specs.get(c, {}).get("properties", {}).get("value", {}).get("enum")
            if allowed and str(cell["value"]) not in allowed:
                problems.append(
                    f"row {i}.{c}: {cell['value']!r} is not an allowed option "
                    f"({allowed[:6]}...)"
                )
    return problems


def ground_envelope(
    env: Dict[str, Any],
    field_name: str,
    markdown: str,
    field_def: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Run every cell's quote through source_linker. Returns (enriched, failures).

    This is the exhaustive pass. It costs nothing (no model tokens) and it is the
    same code path the backend runs, so a cell that grounds here grounds in prod.

    Columns that carry `options` are exempt from the value-must-appear-literally
    check: their values are normalisations ("6_months" from "6 months", "RCT" from
    "randomized controlled trial"), and the repo contract only requires the quote
    to contain the phrase the value was derived from.
    """
    if not _HAVE_SOURCE_LINKER:
        return env, []
    idx = build_source_index(markdown, parse_page_boundaries(markdown))
    normalized_cols = {
        c["field_name"] for c in ((field_def or {}).get("subform_fields") or [])
        if c.get("options")
    }
    failures: List[Dict[str, Any]] = []

    rows = env.get(field_name, {}).get("value")
    if isinstance(rows, list):
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for cname, cell in row.items():
                if not (isinstance(cell, dict) and "source_text" in cell):
                    continue
                q, v = cell.get("source_text", ""), cell.get("value")
                if str(v).strip().upper() == _NR or v in (None, "", []):
                    continue          # deliberate NR needs no quote
                if str(q).strip().upper() in ("NR", ""):
                    failures.append(
                        {"label": f"row{i}.{cname}", "value": v, "quote": q,
                         "reason": "value present but no quote (source_text is NR/empty)"}
                    )
                    continue
                loc = locate_source(q, idx, threshold=CFG.grounding_threshold)
                if loc is None or loc.confidence < CFG.grounding_threshold:
                    failures.append(
                        {"label": f"row{i}.{cname}", "value": v, "quote": q,
                         "reason": "quote not found in the paper (paraphrased or stitched)"}
                    )
                    continue
                if cname in normalized_cols:
                    continue          # normalised code: quote resolution is enough
                hay = re.sub(r"\s+", " ", str(q)).lower()
                if str(v).strip().lower() not in hay and locate_value(v, idx) is None:
                    failures.append(
                        {"label": f"row{i}.{cname}", "value": v, "quote": q,
                         "reason": "value absent from both the quote and the paper"}
                    )
    # Attach page/bbox metadata using the production enricher.
    try:
        enriched = enrich_extraction_results(env, markdown)
    except Exception:
        enriched = env
    return enriched, failures


def grounding_profile(enriched: Dict[str, Any], field_name: str) -> Dict[str, int]:
    """Tally WHERE the grounded values actually came from.

    A table field's data is frequently not in a table at all. This makes that
    measurable instead of assumed: `md_table` counts quotes that are pipe rows,
    `prose` counts sentence quotes, `nr` counts deliberate not-reporteds, and
    `value_*` counts cells where the model's quote was too weak to use and
    source_linker fell back to locating the bare value.
    """
    prof = {"md_table": 0, "prose": 0, "nr": 0, "ungrounded": 0,
            "quote_exact": 0, "quote_fuzzy": 0, "value_table": 0, "value_text": 0}
    rows = enriched.get(field_name, {}).get("value")
    if not isinstance(rows, list):
        return prof
    for row in rows:
        if not isinstance(row, dict):
            continue
        for cell in row.values():
            if not isinstance(cell, dict):
                continue
            q = str(cell.get("source_text", ""))
            if str(cell.get("value")).strip().upper() == _NR:
                prof["nr"] += 1
                continue
            prof["md_table" if q.count("|") >= 2 else "prose"] += 1
            loc = cell.get("source_location")
            if not loc:
                prof["ungrounded"] += 1
                continue
            m = loc.get("grounding_method")
            if m in prof:
                prof[m] += 1
    return prof


def normalize_envelope(
    env: Dict[str, Any],
    field_name: str,
    field_def: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Empty row array -> the explicit not_reported envelope, mirroring
    `_TwoStageExtractor` (`{"value":"NR","source_text":"NR","status":"not_reported"}`).

    Also brings this path into the one status vocabulary the rest of the system
    reads: "extracted" becomes "reported", and every row cell gets a stamped
    status (the agentic cell schema deliberately does not ask the model for one,
    so it is derived here) with the column's declared options taking precedence.
    """
    outer = env.get(field_name)
    if isinstance(outer, dict) and outer.get("value") == []:
        return {
            field_name: {
                "value": _NR,
                "source_text": _NR,
                "status": absence.NOT_REPORTED,
            }
        }
    if isinstance(outer, dict) and outer.get("value") == _NR:
        outer.setdefault("source_text", _NR)
        outer["status"] = absence.NOT_REPORTED
        return env

    if isinstance(outer, dict):
        canonical = absence.normalize_status(outer.get("status"))
        if canonical is not None:
            outer["status"] = canonical
        col_options = {
            sf["field_name"]: sf["options"]
            for sf in ((field_def or {}).get("subform_fields") or [])
            if sf.get("options")
        }
        rows = outer.get("value")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for col, cell in list(row.items()):
                    if isinstance(cell, dict) and "value" in cell:
                        row[col] = absence.stamp(cell, col_options.get(col))
    return env


# ═════════════════════════════════════════════════════════════════════════════
# 5. PROMPTS
# ═════════════════════════════════════════════════════════════════════════════
SYSTEM_APPEND = """
You are a systematic-review data extractor. Your only source of truth is the paper
text placed in your first user message. You never use outside knowledge, and you
never infer a number the paper does not print.

Non-negotiable working rules:
1. ROWS FIRST. Enumerate every distinct row identity before you look at a single
   measured value, and register it with commit_row_plan. Rows are the product of
   the identity columns (e.g. outcome x subgroup x timepoint) - a paper reporting
   3 outcomes at 2 timepoints has 6 rows, not 3.
2. A missing row is the most expensive error you can make: it silently deletes a
   whole record. "Not reported" for a cell is cheap and correct; a missing row is
   neither. When in doubt about whether a row exists, include it with NR cells.
3. NEVER write the table, or any cell value, into your prose. Cite line numbers
   and short quotes only. The table is emitted exactly once, as the structured
   output. Restating it costs real money.
4. Every quote must be a contiguous span copied byte-for-byte from the paper -
   a sentence, or a table row exactly as printed including its pipes and labels.
   Never paraphrase, never stitch two sentences together.
5. A value not printed in the paper is "NR". Fabricating, unit-converting, or
   deriving a value is a hard error. Extract what is printed.
6. Grep is free and you are cheap on read turns: use Grep to prove a row exists,
   to find a table that continues past a page break, and to prove absence before
   you write NR.

THE DATA IS NOT NECESSARILY IN A TABLE. "Table field" describes the SHAPE of your
answer (one row per identity), not where the numbers live. For every cell, search
all four locations, in this order, and stop at the first that reports it:
   (a) a results table row;
   (b) a prose sentence in Results or Abstract ("HbA1c fell from 8.1% to 7.4% at
       6 months in the test group") - these ground more reliably than table rows,
       so prefer the sentence when both exist and they agree;
   (c) a figure caption or table footnote;
   (d) nowhere in the text -> the cell is NR.
Case (d) includes the common trap of a value that appears ONLY inside a figure
image: the markdown has no text for it, so there is nothing to quote and nothing
to extract. Write NR. A number you can see in a chart but cannot quote is a
fabrication. Likewise, two printed numbers you would have to add, subtract, or
convert are NOT a third printed number - that is derivation, so NR.
If a table and the prose disagree, take the table, and say so in your reply.
""".strip()


def compose_field_brief(sig_def: Dict[str, Any], field_def: Dict[str, Any]) -> str:
    """Render the table field spec. Prefers the repo's own `_compose_field_desc`
    so the agent reads the identical hints/rules/options/examples text the DSPy
    path reads - one source of truth for prompt content."""
    if _HAVE_COMPOSER:
        try:
            return _compose_field_desc(field_def)
        except Exception:
            pass
    parts = [field_def.get("description", "")]
    for header, key in (("Extraction Hints", "hints"), ("Rules", "rules")):
        items = field_def.get(key) or []
        if items:
            parts.append(f"\n{header}:")
            parts += [f"- {x}" for x in items]
    anchors = set(field_key_columns(field_def))
    parts.append("\nTable Columns:")
    for col in field_def.get("subform_fields") or []:
        role = "ROW IDENTITY" if col["field_name"] in anchors else "MEASURED VALUE"
        parts.append(f"  - {col['field_name']} ({col.get('field_type','string')}) [{role}]"
                     f": {col.get('field_description','')}")
        for o in col.get("options") or []:
            parts.append(f'      - Option: "{o}"')
        for h in col.get("hints") or []:
            parts.append(f"      - Hint: {h}")
        for r in col.get("rules") or []:
            parts.append(f"      - Rule: {r}")
    return "\n".join(p for p in parts if p)


def build_prompt(
    run_id: str,
    paper_name: str,
    markdown: str,
    sig_def: Dict[str, Any],
    field_def: Dict[str, Any],
    schema: Dict[str, Any],
    cfg: Config,
    allow_precheck: bool,
    use_recall_audit: bool,
) -> str:
    """Paper FIRST, spec second: keeps `system | paper` a shared cacheable prefix
    across every field-session for this paper."""
    anchors = field_key_columns(field_def)
    values = [c["field_name"] for c in (field_def.get("subform_fields") or [])
              if c["field_name"] not in set(anchors)]

    recall_audit_step = ""
    if use_recall_audit:
        recall_audit_step = (
            "\nSTEP 1b - INDEPENDENT CENSUS. Call the Agent tool with "
            "subagent_type 'row-census' and prompt it with: the absolute path "
            f"{paper_name!r} on disk (./paper.md), the row-identity columns "
            f"{anchors}, and their definitions. It has never seen your answer, so "
            "treat any row it finds that you missed as real: add it. Reconcile "
            "before you call commit_row_plan.\n"
        )

    precheck_step = (
        "\nSTEP 4 - SHAPE CHECK. Call check_envelope once with your candidate "
        "envelope. Fix whatever it rejects.\n"
        if allow_precheck else
        "\nSTEP 4 - SKIPPED. This table is too tall to dry-run; the schema is "
        "enforced on your structured output directly. Get the shape right first "
        "time: every cell is an object with exactly `value` and `source_text`.\n"
    )

    return f"""<paper name="{paper_name}">
{markdown}
</paper>

RUN_ID: {run_id}
(Pass this exact RUN_ID to every evistream tool call.)

The paper above is also on disk at ./paper.md - use Grep against that path to
locate, count and confirm; use Read with offset/limit to re-read a table that
spans a page break. Do not Read the whole file, it is already above.

════════════════════════════════════════════════════════════════════════
TASK: extract the table field `{field_def['name']}` from this paper.
════════════════════════════════════════════════════════════════════════
{sig_def.get('docstring', '')}

FIELD SPECIFICATION
{compose_field_brief(sig_def, field_def)}

ROW IDENTITY COLUMNS : {anchors}
MEASURED VALUE COLUMNS: {values}

════════════════════════════════════════════════════════════════════════
PROCEDURE
════════════════════════════════════════════════════════════════════════
STEP 1 - ROW CENSUS. Find every distinct row identity the paper reports data for,
wherever that data lives. Grep for every allowed value (and its synonyms) of every
identity column. Sweep all four locations:
  - results tables, INCLUDING continuations past a page separator ({{N}}------);
  - Results and Abstract prose, which frequently reports outcomes that never
    appear in any table;
  - figure captions and table footnotes;
  - the Methods outcome list, which tells you which outcomes/timepoints were
    measured and therefore which rows SHOULD exist.
A row whose identity is reported but whose numbers are absent is still a row: it
gets NR value cells. Do not drop it. Write down where each row lives (line
numbers), not its values.
{recall_audit_step}
STEP 2 - COMMIT. Call commit_row_plan with the full row list and your evidence.
This fixes the row count. You cannot finish without it.

STEP 3 - VERIFY GROUNDING ON A SAMPLE. Call verify_quotes ONCE with:
  - the identifying quote for every row you registered, and
  - the quotes for up to {cfg.sample_value_cells} measured cells, chosen from the
    rows and columns you are least sure about.
If any come back FAIL, your quoting method is wrong - fix it for ALL cells, not
just the ones that failed.
{precheck_step}
STEP 5 - EMIT. Produce the structured output. Exactly the committed rows. Every
column present in every row. Every cell `{{"value":..., "source_text":...}}`.
A cell whose value is not printed anywhere in the text - including one you can
only see in a figure, or one you would have to derive from two other numbers -
is `{{"value":"NR","source_text":"NR"}}`. Every result is re-checked against the
paper by a deterministic resolver after you finish, so an unquotable value will
be caught and sent back to you; NR now is cheaper than NR after a round trip.
`status` is "reported"
when you found rows, "partial" if you know the paper has rows you could not
resolve, "not_reported" with `value: []` only if this paper reports no such table
at all.

Do not print the table in your reply. Reply with one line: the row count and
anything a human reviewer should double-check.
"""


REPAIR_PROMPT = """The extraction is structurally fine but {n} cell(s) failed the
deterministic grounding check that runs on every result. A cell fails when its
source_text cannot be located in the paper - meaning it was paraphrased, stitched
from two places, or reformatted.

Failed cells:
{failures}

Fix ONLY these cells. For each one:
  - Grep ./paper.md for the value, find the exact line, and copy that line or
    sentence byte-for-byte (including pipes and labels for table rows).
  - If the value genuinely is not in the paper, the cell is
    {{"value":"NR","source_text":"NR"}} - say so.
Call verify_quotes with the corrected quotes before you emit.

Re-emit the COMPLETE envelope as structured output, with every other cell byte
identical to before.
"""


ROW_REPAIR_PROMPT = """Your structured output is missing {n} row(s) that you
registered with commit_row_plan. The committed plan is this table's row inventory,
so every identity in it must be accounted for in the output.

Missing row identities:
{missing}

For EACH missing row do exactly one of these. Never invent a number to fill a gap.

  1. The paper reports values for it and you dropped it: extract them now, with a
     verbatim source_text per cell, to the same standard as the other rows.
  2. The row exists but the paper reports no numbers for it: emit the row with its
     anchor values and every value cell set to {{"value":"NR","source_text":"NR"}}.
  3. The identity does not exist in this paper and your plan was wrong: do NOT
     emit it, and state in your reply which identity you are withdrawing and why.
     A withdrawn row is recorded, not hidden - an honest withdrawal is a correct
     answer here.

Re-emit the COMPLETE envelope as structured output. Every row already present must
be byte identical to before.
"""


def _row_key(row: Dict[str, Any], anchors: List[str]) -> str:
    """Canonical record identity, computed on the composite key only.

    Value columns are excluded on purpose: the same row is 'the same row' whether
    or not its measurements came through. Accepts both the flat plan shape and the
    {value, source_text} cell shape.
    """
    flat: Dict[str, str] = {}
    for a in anchors:
        v = row.get(a)
        if isinstance(v, dict):
            v = v.get("value")
        flat[a] = str(v).strip().lower() if v is not None else ""
    return json.dumps(flat, sort_keys=True)


def missing_plan_rows(
    plan_rows: List[Dict[str, Any]],
    emitted_rows: Any,
    anchors: List[str],
) -> List[Dict[str, Any]]:
    """Committed row identities that never made it into the emitted table.

    Returns [] when the field has no composite key - identities can't be matched
    then, so the caller falls back to a count comparison.
    """
    if not anchors or not plan_rows or not isinstance(emitted_rows, list):
        return []
    have = {_row_key(r, anchors) for r in emitted_rows if isinstance(r, dict)}
    return [p for p in plan_rows if _row_key(p, anchors) not in have]

TURN_LIMIT_PROMPT = """You ran out of turns. Stop investigating. Emit the
structured output NOW from what you already established, using
{{"value":"NR","source_text":"NR"}} for anything you could not confirm and
status "partial". Do not call any more tools.
"""


# ═════════════════════════════════════════════════════════════════════════════
# 6. SUBAGENT (optional, opt-in)
# ═════════════════════════════════════════════════════════════════════════════
def row_census_agent(cfg: Config) -> AgentDefinition:
    """A fresh-context row counter.

    This is the one place a subagent earns its keep: context isolation is the
    MECHANISM, not a side effect. A same-context "did you miss anything?" turn is
    anchored on the answer it is auditing; a subagent that has never seen that
    answer is genuinely independent. It costs one extra cache-write of the paper
    (~16k tokens) and emits only anchor tuples, so it is the cheapest possible
    second opinion on the error that hurts most.

    Note `disallowedTools` / `mcpServers` keep camelCase in the Python SDK.
    """
    return AgentDefinition(
        description=(
            "Independent row census for a table field. Reads the paper cold and "
            "reports every distinct row identity it can find, with line numbers. "
            "Never extracts measured values."
        ),
        prompt=(
            "You count rows in a clinical paper's results tables and nothing else.\n"
            "Read ./paper.md. You will be told the row-identity columns and their "
            "allowed values.\n"
            "Enumerate EVERY distinct combination of those identity columns that "
            "the paper reports data for. Grep for every allowed value of every "
            "identity column - do not rely on reading the tables you happen to "
            "notice. Tables continue past page separators ({N}------); follow them.\n"
            "Data is NOT only in tables. Check Results and Abstract prose, figure "
            "captions, table footnotes, and the Methods outcome list - an outcome "
            "the Methods say was measured is a row even if no table shows it.\n"
            "Report as a compact list, one line per row: the identity values, then "
            "the line number where you saw it. Then a single total.\n"
            "Do NOT report measured values, means, SDs, or counts. Do not comment "
            "on data quality. Rows and line numbers only."
        ),
        tools=["Read", "Grep"],
        model=cfg.recall_audit_model,
        effort=cfg.recall_audit_effort,
        maxTurns=8,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 7. HOOKS
# ═════════════════════════════════════════════════════════════════════════════
def make_hooks(cfg: Config, run_id: str):
    """PostToolUse: steer without spending a turn. Stop: flush the audit.

    The Python hooks API documents `systemMessage`, `continue`/`continue_` and
    `hookSpecificOutput`; it does NOT document a Stop-hook field that forces the
    agent to keep working. So the HARD gate (plan committed, rows reconciled,
    quotes grounded) is enforced in the driver, which is also where
    `structured_output` actually lands. The Stop hook only records.
    """

    async def nudge_after_verify(input_data, tool_use_id, context):
        if input_data.get("hook_event_name") != "PostToolUse":
            return {}
        aud = _audit(run_id)
        if aud is None:
            return {}
        extra = []
        if aud.quotes_failed:
            extra.append(
                f"{aud.quotes_failed} of {aud.quotes_checked} sampled quotes were "
                "unusable. Apply the same fix to every cell you have not checked."
            )
        if aud.plan_committed:
            extra.append(f"Reminder: the committed plan has {len(aud.plan_rows)} rows.")
        if not extra:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": " ".join(extra),
            }
        }

    async def block_plan_skip(input_data, tool_use_id, context):
        """Deny check_envelope before a plan exists - forces rows-before-values."""
        if input_data.get("hook_event_name") != "PreToolUse":
            return {}
        aud = _audit(run_id)
        if aud is None or aud.plan_committed:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "commit_row_plan has not been called. Enumerate and register "
                    "the rows before you assemble any values."
                ),
            }
        }

    async def audit_tool_calls(input_data, tool_use_id, context):
        """Runs in OUR process, before every tool call, and costs no context.
        This is the ground-truth record of what the agent did - including calls
        that were denied, which never appear in the transcript as results."""
        if not CFG.log_tool_calls:
            return {}
        aud = _audit(run_id)
        if aud is not None:
            aud.tool_calls.append(str(input_data.get("tool_name", "?")))
            if aud.tracer is not None:
                aud.tracer.emit(
                    "hook_pre_tool",
                    tool=input_data.get("tool_name"),
                    tool_use_id=tool_use_id,
                    agent_id=input_data.get("agent_id"),
                    agent_type=input_data.get("agent_type"),
                    args=json.dumps(input_data.get("tool_input"), default=str),
                )
        return {}

    return {
        "PreToolUse": [
            # Gate EVERY evistream tool behind the row plan, not just
            # check_envelope. check_envelope is optional (and only offered on
            # short tables), so gating it alone left rows-before-values as a
            # prompt suggestion rather than a constraint. Read and Grep stay
            # open, so the forced order is: read/grep -> commit -> everything else.
            HookMatcher(matcher="mcp__evistream__check_envelope", hooks=[block_plan_skip]),
            HookMatcher(matcher="mcp__evistream__verify_quotes", hooks=[block_plan_skip]),
            HookMatcher(hooks=[audit_tool_calls]),
        ],
        "PostToolUse": [
            HookMatcher(matcher="mcp__evistream__verify_quotes", hooks=[nudge_after_verify]),
        ],
    }


# ═════════════════════════════════════════════════════════════════════════════
# 8. OPTIONS BUILDER
# ═════════════════════════════════════════════════════════════════════════════
def build_options(
    *,
    cfg: Config,
    run_id: str,
    workdir: Path,
    schema: Dict[str, Any],
    expected_rows: int,
    use_recall_audit: bool,
    resume: Optional[str] = None,
    n_attr_cols: int = 0,
    spent_so_far: float = 0.0,
) -> ClaudeAgentOptions:
    tools = ["Read", "Grep"]
    allowed = ["Read", "Grep", *MCP_TOOL_NAMES]
    agents = None
    if use_recall_audit:
        tools.append("Agent")
        allowed.append("Agent")
        agents = {"row-census": row_census_agent(cfg)}

    env = {
        "API_TIMEOUT_MS": str(cfg.api_timeout_ms),
        "CLAUDE_CODE_MAX_RETRIES": "3",
        # MCP results are small; cap them so a pathological verify_quotes reply
        # can never be spilled to disk and re-read.
        "MAX_MCP_OUTPUT_TOKENS": "8000",
    }
    if cfg.prompt_cache_1h:
        # Sibling field-sessions for one paper can land minutes apart; a 5-minute
        # TTL would expire the shared `system | paper` prefix between them.
        env["ENABLE_PROMPT_CACHING_1H"] = "1"

    max_turns = min(
        cfg.max_turns_cap,
        cfg.max_turns_base + (expected_rows // 4) * cfg.max_turns_per_4_rows,
    )
    # Session allowance scales on cells; then clamped by whatever is left of the
    # whole-extraction ceiling, so resume and repair legs draw down rather than
    # each receiving a fresh allowance.
    cells = max(1, expected_rows * max(1, n_attr_cols))
    session_budget = min(
        cfg.budget_usd_session_cap,
        cfg.budget_usd_base + cells * cfg.budget_usd_per_cell,
    )
    remaining = max(0.0, cfg.budget_usd_extraction_cap - spent_so_far)
    budget = min(session_budget, remaining)
    if budget <= 0.05:
        # Nothing meaningful left; let the caller see an immediate budget stop
        # rather than starting a session that cannot finish.
        budget = 0.05
    logger.info(
        "agentic budget: cells=%d session=$%.3f remaining=$%.3f -> $%.3f (effort=%s, turns=%d)",
        cells, session_budget, remaining, budget,
        cfg.effort_large if n_attr_cols > cfg.effort_col_switch else cfg.effort_small,
        max_turns,
    )

    return ClaudeAgentOptions(
        model=cfg.model,
        fallback_model=cfg.fallback_model,
        # Preset keeps the tool-use discipline and safety text; append carries the
        # extraction contract. exclude_dynamic_sections drops cwd/git/platform out
        # of the system prompt so every session on every worker shares one cache
        # entry for the system block.
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": SYSTEM_APPEND,
            "exclude_dynamic_sections": True,
        },
        # `tools` is the AVAILABILITY layer: anything not listed is not in context
        # at all, so it cannot be attempted and its schema costs nothing.
        tools=tools,
        # `allowed_tools` is the PERMISSION layer: these run without a prompt.
        allowed_tools=allowed,
        # Belt-and-braces: bare names stay out of context even if a preset re-adds
        # them. Write/Edit are excluded because the answer travels as structured
        # output - a file-writing escape hatch would let the agent "finish" into a
        # file nobody reads. Bash is excluded because there is nothing to run and
        # it would let text munging bypass the audited tool path. Web* is excluded
        # because outside knowledge is a fabrication vector for this task.
        disallowed_tools=[
            "Bash", "Write", "Edit", "NotebookEdit", "WebSearch", "WebFetch",
            "Glob", "TodoWrite", "TaskCreate", "TaskUpdate", "AskUserQuestion",
            "Skill", "SlashCommand",
        ] + ([] if use_recall_audit else ["Agent"]),
        permission_mode="dontAsk",   # locked surface: unlisted => hard deny, never prompts
        mcp_servers={"evistream": EVISTREAM_SERVER},
        strict_mcp_config=True,      # ignore any .mcp.json on the box
        output_format={"type": "json_schema", "schema": schema},
        agents=agents,
        max_turns=max_turns,
        max_budget_usd=budget,
        effort=(cfg.effort_large if n_attr_cols > cfg.effort_col_switch
                else cfg.effort_small),
        # In trace mode deliver the reasoning summaries instead of discarding
        # them; the tokens are generated and billed either way, so a run you
        # intend to audit should keep them.
        thinking=({**cfg.thinking, "display": "summarized"}
                  if cfg.trace and cfg.thinking.get("type") != "disabled"
                  else cfg.thinking),
        # Empty on purpose: the repo's CLAUDE.md and .claude hooks are about
        # editing evistream, not extracting tables. Loading them would inject
        # thousands of irrelevant tokens into every session and compete with the
        # extraction contract.
        setting_sources=[],
        cwd=str(workdir),
        env=env,
        resume=resume,
        hooks=make_hooks(cfg, run_id),
        include_partial_messages=False,
        stderr=(lambda line: None),
    )


# ═════════════════════════════════════════════════════════════════════════════
# 8b. TRACE: what the model thought and how it got the data
# ═════════════════════════════════════════════════════════════════════════════
class Tracer:
    """Append-only JSONL trace of one extraction session.

    Every record is {t, step, kind, ...}. Kinds:
      init        session id, model, tools actually available, mcp status
      thinking    the model's reasoning summary for that step
      text        the model's prose for that step
      tool_use    tool name + full arguments
      tool_result what came back (truncated)
      usage       per-step tokens, deduplicated by message id
      result      terminal subtype, stop_reason, cost, turns
    Written from the SDK message stream, so it reflects the real loop rather than
    anything the model claims about itself.
    """

    def __init__(self, path: Optional[Path], max_chars: int = 4000):
        self.path = path
        self.max_chars = max_chars
        self.step = 0
        self._seen_msg_ids: set = set()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("w", encoding="utf-8")
        else:
            self._fh = None

    def _clip(self, s: Any) -> Any:
        if isinstance(s, str) and len(s) > self.max_chars:
            return s[: self.max_chars] + f"...[+{len(s)-self.max_chars} chars]"
        return s

    def emit(self, kind: str, **fields) -> None:
        if self._fh is None:
            return
        rec = {"t": round(time.time(), 3), "step": self.step, "kind": kind}
        rec.update({k: self._clip(v) for k, v in fields.items()})
        self._fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def on_assistant(self, msg: Any) -> None:
        """One assistant response = one step of the loop."""
        if self._fh is None:
            return
        self.step += 1
        thinking = getattr(msg, "thinking", None)
        if thinking:
            self.emit("thinking", text=thinking)
        for block in getattr(msg, "content", None) or []:
            if isinstance(block, ToolUseBlock):
                self.emit("tool_use", tool=block.name, id=block.id,
                          args=json.dumps(block.input, default=str)[: self.max_chars])
            elif isinstance(block, TextBlock):
                self.emit("text", text=block.text)
            elif getattr(block, "type", "") == "thinking":
                self.emit("thinking", text=getattr(block, "thinking", "")
                          or getattr(block, "text", ""))
        # Per-step usage. Parallel tool calls share a message id with identical
        # usage, so dedupe by id or the totals inflate.
        mid = getattr(msg, "message_id", None)
        usage = getattr(msg, "usage", None)
        if usage and mid not in self._seen_msg_ids:
            self._seen_msg_ids.add(mid)
            u = usage if isinstance(usage, dict) else getattr(usage, "__dict__", {})
            self.emit("usage", message_id=mid,
                      input=u.get("input_tokens"), output=u.get("output_tokens"),
                      cache_read=u.get("cache_read_input_tokens"),
                      cache_write=u.get("cache_creation_input_tokens"))

    def on_user(self, msg: Any) -> None:
        if self._fh is None:
            return
        for block in getattr(msg, "content", None) or []:
            if isinstance(block, ToolResultBlock):
                content = block.content
                if not isinstance(content, str):
                    content = json.dumps(content, default=str)
                self.emit("tool_result", id=block.tool_use_id,
                          is_error=getattr(block, "is_error", False), content=content)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def render_trace(trace_path: Path, show_thinking: bool = True) -> str:
    """Human-readable timeline from a trace JSONL. Answers 'how did it get this?'"""
    out: List[str] = [f"── trace {trace_path.name}"]
    tok_in = tok_out = 0
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        k, step = r["kind"], r["step"]
        if k == "init":
            out.append(f"  session {r.get('session_id')}  model={r.get('model')}")
            out.append(f"  tools available: {r.get('tools')}")
        elif k == "thinking" and show_thinking:
            out.append(f"  [{step}] THINK  {str(r.get('text',''))[:600]}")
        elif k == "text":
            out.append(f"  [{step}] SAY    {str(r.get('text',''))[:400]}")
        elif k == "tool_use":
            out.append(f"  [{step}] CALL   {r.get('tool')}  {str(r.get('args',''))[:300]}")
        elif k == "tool_result":
            flag = " ERROR" if r.get("is_error") else ""
            out.append(f"  [{step}] RESULT{flag} {str(r.get('content',''))[:300]}")
        elif k == "usage":
            tok_in += int(r.get("cache_read") or 0) + int(r.get("input") or 0)
            tok_out += int(r.get("output") or 0)
        elif k == "result":
            out.append(f"  == {r.get('subtype')} stop={r.get('stop_reason')} "
                       f"turns={r.get('num_turns')} cost=${r.get('cost_usd')}")
    out.append(f"  tokens in(incl cache reads)={tok_in:,} out={tok_out:,}")
    return "\n".join(out)


# ═════════════════════════════════════════════════════════════════════════════
# 9. DRIVER: one (paper, table field)
# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class ExtractResult:
    paper: str
    field_name: str
    status: str = "failed"                  # ok | partial | failed
    envelope: Optional[Dict[str, Any]] = None
    n_rows: int = 0
    plan_rows: int = 0
    rows_missing: int = 0                   # committed identities absent from output
    rows_withdrawn: int = 0                 # plan entries the agent retracted, with reason
    subtype: Optional[str] = None
    stop_reason: Optional[str] = None
    session_id: Optional[str] = None
    cost_usd: float = 0.0
    num_turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    duration_ms: int = 0
    refill_rounds: int = 0
    quotes_checked: int = 0
    quotes_failed_inloop: int = 0
    cells_failed_grounding: int = 0
    # where the values actually came from: table rows vs prose vs NR
    cells_from_table: int = 0
    cells_from_prose: int = 0
    cells_nr: int = 0
    cells_value_anchored: int = 0
    tool_calls: str = ""
    trace_path: str = ""
    error: Optional[str] = None
    notes: str = ""


def _accumulate(res: ExtractResult, msg: ResultMessage) -> None:
    """Cost is read from EVERY result, success or error - tokens were spent either way."""
    res.subtype = msg.subtype
    res.stop_reason = getattr(msg, "stop_reason", None)
    res.session_id = getattr(msg, "session_id", None) or res.session_id
    res.cost_usd += float(getattr(msg, "total_cost_usd", None) or 0.0)
    res.num_turns += int(getattr(msg, "num_turns", None) or 0)
    usage = getattr(msg, "usage", None) or {}
    if isinstance(usage, dict):
        res.input_tokens += int(usage.get("input_tokens") or 0)
        res.output_tokens += int(usage.get("output_tokens") or 0)
        res.cache_read_tokens += int(usage.get("cache_read_input_tokens") or 0)
        res.cache_creation_tokens += int(usage.get("cache_creation_input_tokens") or 0)
    # total_input_tokens/total_output_tokens are also on ResultMessage; usage is
    # preferred here because it carries the cache split.
    if not res.input_tokens:
        res.input_tokens = int(getattr(msg, "total_input_tokens", None) or 0)
    if not res.output_tokens:
        res.output_tokens = int(getattr(msg, "total_output_tokens", None) or 0)


async def _run_once(
    prompt: str,
    options: ClaudeAgentOptions,
    res: ExtractResult,
    tracer: Optional[Tracer] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """One query() pass. Returns (structured_output, final_text)."""
    structured: Optional[Dict[str, Any]] = None
    final_text: Optional[str] = None
    tr = tracer or Tracer(None)
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, SystemMessage):
                if msg.subtype == "init":
                    data = msg.data or {}
                    res.session_id = data.get("session_id") or res.session_id
                    tr.emit("init", session_id=data.get("session_id"),
                            model=data.get("model"), tools=data.get("tools"),
                            mcp_servers=data.get("mcp_servers"),
                            paper=res.paper, field=res.field_name)
                    bad = [s for s in data.get("mcp_servers", [])
                           if s.get("status") in ("failed", "needs-auth")]
                    if bad:
                        res.notes += f"mcp unavailable: {bad}; "
                elif msg.subtype == "compact_boundary":
                    # Should never happen at ~40k context; if it does, the paper
                    # is an outlier (we have one at 82k tokens) and early history
                    # was summarised away.
                    res.notes += "COMPACTED; "
                    tr.emit("compact_boundary")
            elif isinstance(msg, AssistantMessage):
                tr.on_assistant(msg)
                for block in msg.content:
                    if isinstance(block, ToolUseBlock) and block.name in ("Agent", "Task"):
                        res.notes += f"subagent:{block.input.get('subagent_type')}; "
            elif isinstance(msg, UserMessage):
                tr.on_user(msg)
            elif isinstance(msg, ResultMessage):
                _accumulate(res, msg)
                structured = getattr(msg, "structured_output", None)
                tr.emit("result", subtype=msg.subtype,
                        stop_reason=getattr(msg, "stop_reason", None),
                        num_turns=getattr(msg, "num_turns", None),
                        cost_usd=getattr(msg, "total_cost_usd", None),
                        model_usage=getattr(msg, "model_usage", None),
                        has_structured_output=structured is not None)
                if msg.subtype == "success":
                    r = msg.result
                    final_text = r if isinstance(r, str) else json.dumps(r) if r else None
    except Exception as e:
        # A single-shot query() raises AFTER yielding the error result, so cost
        # is already accumulated above.
        res.error = f"{type(e).__name__}: {str(e)[:300]}"
        tr.emit("exception", error=res.error)
    return structured, final_text


def _salvage_json(text: str, field_name: str) -> Optional[Dict[str, Any]]:
    """Last resort: pull the first JSON object out of prose (same trick as
    run_agent_extraction.py::_parse_extraction)."""
    if not text:
        return None
    for start in (m.start() for m in re.finditer(r"\{", text)):
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(obj, dict) and field_name in obj:
            return obj
    return None


async def extract_table(
    *,
    paper_path: Path,
    sig_def: Dict[str, Any],
    field_def: Dict[str, Any],
    cfg: Config = CFG,
    expected_rows: Optional[int] = None,
    trace_dir: Optional[Path] = None,
) -> ExtractResult:
    """Extract one table field from one paper. Never raises."""
    field_name = field_def["name"]
    paper = paper_path.stem
    res = ExtractResult(paper=paper, field_name=field_name)
    markdown = paper_path.read_text(encoding="utf-8", errors="replace")
    schema = build_output_schema(field_def, strict_nr_union=cfg.strict_nr_union_schema)

    n_cols = len(field_def.get("subform_fields") or [])
    # Value columns are what Stage-2-style work actually fills, and what the
    # output token count scales with. Anchors are cheap by comparison.
    _anchor_set = set(field_key_columns(field_def))
    n_attr_cols = len([
        c for c in (field_def.get("subform_fields") or [])
        if c.get("field_name") not in _anchor_set
    ]) or n_cols
    # Row count is genuinely unknown before reading the paper. This guess only
    # sizes turns and budget — it deliberately no longer influences effort, and
    # it is intentionally an OVER-estimate now: guessing high costs headroom,
    # guessing low costs the whole run.
    exp_rows = expected_rows if expected_rows is not None else 10

    run_id = f"{paper[:24].replace(' ', '_')}::{field_name}::{os.getpid()}::{int(time.time()*1000)%10**7}"
    aud = SessionAudit(
        run_id=run_id, paper=paper, field_name=field_name,
        markdown=markdown, schema=schema,
        normalized_cols={
            c["field_name"] for c in (field_def.get("subform_fields") or [])
            if c.get("options")
        },
    )
    trace_path = None
    if cfg.trace:
        tdir = trace_dir or AGENTIC_TRACE_DIR
        trace_path = tdir / f"{paper}__{field_name}.jsonl"
    tracer = Tracer(trace_path, cfg.trace_max_chars)
    aud.tracer = tracer
    res.trace_path = str(trace_path) if trace_path else ""
    AUDITS[run_id] = aud

    workdir = Path(tempfile.mkdtemp(prefix=f"sdktab_{field_name}_"))
    t0 = time.time()
    try:
        # Isolated cwd holding exactly one file: Grep/Read cannot wander, and the
        # agent cannot accidentally ground a quote in another paper.
        shutil.copyfile(paper_path, workdir / "paper.md")

        allow_precheck = exp_rows <= cfg.envelope_precheck_max_rows
        prompt = build_prompt(
            run_id, paper, markdown, sig_def, field_def, schema, cfg,
            allow_precheck=allow_precheck, use_recall_audit=cfg.use_recall_audit_subagent,
        )
        options = build_options(
            cfg=cfg, run_id=run_id, workdir=workdir, schema=schema,
            expected_rows=exp_rows, use_recall_audit=cfg.use_recall_audit_subagent,
            n_attr_cols=n_attr_cols, spent_so_far=0.0,
        )

        structured, final_text = await asyncio.wait_for(
            _run_once(prompt, options, res, tracer), timeout=cfg.session_timeout_s
        )

        # ---- recovery ladder -------------------------------------------------
        if structured is None and res.subtype in (
            "error_max_turns", "error_max_budget_usd",
        ) and res.session_id:
            # Resume: the prefix is still cached, so "just emit" is nearly free.
            res.notes += f"resume_after:{res.subtype}; "
            opts2 = build_options(
                cfg=cfg, run_id=run_id, workdir=workdir, schema=schema,
                expected_rows=exp_rows, use_recall_audit=False, resume=res.session_id,
                n_attr_cols=n_attr_cols, spent_so_far=res.cost_usd,
            )
            opts2.max_turns = 2
            # Draw down the extraction ceiling rather than granting a fresh
            # allowance. If nothing is left, this is a no-op session that fails
            # immediately instead of doubling the bill.
            opts2.max_budget_usd = max(
                0.05,
                min(res.cost_usd + 0.15,
                    cfg.budget_usd_extraction_cap - res.cost_usd),
            )
            opts2.tools = []                        # no built-ins: emit only
            opts2.allowed_tools = []
            opts2.mcp_servers = {}                  # drop MCP schemas from context
            opts2.effort = "low"                    # nothing left to reason about
            structured, final_text = await _run_once(TURN_LIMIT_PROMPT, opts2, res, tracer)

        if structured is None and res.subtype == "error_max_structured_output_retries":
            res.notes += "schema_retries_exhausted; "

        if structured is None:
            structured = _salvage_json(final_text or "", field_name)
            if structured is not None:
                res.notes += "salvaged_from_text; "

        if structured is None:
            res.status = "failed"
            res.error = res.error or f"no structured output ({res.subtype})"
            return res

        # ---- validate --------------------------------------------------------
        problems = validate_envelope(structured, field_name, schema)
        if problems:
            res.notes += f"schema_problems:{len(problems)}; "
            res.error = "; ".join(problems[:3])

        structured = normalize_envelope(structured, field_name, field_def)
        rows = structured.get(field_name, {}).get("value")
        res.n_rows = len(rows) if isinstance(rows, list) else 0
        res.plan_rows = len(aud.plan_rows)

        # ---- exhaustive grounding sweep + targeted repair --------------------
        enriched, failures = ground_envelope(structured, field_name, markdown, field_def)
        res.cells_failed_grounding = len(failures)

        anchors = field_key_columns(field_def)
        missing = (
            missing_plan_rows(aud.plan_rows, rows, anchors)
            if aud.plan_committed else []
        )
        res.rows_missing = len(missing)

        rounds = 0
        while rounds < cfg.max_repair_rounds and res.session_id:
            do_grounding = (
                bool(failures) and len(failures) >= cfg.repair_min_failed_cells
            )
            # A committed row that never got emitted is a worse defect than a quote
            # that won't resolve, so rows are repaired first when both are present.
            do_rows = bool(missing) or (
                aud.plan_committed and not anchors
                and res.n_rows < len(aud.plan_rows)
            )
            if not (do_grounding or do_rows):
                break
            rounds += 1

            # Stop repairing if the extraction ceiling is already spent — a
            # repair that cannot finish is pure waste.
            if res.cost_usd >= cfg.budget_usd_extraction_cap - 0.10:
                res.notes += "repair_skipped_budget_exhausted; "
                logger.warning(
                    "agentic %s/%s: skipping repair, $%.3f of $%.2f extraction cap spent",
                    paper, field_name, res.cost_usd, cfg.budget_usd_extraction_cap,
                )
                break

            opts_r = build_options(
                cfg=cfg, run_id=run_id, workdir=workdir, schema=schema,
                expected_rows=exp_rows, use_recall_audit=False, resume=res.session_id,
                n_attr_cols=n_attr_cols, spent_so_far=res.cost_usd,
            )
            opts_r.max_turns = 6
            opts_r.max_budget_usd = max(
                0.05,
                min(res.cost_usd + 0.25,
                    cfg.budget_usd_extraction_cap - res.cost_usd),
            )

            if do_rows:
                if missing:
                    n_missing = len(missing)
                    miss_txt = "\n".join(
                        f"  - {json.dumps(m, sort_keys=True, default=str)}"
                        for m in missing[:40]
                    )
                else:
                    # No composite key: the records can't be named, only counted.
                    n_missing = len(aud.plan_rows) - res.n_rows
                    miss_txt = (
                        f"  - (this table declares no anchor columns, so the missing "
                        f"rows cannot be named individually: you committed "
                        f"{len(aud.plan_rows)} rows and emitted {res.n_rows})"
                    )
                prompt_r = ROW_REPAIR_PROMPT.format(n=n_missing, missing=miss_txt)
                tracer.emit("row_repair_round", round=rounds, missing_rows=n_missing,
                            identities=missing[:40])
            else:
                fail_txt = "\n".join(
                    f"  - {f['label']}: value={f['value']!r} quote={f['quote'][:110]!r} "
                    f"({f['reason']})"
                    for f in failures[:40]
                )
                prompt_r = REPAIR_PROMPT.format(n=len(failures), failures=fail_txt)
                tracer.emit("repair_round", round=rounds, failed_cells=len(failures),
                            labels=[f["label"] for f in failures[:40]])

            repaired, _ = await _run_once(prompt_r, opts_r, res, tracer)
            if repaired is None:
                break
            if validate_envelope(repaired, field_name, schema):
                break
            repaired = normalize_envelope(repaired, field_name, field_def)
            new_rows = repaired.get(field_name, {}).get("value")
            if isinstance(new_rows, list) and len(new_rows) < res.n_rows:
                if do_rows:
                    # Option 3 of ROW_REPAIR_PROMPT: the agent retracted a row it
                    # had wrongly planned. Permitted here, but always recorded -
                    # the alternative is pressuring it to fill a quota.
                    res.rows_withdrawn += res.n_rows - len(new_rows)
                    res.notes += "row_repair_withdrew_rows; "
                else:
                    res.notes += "repair_dropped_rows_rejected; "
                    break
            structured = repaired
            rows = new_rows
            res.n_rows = len(new_rows) if isinstance(new_rows, list) else 0
            enriched, failures = ground_envelope(structured, field_name, markdown, field_def)
            res.cells_failed_grounding = len(failures)
            missing = (
                missing_plan_rows(aud.plan_rows, rows, anchors)
                if aud.plan_committed else []
            )
            res.rows_missing = len(missing)
        res.refill_rounds = rounds

        # ---- row reconciliation (the hard gate) ------------------------------
        if aud.plan_committed and res.n_rows != len(aud.plan_rows):
            res.notes += f"ROW_MISMATCH plan={len(aud.plan_rows)} emitted={res.n_rows}; "
        if res.rows_missing:
            # Survived a repair round and is still absent - a reviewer must see this.
            res.notes += f"ROWS_UNRESOLVED={res.rows_missing}; "
        if not aud.plan_committed:
            res.notes += "NO_ROW_PLAN; "

        prof = grounding_profile(enriched, field_name)
        res.cells_from_table = prof["md_table"]
        res.cells_from_prose = prof["prose"]
        res.cells_nr = prof["nr"]
        res.cells_value_anchored = prof["value_table"] + prof["value_text"]

        res.envelope = enriched
        res.quotes_checked = aud.quotes_checked
        res.quotes_failed_inloop = aud.quotes_failed
        res.tool_calls = ",".join(aud.tool_calls[:40])
        res.status = (
            "partial"
            if (problems or failures or "ROW_MISMATCH" in res.notes
                or res.rows_missing
                or not aud.plan_committed          # completeness was never verifiable
                or structured.get(field_name, {}).get("status") == "partial")
            else "ok"
        )
        return res

    except asyncio.TimeoutError:
        res.error = f"timeout after {cfg.session_timeout_s}s"
        return res
    except Exception as e:
        res.error = f"{type(e).__name__}: {str(e)[:300]}"
        return res
    finally:
        res.duration_ms = int((time.time() - t0) * 1000)
        tracer.emit("done", status=res.status, n_rows=res.n_rows,
                    plan_rows=res.plan_rows, cost_usd=round(res.cost_usd, 6),
                    cells_from_table=res.cells_from_table,
                    cells_from_prose=res.cells_from_prose, cells_nr=res.cells_nr,
                    cells_failed_grounding=res.cells_failed_grounding,
                    notes=res.notes, error=res.error)
        tracer.close()
        shutil.rmtree(workdir, ignore_errors=True)
        AUDITS.pop(run_id, None)



# ═════════════════════════════════════════════════════════════════════════════
# 10. RUNTIME ADAPTER — the seam consumed by build_schema_classes
# ═════════════════════════════════════════════════════════════════════════════
import hashlib          # noqa: E402
import logging          # noqa: E402
import uuid as _uuid    # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import dspy             # noqa: E402

logger = logging.getLogger(__name__)

try:
    from config.models import AGENTIC_TASK_CONCURRENCY
except Exception:  # pragma: no cover - config import is not available in tests
    AGENTIC_TASK_CONCURRENCY = 4


# Each agent session is a subprocess, so this MUST NOT ride the pipeline's
# 350-permit LLM semaphore (extraction_service.py). Celery prefork hands every
# task a fresh process and asyncio.run() a fresh loop, so a Semaphore built at
# import time would latch onto the wrong loop; rebuild whenever the loop changes.
_SEM: Optional[asyncio.Semaphore] = None
_SEM_LOOP: Any = None

# "paper::field" -> USD spent on it in THIS worker process. Celery prefork gives
# each task its own process, so this scopes naturally to one extraction job. It
# exists to survive StagedPipeline's retry loop, which the per-run budget cannot
# see: without it, three retries meant three fresh allowances.
_SPEND_LEDGER: Dict[str, float] = {}


def _agentic_semaphore() -> asyncio.Semaphore:
    global _SEM, _SEM_LOOP
    loop = asyncio.get_running_loop()
    if _SEM is None or _SEM_LOOP is not loop:
        _SEM = asyncio.Semaphore(AGENTIC_TASK_CONCURRENCY)
        _SEM_LOOP = loop
    return _SEM


def _record_cost(res: "ExtractResult", schema_name: str) -> None:
    """Write one llm_history row per agent session.

    Agent SDK traffic never touches litellm, so without this an agentic run
    spends real money invisibly. Shape mirrors utils/langchain_cost_callback.py.
    """
    if not res.cost_usd and not res.output_tokens:
        return
    try:
        from utils.run_context import get_current_job_id
        from utils.supabase_client import get_supabase_client

        call_uuid = str(_uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        # The SDK takes a bare model id, but llm_history is keyed on the LiteLLM
        # form everywhere else. Store the canonical id so /usage groups agentic
        # spend together with the rest instead of inventing a second model row.
        model_id = CFG.model if "/" in CFG.model else f"anthropic/{CFG.model}"
        row = {
            "call_hash": hashlib.md5(f"{call_uuid}{ts}".encode()).hexdigest(),
            "call_uuid": call_uuid,
            "call_timestamp": ts,
            "model": model_id,
            "cost": float(res.cost_usd or 0.0),
            "prompt_tokens": int(res.input_tokens or 0),
            "completion_tokens": int(res.output_tokens or 0),
            "total_tokens": int((res.input_tokens or 0) + (res.output_tokens or 0)),
            "cache_creation_input_tokens": int(res.cache_creation_tokens or 0),
            "cache_read_input_tokens": int(res.cache_read_tokens or 0),
            "cache_hit": bool(res.cache_read_tokens),
            "messages": [],
            "system_prompt": "",
            "user_prompt": "",
            "assistant_response": "",
            "source_file": res.paper,
            "schema_name": schema_name,
            "job_id": get_current_job_id(),
            "metadata": {
                "transport": "claude_agent_sdk",
                "field_name": res.field_name,
                "status": res.status,
                "num_turns": res.num_turns,
                "n_rows": res.n_rows,
                "plan_rows": res.plan_rows,
                "rows_missing": res.rows_missing,
                "rows_withdrawn": res.rows_withdrawn,
                "refill_rounds": res.refill_rounds,
                "session_id": res.session_id,
            },
        }
        client = get_supabase_client()
        if client and client.is_available():
            client.client.table("llm_history").upsert(row, on_conflict="call_hash").execute()
    except Exception:  # never let telemetry break an extraction
        logger.warning("agentic: failed to record cost to llm_history", exc_info=True)


async def extract_table_from_markdown(
    *,
    markdown_content: str,
    sig_def: Dict[str, Any],
    field_def: Dict[str, Any],
    paper_hint: str = "paper",
    cfg: Config = CFG,
) -> "ExtractResult":
    """Runtime entry point: same as extract_table() but takes markdown, not a path.

    The pipeline hands extractors a string; extract_table() wants a file because
    the agent Greps it. Materialise it in a temp dir for the session's lifetime.
    """
    stem = re.sub(r"[^A-Za-z0-9._ -]", "_", paper_hint).strip() or "paper"
    tmp = Path(tempfile.mkdtemp(prefix="agentic_src_"))
    try:
        p = tmp / f"{stem[:80]}.md"
        p.write_text(markdown_content, encoding="utf-8")
        return await extract_table(
            paper_path=p, sig_def=sig_def, field_def=field_def, cfg=cfg
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_agentic_table_extractor_class(
    parent_sig_def: Dict[str, Any],
    output_field_def: Dict[str, Any],
    task_name: str = "runtime",
) -> type:
    """Build the extractor class registered in `extractor_factories`.

    Contract required by schemas/config.py:259-265 — zero-arg constructible, and
    `async __call__(markdown_content, **kwargs) -> {field_name: envelope}`.
    `_is_keyed_pipeline` stays False so the single-call pilot-calibration branch runs
    (it no-ops harmlessly when `.extract` is absent).
    """
    field_name = output_field_def["name"]
    schema_name = parent_sig_def.get("class_name", task_name)

    class _AgenticTableExtractor(dspy.Module):
        _is_keyed_pipeline: bool = False
        _is_agentic: bool = True
        _field_name: str = field_name
        _sig_def: Dict[str, Any] = parent_sig_def
        _field_def: Dict[str, Any] = output_field_def
        _schema_name: str = schema_name

        async def __call__(self, markdown_content: str, **kwargs) -> Dict[str, Any]:
            cls = self.__class__
            fn = cls._field_name
            paper_hint = str(kwargs.get("source_file") or kwargs.get("paper") or "paper")
            key = f"{paper_hint}::{fn}"
            prior = _SPEND_LEDGER.get(key, 0.0)

            # StagedPipeline retries a field up to MAX_EXTRACTOR_RETRIES times when
            # it looks empty — which is exactly what a failed agentic run returns.
            # Retrying a deterministic failure (budget, turns) re-buys the same
            # outcome, so refuse once this field has already consumed its ceiling.
            # The failure stays VISIBLE (status="error"); only the spending stops.
            if prior >= CFG.budget_usd_extraction_cap:
                logger.error(
                    "agentic %s/%s: refusing to re-run — $%.2f already spent against "
                    "a $%.2f cap for this field. Returning failure without spending.",
                    paper_hint, fn, prior, CFG.budget_usd_extraction_cap,
                )
                return {fn: absence.failure_envelope(absence.ERROR, "budget cap reached")}

            async with _agentic_semaphore():
                res = await extract_table_from_markdown(
                    markdown_content=markdown_content,
                    sig_def=cls._sig_def,
                    field_def=cls._field_def,
                    paper_hint=paper_hint,
                )

            _SPEND_LEDGER[key] = prior + (res.cost_usd or 0.0)
            _record_cost(res, cls._schema_name)

            if res.envelope and isinstance(res.envelope.get(fn), dict):
                logger.info(
                    "agentic %s/%s: status=%s rows=%d/%d missing=%d repairs=%d "
                    "turns=%d $%.4f (field total $%.4f)",
                    paper_hint, fn, res.status, res.n_rows, res.plan_rows,
                    res.rows_missing, res.refill_rounds, res.num_turns,
                    res.cost_usd, _SPEND_LEDGER[key],
                )
                return {fn: res.envelope[fn]}

            # No usable envelope. Kept as status="error" rather than a quiet "NR":
            # an NR would be written to extraction_results as though the paper
            # genuinely reported nothing, and a reviewer could accept it. A
            # visible failure is the safer lie-free option; the ledger above is
            # what stops the retry from costing anything.
            logger.error(
                "agentic %s/%s produced no envelope after $%.4f (subtype=%s): %s %s",
                paper_hint, fn, res.cost_usd, res.subtype, res.error, res.notes,
            )
            return {fn: absence.failure_envelope(absence.ERROR, "no usable envelope")}

    _AgenticTableExtractor.__name__ = f"AsyncAgentic_{schema_name}_{field_name}_Extractor"
    _AgenticTableExtractor.__qualname__ = _AgenticTableExtractor.__name__
    _AgenticTableExtractor.__module__ = f"dspy_components.runtime.{task_name}"
    return _AgenticTableExtractor
