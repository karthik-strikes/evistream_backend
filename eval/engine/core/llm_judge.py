"""
LLM-as-judge for free-text field comparison.
Default model: Claude Haiku 4.5. Escalate to Sonnet 4.6 via env var.
All judgments are cached in memory (loaded once at import, flushed to disk when new entries added).
Batches all fields for one study into a single API call.
"""

import os
import json
import hashlib
import anthropic
from dotenv import load_dotenv
from config.nr_synonyms import is_nr
from core.comparator import ComparisonResult, MATCH_YES, MATCH_NO, MATCH_NA, MATCH_SKIP

load_dotenv()

DEFAULT_MODEL = os.getenv("LLM_JUDGE_MODEL", "claude-haiku-4-5-20251001")
# Bump when SYSTEM_PROMPT changes so stale judgments don't survive in the cache.
PROMPT_VERSION = "v4-core-principle"
CACHE_DIR     = os.path.join(os.path.dirname(__file__), "..", "..", "outputs")
_CACHE_PATH   = os.path.join(CACHE_DIR, "llm_judgments.json")

# ── In-memory cache — loaded once at import, not re-read per call ─────────────
os.makedirs(CACHE_DIR, exist_ok=True)
if os.path.exists(_CACHE_PATH):
    with open(_CACHE_PATH) as _f:
        _CACHE: dict = json.load(_f)
else:
    _CACHE: dict = {}
# ──────────────────────────────────────────────────────────────────────────────

# The judge instructions live in engine/llm_judge_prompt.txt (sibling of config/core/forms),
# read at import. Bump PROMPT_VERSION above whenever that file changes so stale cached
# judgments don't survive.
_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "llm_judge_prompt.txt")
with open(_PROMPT_PATH, encoding="utf-8") as _pf:
    SYSTEM_PROMPT = _pf.read().strip()


def _cache_key(field_name: str, ai_val: str, gt_val: str, model: str) -> str:
    raw = f"{field_name}||{ai_val}||{gt_val}||{model}||{PROMPT_VERSION}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _flush_cache():
    with open(_CACHE_PATH, "w") as f:
        json.dump(_CACHE, f, indent=2)


def judge_batch(pairs: list, model: str = DEFAULT_MODEL) -> dict:
    """
    Judge multiple field pairs in a single LLM call.
    pairs: [{"field_name": str, "ai_val": str, "gt_val": str}, ...]
    Returns: {field_name: ComparisonResult}
    NR cases must be filtered out before calling this function.
    """
    if not pairs:
        return {}

    results = {}
    uncached = []

    for p in pairs:
        field  = p["field_name"]
        ai_str = str(p["ai_val"]).strip()
        gt_str = str(p["gt_val"]).strip()
        key    = _cache_key(field, ai_str, gt_str, model)
        if key in _CACHE:
            equivalent = _CACHE[key]["equivalent"]
            results[field] = ComparisonResult(
                ai_raw=p["ai_val"], gt_raw=p["gt_val"],
                ai_clean=ai_str, gt_clean=gt_str,
                match=MATCH_YES if equivalent else MATCH_NO,
                note="(cached)",
            )
        else:
            uncached.append(p)

    if not uncached:
        return results

    lines = []
    for i, p in enumerate(uncached):
        lines.append(
            f"{i}. Field: {p['field_name']}\n"
            f"   AI: {str(p['ai_val']).strip()}\n"
            f"   GT: {str(p['gt_val']).strip()}"
        )
    user_msg = "Evaluate these field pairs:\n\n" + "\n\n".join(lines)

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    try:
        # No assistant-message prefill: Sonnet 4.6 rejects it ("model does not
        # support assistant message prefill"). Instead we instruct pure-JSON
        # output and parse the first {...} object from the response, which works
        # across models (and tolerates a stray preamble before the JSON).
        response = client.messages.create(
            model=model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_msg},
            ],
        )
        text = response.content[0].text
        start = text.find("{")
        if start == -1:
            raise ValueError(f"no JSON object in judge response: {text[:200]!r}")
        # raw_decode stops at the first complete JSON object, ignoring any trailing text
        parsed, _ = json.JSONDecoder().raw_decode(text[start:])
    except Exception as e:
        for p in uncached:
            results[p["field_name"]] = ComparisonResult(
                ai_raw=p["ai_val"], gt_raw=p["gt_val"],
                ai_clean=str(p["ai_val"]).strip(), gt_clean=str(p["gt_val"]).strip(),
                match=MATCH_SKIP, note=f"LLM error: {e}",
            )
        return results

    for i, p in enumerate(uncached):
        field  = p["field_name"]
        ai_str = str(p["ai_val"]).strip()
        gt_str = str(p["gt_val"]).strip()
        # Use numeric index key — field name is non-unique when batching multiple
        # studies for the same field (pre-batch pattern), so field-name keys cause
        # the LLM to return one boolean for all pairs, poisoning the cache.
        # A MISSING key means the model omitted this pair from its response. Do NOT
        # default to False: that silently scores a real pair as a mismatch and
        # caches the bad verdict. Mark it SKIP and leave it UNcached so the next
        # run re-queries it instead of inheriting a fabricated result.
        if str(i) not in parsed:
            results[field] = ComparisonResult(
                ai_raw=p["ai_val"], gt_raw=p["gt_val"],
                ai_clean=ai_str, gt_clean=gt_str,
                match=MATCH_SKIP, note=f"judge omitted pair {i} from response",
            )
            continue
        # Coerce the verdict defensively. bool("false") is True in Python, so a
        # stringized verdict (model ignoring the "booleans not strings" rule)
        # would silently flip a mismatch into a match. Accept only a real JSON
        # bool or an explicit "true"/"false" string; anything else → SKIP, uncached.
        raw_verdict = parsed[str(i)]
        if isinstance(raw_verdict, bool):
            equivalent = raw_verdict
        elif isinstance(raw_verdict, str) and raw_verdict.strip().lower() in ("true", "false"):
            equivalent = raw_verdict.strip().lower() == "true"
        else:
            results[field] = ComparisonResult(
                ai_raw=p["ai_val"], gt_raw=p["gt_val"],
                ai_clean=ai_str, gt_clean=gt_str,
                match=MATCH_SKIP, note=f"judge returned non-boolean verdict for pair {i}: {raw_verdict!r}",
            )
            continue
        key = _cache_key(field, ai_str, gt_str, model)
        _CACHE[key] = {"equivalent": equivalent, "model": model, "field": field}
        results[field] = ComparisonResult(
            ai_raw=p["ai_val"], gt_raw=p["gt_val"],
            ai_clean=ai_str, gt_clean=gt_str,
            match=MATCH_YES if equivalent else MATCH_NO,
        )

    _flush_cache()
    return results


def judge(ai_val, gt_val, field_name: str = "", model: str = DEFAULT_MODEL) -> ComparisonResult:
    """Single-pair judge — used by run_judge_calibration.py."""
    ai_nr = is_nr(ai_val)
    gt_nr = is_nr(gt_val)
    if ai_nr and gt_nr:
        return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                                ai_clean="NR", gt_clean="NR",
                                match=MATCH_NA, note="both NR")
    if ai_nr or gt_nr:
        side = "AI" if ai_nr else "GT"
        return ComparisonResult(ai_raw=ai_val, gt_raw=gt_val,
                                ai_clean=str(ai_val), gt_clean=str(gt_val),
                                match=MATCH_NO, note=f"{side} is NR")
    results = judge_batch([{"field_name": field_name, "ai_val": ai_val, "gt_val": gt_val}], model)
    return results.get(field_name, ComparisonResult(
        ai_raw=ai_val, gt_raw=gt_val,
        ai_clean=str(ai_val), gt_clean=str(gt_val),
        match=MATCH_SKIP, note="batch returned no result",
    ))
