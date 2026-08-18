"""What an LLM call was FOR — which paper, which pipeline step, first try or retry.

Why this module exists
---------------------
`llm_history` has always recorded what a call COST (tokens, cache, dollars) and
never what it was FOR. Answering "why did this 4-paper run cost 9 calls?" for
job `05f205c7` meant reading `logs/extraction_worker.log` and identifying the
four papers by their *prompt-cache byte counts*. Every fact needed to answer it
is present in the flush, so we derive it there and store it once.

Three rules shape this code:

* **Derive, never guess.** A paper is attributed only when exactly one of the
  run's papers has its text in the prompt. Zero or two matches leaves the row
  unlabeled — a wrong filename on a cost row is worse than a blank one.
* **The step comes from the signature's field set, not from ordering.** DSPy
  renders every input field name into the prompt as `[[ ## name ## ]]`, so
  `candidate_row_plan` / `rows_to_fill` / `row_anchor` identify which
  keyed-pipeline operation ran. This survives the JSONAdapter fallback, which
  reformats the *output* but keeps the input labels — verified on job
  `05f205c7`, where both attempts of one recall audit carry the marker.
* **The last attempt is the keeper.** When one semantic input produced several
  calls, the earlier ones were discarded (unparseable output, or a pipeline
  retry). They are marked `superseded` and their cost is the run's waste. This
  is why the first Santos 2020 recall audit ($0.099) is the wasted call and the
  JSON-shaped retry ($0.071) is the one that counted — not the other way round.

Consumed by `utils/logging.py` at flush time (writes `llm_history.metadata`) and
by `app/api/v1/usage.py` at read time (legacy rows, which have no metadata).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ── Steps ──────────────────────────────────────────────────────────────────
# Named for the operation, per the Aug 2026 terminology in CLAUDE.md — never
# "Stage 1 / Stage 2", which described a mechanism that no longer exists.
STEP_EXTRACT = "extract"                    # plain single-call signature
STEP_RECORD_DISCOVERY = "record_discovery"  # keyed pipeline, key columns only
STEP_RECALL_AUDIT = "recall_audit"          # "what did discovery miss?"
STEP_SLOT_FILL = "slot_fill"                # set-at-a-time attribute fill
STEP_SLOT_FILL_ROW = "slot_fill_row"        # one call per record (fallback path)
STEP_REFILL = "refill"                      # targeted re-extraction of empties

# The keyed pipeline's frozen input field names (see CLAUDE.md — these are in
# the prompt AND are response parse keys, so they do not drift).
FIELD_CANDIDATE_PLAN = "candidate_row_plan"
FIELD_ROWS_TO_FILL = "rows_to_fill"
FIELD_ROW_ANCHOR = "row_anchor"
FIELD_MARKDOWN = "markdown_content"

_MARK = "[[ ## {} ## ]]"
_MARK_ANY_RE = re.compile(r"\[\[\s*##\s*([A-Za-z_][A-Za-z0-9_]*)\s*##\s*\]\]")

# DSPy ChatAdapter system prompt has a block like:
#   Your output fields are:
#   1. `reasoning` (str): ...
#   2. `actual_field` (List[...]): ...
# ChainOfThought always prepends `reasoning`; we want the first non-meta field.
_OUTPUT_BLOCK_RE = re.compile(
    r"Your output fields are:\s*\n(.*?)(?:\n\s*\n|\nAll interactions|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_NUMBERED_FIELD_RE = re.compile(r"^\s*\d+\.\s*`([A-Za-z_][A-Za-z0-9_]*)`", re.MULTILINE)
_META_FIELDS = {"reasoning", "completed", "done", "output", "answer", "rationale"}

# Every user message ends with an instruction that NAMES the output fields —
# "Respond with the corresponding output fields, starting with the field
# `[[ ## reasoning ## ]]`, then …" (ChatAdapter) or "Respond with a JSON object…"
# (JSONAdapter). Those markers are not inputs, and reading them as inputs made
# four different papers' record-discovery calls produce byte-identical "inputs",
# i.e. four attempts at the same work. Cut the trailer before parsing anything.
# `\A` as well as `\n`: when CachingChatAdapter hoists the paper into the system
# message, the trailer is the *entire* user message, with no newline in front of
# it — which is the common case for record discovery.
_TRAILER_RE = re.compile(
    r"(?:\A|\n)\s*Respond with (?:the corresponding output fields|a JSON object).*\Z",
    re.DOTALL | re.IGNORECASE,
)

# Response shapes
SHAPE_FIELDS = "fields"   # ChatAdapter: [[ ## name ## ]] blocks
SHAPE_JSON = "json"       # JSONAdapter fallback: a bare JSON object
SHAPE_EMPTY = "empty"

# A paper is identified by a slice of its own text. Taken from the middle rather
# than the head so two papers from the same journal (identical banner, DOI line,
# "Received: ..." block) cannot both match.
_PROBE_START = 600
_PROBE_LEN = 300


# ── Message plumbing ──────────────────────────────────────────────────────

def _content_text(content: Any) -> str:
    """Flatten one message's content, which may be a str or a list of blocks.

    CachingChatAdapter splits the paper into separate content blocks to place
    Anthropic cache breakpoints, so list-shaped content is the norm here, not an
    edge case.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text") or "")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def messages_text(messages: Any) -> str:
    """All text of a call's prompt, system and user alike."""
    if not isinstance(messages, list):
        return ""
    return "\n".join(
        _content_text(m.get("content")) for m in messages if isinstance(m, dict)
    )


def user_fields(messages: Any) -> Dict[str, str]:
    """The input field VALUES from the user message(s), keyed by field name.

    Only user messages are read: the system message contains the same markers as
    an unfilled template (`[[ ## markdown_content ## ]]\\n{markdown_content}`),
    and treating those placeholders as values would make every call look alike.
    """
    if not isinstance(messages, list):
        return {}
    text = "\n".join(
        _content_text(m.get("content"))
        for m in messages
        if isinstance(m, dict) and m.get("role") == "user"
    )
    if not text:
        return {}
    # Before anything else: the closing instruction names output fields, and a
    # marker read from there is not an input value.
    text = _TRAILER_RE.sub("", text)
    if not text.strip():
        return {}
    out: Dict[str, str] = {}
    marks = list(_MARK_ANY_RE.finditer(text))
    for i, m in enumerate(marks):
        name = m.group(1)
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        value = text[m.end():end]
        value = _TRAILER_RE.sub("", value).strip()
        # First occurrence wins: a field is emitted once per message.
        out.setdefault(name, value)
    return out


def _leading_json(value: Optional[str]) -> Any:
    """Parse the JSON payload at the start of a field value, tolerantly.

    Field values arrive with whatever the adapter appended after them, so a
    strict parse fails on text that is perfectly good JSON up to its last
    bracket.
    """
    if not value:
        return None
    text = value.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    for closer in ("]", "}"):
        idx = text.rfind(closer)
        if idx > 0:
            try:
                return json.loads(text[: idx + 1])
            except (json.JSONDecodeError, TypeError):
                continue
    return None


def _payload_records(value: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    parsed = _leading_json(value)
    if isinstance(parsed, list):
        return [r for r in parsed if isinstance(r, dict)]
    return None


def _record_keys(records: Optional[Sequence[Dict[str, Any]]]) -> frozenset:
    """Identity of each record in a payload, order- and formatting-insensitive."""
    if not records:
        return frozenset()
    keys = set()
    for r in records:
        try:
            keys.add(json.dumps(r, sort_keys=True, default=str))
        except Exception:
            keys.add(str(r))
    return frozenset(keys)


# ── Per-call derivations ──────────────────────────────────────────────────

def classify_step(messages: Any) -> str:
    """Which pipeline operation this call performed.

    Read from the whole prompt (system template included) because the template
    enumerates the signature's fields — that is precisely the fingerprint we
    want, and it is identical across the ChatAdapter and JSONAdapter renderings
    of the same signature.
    """
    text = messages_text(messages)
    if _MARK.format(FIELD_CANDIDATE_PLAN) in text:
        return STEP_RECALL_AUDIT
    if _MARK.format(FIELD_ROWS_TO_FILL) in text:
        return STEP_SLOT_FILL
    if _MARK.format(FIELD_ROW_ANCHOR) in text:
        return STEP_SLOT_FILL_ROW
    return STEP_EXTRACT


def signature_field(messages: Any, source_file: Optional[str] = None) -> Optional[str]:
    """Best-effort human-readable output field this call targeted.

    Relocated from `app/api/v1/usage.py` so the writer and the reader share one
    implementation instead of drifting apart.
    """
    if not isinstance(messages, list):
        system_text = user_text = ""
    else:
        system_text = user_text = ""
        for m in messages:
            if not isinstance(m, dict):
                continue
            content = _content_text(m.get("content"))
            if m.get("role") == "system" and not system_text:
                system_text = content
            elif m.get("role") == "user" and not user_text:
                user_text = content

    for txt in (system_text, user_text):
        if not txt:
            continue
        block = _OUTPUT_BLOCK_RE.search(txt)
        if block:
            for name in _NUMBERED_FIELD_RE.findall(block.group(1)):
                if name.lower() not in _META_FIELDS:
                    return name
        for name in _MARK_ANY_RE.findall(txt):
            if name.lower() not in _META_FIELDS:
                return name

    # Codegen fallback: last segment of source_file (codegen:signatures:enrich → enrich)
    if source_file and ":" in source_file:
        return source_file.rsplit(":", 1)[-1]
    return None


def response_shape(assistant_response: Optional[str]) -> str:
    """Which adapter produced this response.

    `json` means DSPy had already fallen back to JSONAdapter for this call — the
    tell-tale of a preceding parse failure, and of a prompt-cache miss, since
    the fallback re-renders the prompt without our cache breakpoints.
    """
    if not assistant_response or not assistant_response.strip():
        return SHAPE_EMPTY
    return SHAPE_FIELDS if "[[ ##" in assistant_response else SHAPE_JSON


def _missing_reasoning(assistant_response: Optional[str]) -> bool:
    """A ChatAdapter response that omitted `reasoning` — the parse fails on it.

    Every extraction signature is a ChainOfThought, so `reasoning` is mandatory.
    This is exactly what happened to the first Santos 2020 recall audit: 2 KB of
    prose, then `[[ ## missing_rows ## ]]`, and no reasoning header.
    """
    return bool(assistant_response) and "[[ ## reasoning ## ]]" not in assistant_response


def duration_ms(call_data: Dict[str, Any]) -> Optional[int]:
    """Wall-clock duration of the call, in milliseconds.

    litellm stamps this on every response it returns
    (`litellm_core_utils/llm_response_utils/response_metadata.py`), and DSPy
    keeps that response object in its history — so timing costs nothing extra to
    capture, and does not need a wrapper around the call site.
    """
    resp = call_data.get("response")
    if resp is None:
        return None
    ms = getattr(resp, "_response_ms", None)
    if ms is None:
        hidden = getattr(resp, "_hidden_params", None)
        if isinstance(hidden, dict):
            ms = hidden.get("_response_ms")
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return None
    try:
        return max(0, int(round(float(ms))))
    except (TypeError, ValueError):
        return None


def response_text(call_data: Dict[str, Any]) -> str:
    """The assistant text of a call, or '' when unavailable.

    Two shapes, because this runs in two places: a live DSPy history entry (a
    response object) at flush time, and a stored `llm_history` row (a plain
    string) when labelling legacy rows at read time.
    """
    resp = call_data.get("response")
    try:
        choices = getattr(resp, "choices", None)
        if choices:
            return choices[0].message.content or ""
    except Exception:
        pass
    stored = call_data.get("assistant_response")
    return stored if isinstance(stored, str) else ""


# ── Paper attribution ─────────────────────────────────────────────────────

def _build_probes(papers: Optional[Sequence[Dict[str, Any]]]) -> List[Tuple[str, str, str]]:
    """(doc_id, filename, probe) for each paper of the run.

    `papers` is `run_batch`'s own argument — `{"doc_id", "markdown_content",
    "path"}` per `app/services/extraction_service.py`.
    """
    probes: List[Tuple[str, str, str]] = []
    for p in (papers or []):
        if not isinstance(p, dict):
            continue
        md = p.get("markdown_content") or ""
        doc_id = p.get("doc_id")
        if not md or not doc_id:
            continue
        probe = md[_PROBE_START:_PROBE_START + _PROBE_LEN] or md[:200]
        probe = probe.replace("\x00", "").strip()
        if len(probe) < 40:
            # Too short to identify anything; better unlabeled than wrong.
            continue
        # `path` is the markdown file, whose basename is a content hash — kept as
        # provenance only. `document_id` is the label that matters; the usage API
        # resolves it to the real document name at read time.
        path = p.get("path") or ""
        filename = path.rsplit("/", 1)[-1] if path else ""
        probes.append((str(doc_id), filename, probe))
    return probes


def _match_paper(text: str, probes: Sequence[Tuple[str, str, str]]) -> Optional[Tuple[str, str]]:
    """The one paper whose text is in this prompt, or None if not exactly one."""
    hits = [(doc_id, filename) for doc_id, filename, probe in probes if probe in text]
    return hits[0] if len(hits) == 1 else None


# ── The run-level pass ────────────────────────────────────────────────────

def label_run_calls(
    entries: Sequence[Dict[str, Any]],
    papers: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """One metadata dict per DSPy history entry, in the same order.

    A run-level pass, not a per-row one, because three of the labels are only
    knowable by comparing calls to each other: whether an `extract` was really
    the keyed pipeline's record discovery, whether a fill was a refill, and
    which attempts were superseded.

    Never raises: a telemetry failure must not fail an extraction, so the caller
    gets `{}` for any entry this cannot label.
    """
    n = len(entries)
    if not n:
        return []

    probes = _build_probes(papers)
    labels: List[Dict[str, Any]] = []
    parsed: List[Dict[str, Any]] = []

    for call in entries:
        try:
            messages = call.get("messages") or []
            text = messages_text(messages)
            fields = user_fields(messages)
            resp = response_text(call)
            step = classify_step(messages)

            lab: Dict[str, Any] = {"transport": "dspy", "step": step}
            field = signature_field(messages)
            if field:
                # The signature's own output field. For keyed steps this is the
                # synthesized name (`missing_rows`, `filled_rows`), not the table
                # field the work was about — `_resolve_table_field` fixes that up
                # once the whole run is visible.
                lab["field_name"] = field
                lab["output_field"] = field
            ms = duration_ms(call)
            if ms is not None:
                lab["duration_ms"] = ms
            shape = response_shape(resp)
            lab["response_shape"] = shape

            match = _match_paper(text, probes)
            if match:
                lab["document_id"], lab["filename"] = match[0], match[1]

            payload = fields.get(FIELD_ROWS_TO_FILL) or fields.get(FIELD_CANDIDATE_PLAN)
            records = _payload_records(payload)
            if records is not None:
                lab["n_records"] = len(records)

            # Semantic identity: the inputs that actually vary between calls.
            # The raw messages cannot be used — the JSONAdapter retry of a call
            # re-renders the whole prompt, so identical work would look
            # different. markdown_content is excluded because the matched paper
            # already carries it, and it is not always in the user message.
            semantic = {k: v for k, v in fields.items() if k != FIELD_MARKDOWN}
            digest_src = json.dumps(semantic, sort_keys=True, default=str) if semantic else text
            lab_digest = hashlib.md5(digest_src.encode("utf-8", "replace")).hexdigest()

            parsed.append({
                "records": records,
                "keys": _record_keys(records),
                "digest": lab_digest,
                "resp": resp,
                "ts": call.get("timestamp") or "",
                "text": text,
            })
            labels.append(lab)
        except Exception:
            logger.warning("llm_call_labels: could not label a call", exc_info=True)
            labels.append({})
            parsed.append({"records": None, "keys": frozenset(), "digest": "",
                           "resp": "", "ts": "", "text": ""})

    _resolve_table_field(labels, parsed)
    _relabel_discovery(labels)
    _mark_refills(labels, parsed)
    _mark_attempts(labels, parsed)
    return labels


def _paper_field_key(lab: Dict[str, Any]) -> Tuple[str, str]:
    return (
        lab.get("document_id") or "?",
        lab.get("table_field") or lab.get("field_name") or "?",
    )


_KEYED_STEPS = (STEP_RECALL_AUDIT, STEP_SLOT_FILL, STEP_SLOT_FILL_ROW, STEP_REFILL)


def _resolve_table_field(labels: List[Dict[str, Any]], parsed: List[Dict[str, Any]]) -> None:
    """Tie each keyed-pipeline call back to the table field it was working on.

    The keyed steps report synthesized output fields — `missing_rows`,
    `filled_rows` — so grouping on the output field puts the three steps of ONE
    table field into three different buckets. That is what made the usage dialog
    call them "3 fields".

    The table field's real name comes from the first-pass call for the same
    paper. When a form has several table fields, the ambiguity is broken by
    looking for each candidate name in the prompt, which the synthesized
    docstrings include ("Audit a candidate record set for <field> …",
    "Fill the value columns of <field> …"). Matching on the name rather than on
    that sentence keeps this from breaking when the wording changes.
    """
    by_paper: Dict[str, List[str]] = {}
    for lab in labels:
        if lab.get("step") == STEP_EXTRACT and lab.get("field_name"):
            by_paper.setdefault(lab.get("document_id") or "?", []).append(lab["field_name"])

    for i, lab in enumerate(labels):
        if lab.get("step") not in _KEYED_STEPS:
            continue
        candidates = list(dict.fromkeys(by_paper.get(lab.get("document_id") or "?", [])))
        pick = None
        if len(candidates) == 1:
            pick = candidates[0]
        elif candidates:
            hits = [c for c in candidates if c in parsed[i]["text"]]
            pick = hits[0] if len(hits) == 1 else None
        if pick:
            lab["table_field"] = pick
            # Report the table field as *the* field: the step name already says
            # what the call did, so "dichotomous_outcomes / recall audit" reads
            # better than "missing_rows / recall audit".
            lab["field_name"] = pick


def _relabel_discovery(labels: List[Dict[str, Any]]) -> None:
    """`extract` → `record_discovery` where the keyed pipeline clearly ran.

    A single-call table field and a keyed pipeline's first call look identical in
    isolation: both send only the paper. What separates them is that the keyed
    pipeline goes on to audit or fill the same field for the same paper.
    """
    keyed = {
        _paper_field_key(lab)
        for lab in labels
        if lab.get("step") in (STEP_RECALL_AUDIT, STEP_SLOT_FILL, STEP_SLOT_FILL_ROW)
    }
    for lab in labels:
        if lab.get("step") == STEP_EXTRACT and _paper_field_key(lab) in keyed:
            lab["step"] = STEP_RECORD_DISCOVERY


def _mark_refills(labels: List[Dict[str, Any]], parsed: List[Dict[str, Any]]) -> None:
    """`slot_fill` → `refill` for repair passes, not for a table's later batches.

    Ordering alone cannot tell the two apart: a 60-row table sends batch 2 after
    batch 1, and a repair also comes second. The difference is that a repair
    re-sends records already asked for, while batch 2 sends new ones.
    """
    by_group: Dict[Tuple[str, str], List[int]] = {}
    for i, lab in enumerate(labels):
        if lab.get("step") == STEP_SLOT_FILL:
            by_group.setdefault(_paper_field_key(lab), []).append(i)

    for idxs in by_group.values():
        if len(idxs) < 2:
            continue
        idxs.sort(key=lambda i: parsed[i]["ts"])
        seen: set = set()
        for pos, i in enumerate(idxs):
            keys = parsed[i]["keys"]
            if pos and keys and keys <= seen:
                labels[i]["step"] = STEP_REFILL
            seen |= set(keys)


def _mark_attempts(labels: List[Dict[str, Any]], parsed: List[Dict[str, Any]]) -> None:
    """Number repeated attempts at the same work; mark the discarded ones.

    Same paper + same field + same step + byte-identical semantic inputs means
    the work was asked for twice. The later answer is the one the pipeline used,
    so every earlier attempt is `superseded` — its money bought nothing.
    """
    groups: Dict[Tuple[Any, ...], List[int]] = {}
    for i, lab in enumerate(labels):
        if not lab:
            continue
        key = (
            lab.get("document_id") or "?",
            lab.get("field_name") or "?",
            lab.get("step") or "?",
            parsed[i]["digest"],
        )
        groups.setdefault(key, []).append(i)

    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        idxs.sort(key=lambda i: parsed[i]["ts"])
        last = idxs[-1]
        for pos, i in enumerate(idxs):
            labels[i]["attempt"] = pos + 1
            labels[i]["attempts_total"] = len(idxs)
            if i == last:
                continue
            labels[i]["superseded"] = True
            labels[i]["superseded_reason"] = _supersede_reason(
                parsed[i]["resp"], labels[i].get("response_shape")
            )


def _supersede_reason(resp: str, shape: Optional[str]) -> str:
    """Plain English for why an attempt was thrown away."""
    if shape == SHAPE_EMPTY:
        return "no response recorded"
    if shape == SHAPE_FIELDS and _missing_reasoning(resp):
        return "response left out a required field, so it could not be read"
    return "output was rejected and the call was repeated"


__all__ = [
    "STEP_EXTRACT",
    "STEP_RECORD_DISCOVERY",
    "STEP_RECALL_AUDIT",
    "STEP_SLOT_FILL",
    "STEP_SLOT_FILL_ROW",
    "STEP_REFILL",
    "SHAPE_FIELDS",
    "SHAPE_JSON",
    "SHAPE_EMPTY",
    "classify_step",
    "signature_field",
    "response_shape",
    "response_text",
    "duration_ms",
    "messages_text",
    "user_fields",
    "label_run_calls",
]
