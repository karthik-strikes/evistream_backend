"""Single-call PROMPT arm for a SCALAR (decomposed) form: litellm -> one JSON object -> one CSV row/paper.

Scalar analogue of eval/studies/table_ablation/run_prompt_arm.py. Collapses a multi-signature
form into ONE prompt / ONE litellm call per paper, using the SAME model as production
(anthropic/claude-sonnet-4-6) so the Δ vs the production decomposed CSV is attributable
to the decomposition, not the model.

Usage:
    python -m eval.studies.stage_ablation.run_prompt_arm --seed-all
    python -m eval.studies.stage_ablation.run_prompt_arm --form perio_study_characteristics --seed [--force]
    python -m eval.studies.stage_ablation.run_prompt_arm --form perio_study_characteristics --papers 2   # smoke
    python -m eval.studies.stage_ablation.run_prompt_arm --form perio_study_characteristics              # full
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/evistream")

from eval.studies.ablation.run_extraction import (
    _output_field_order, _results_to_grounded_rows, _results_to_rows,
    _write_csv, _write_grounded_csv, grounded_scalar_header,
)
from eval.studies.stage_ablation.prompt_arm_forms import FORMS
from eval.studies.stage_ablation.seed_scalar_prompts import (
    build_seed_prompt, load_schema_def, scalar_field_order,
)

MODEL = os.environ.get("PROMPT_ARM_MODEL", "anthropic/claude-sonnet-4-6")
MAX_TOKENS = int(os.environ.get("PROMPT_ARM_MAX_TOKENS", "20000"))
TEMPERATURE = float(os.environ.get("PROMPT_ARM_TEMPERATURE", "1.0"))
CALL_TIMEOUT = int(os.environ.get("PROMPT_ARM_TIMEOUT", "300"))
RETRY_MAX_TOKENS = int(os.environ.get("PROMPT_ARM_RETRY_MAX_TOKENS", "48000"))  # streamed retry on truncation
DEFAULT_CONCURRENCY = 8

EVAL_ROOT = Path("/home/ubuntu/evistream/eval")


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in (EVAL_ROOT / ".env", Path("/home/ubuntu/evistream/backend/.env")):
        if p.exists():
            load_dotenv(p, override=False)


def _load_papers(md_dir: Path, limit: int | None) -> list[dict]:
    md_dir = Path(md_dir)
    if not md_dir.exists():
        sys.exit(f"ERROR: markdown cache not found: {md_dir}")
    papers: list[dict] = []
    for md in sorted(md_dir.glob("*.md")):
        papers.append({
            "doc_id": md.stem,
            "markdown_content": md.read_text(encoding="utf-8"),
            "_filename": md.stem,
            "_author": md.stem,
        })
        if limit and len(papers) >= limit:
            break
    return papers


def _json_loaders():
    """Parsers tried in order: strict JSON → Python literal → json_repair (fixes LLM JSON:
    unescaped quotes in verbatim source_text, trailing commas, fences, etc.)."""
    loaders = [json.loads, ast.literal_eval]
    try:
        from json_repair import repair_json
        loaders.append(lambda s: repair_json(s, return_objects=True))
    except Exception:
        pass
    return loaders


def _parse_object(text: str | None):
    """Parse a single flat field->value object from a model reply. Returns dict or None."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 2:
            t = parts[1]
            if t[:4].lower() == "json":
                t = t[4:]
        t = t.strip()
    chunk = t[t.find("{"): t.rfind("}") + 1] if "{" in t and "}" in t else t
    for parser in _json_loaders():
        try:
            obj = parser(chunk)
        except Exception:
            continue
        if isinstance(obj, dict):
            inner = obj.get("fields")
            return inner if isinstance(inner, dict) else obj
    return None


async def _complete(messages: list, max_tokens: int, stream: bool):
    """One litellm call → (text, finish_reason). num_retries handles 429/5xx/timeout w/ backoff."""
    import litellm
    if not stream:
        resp = await litellm.acompletion(
            model=MODEL, max_tokens=max_tokens, temperature=TEMPERATURE,
            messages=messages, timeout=CALL_TIMEOUT, num_retries=4,
        )
        ch = resp.choices[0]
        return ch.message.content, ch.finish_reason
    parts, finish = [], None
    resp = await litellm.acompletion(
        model=MODEL, max_tokens=max_tokens, temperature=TEMPERATURE,
        messages=messages, timeout=CALL_TIMEOUT, num_retries=4, stream=True,
    )
    async for chunk in resp:
        ch = chunk.choices[0]
        if getattr(ch, "delta", None) and ch.delta.content:
            parts.append(ch.delta.content)
        if ch.finish_reason:
            finish = ch.finish_reason
    return "".join(parts), finish


async def _extract_one(system_prompt: str, paper: dict, sem: asyncio.Semaphore):
    """One paper -> ONE LLM call (system=instructions, user=paper). No decomposition.

    Robustness: ≤2 attempts. Attempt 2 retries on API error, unparseable JSON, or truncation
    (`finish_reason == "length"`) — streamed at a higher cap. A legitimately empty object (`{}`)
    is NOT retried. Always returns (doc_id, dict).
    """
    doc = paper["doc_id"]
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",
         "content": f"Extract the form from the following paper.\n\n{paper['markdown_content']}"},
    ]
    async with sem:
        for attempt in range(2):
            stream = attempt > 0
            mt = RETRY_MAX_TOKENS if stream else MAX_TOKENS
            try:
                text, finish = await _complete(messages, mt, stream)
            except Exception as exc:              # noqa: BLE001
                print(f"    ! {doc}: call failed (attempt {attempt+1}): {exc}", flush=True)
                continue
            if finish == "length":
                print(f"    ! {doc}: truncated (attempt {attempt+1}); retry streamed @ {RETRY_MAX_TOKENS}", flush=True)
                continue
            obj = _parse_object(text)
            if obj is not None:
                print(f"    · {doc}: {len(obj)} fields", flush=True)
                return doc, obj
            print(f"    ! {doc}: unparseable JSON (attempt {attempt+1}); head={text[:120]!r}", flush=True)
        print(f"    ✗ {doc}: giving up after retries -> empty", flush=True)
        return doc, {}


async def _main_async(spec, papers_limit: int | None, concurrency: int) -> int:
    schema_def = load_schema_def(spec)
    field_order = scalar_field_order(schema_def)

    if not spec.prompt_path.exists():
        sys.exit(f"ERROR: prompt missing: {spec.prompt_path}\n"
                 f"  Seed it: python -m eval.studies.stage_ablation.run_prompt_arm --form {spec.key} --seed")
    system_prompt = spec.prompt_path.read_text()

    _load_env()
    _m = MODEL.lower()
    _keys = (["OPENAI_API_KEY"] if ("openai" in _m or "gpt" in _m)
             else ["GEMINI_API_KEY", "GOOGLE_API_KEY"] if ("gemini" in _m or "google" in _m)
             else ["ANTHROPIC_API_KEY"])
    if not any(os.getenv(k) for k in _keys):
        sys.exit(f"ERROR: none of {_keys} set (expected in eval/.env) for model {MODEL}")
    import litellm
    litellm.drop_params = True
    litellm.suppress_debug_info = True

    papers = _load_papers(spec.markdown_dir, papers_limit)
    if not papers:
        sys.exit(f"ERROR: no papers in {spec.markdown_dir}")
    print(f"  model={MODEL}  form={spec.key}  fields={len(field_order)}  "
          f"papers={len(papers)}  concurrency={concurrency}", flush=True)

    sem = asyncio.Semaphore(concurrency)
    pairs = await asyncio.gather(*[_extract_one(system_prompt, p, sem) for p in papers])
    results = {doc_id: obj for doc_id, obj in pairs}
    rows = _results_to_rows(results, papers, field_order)
    spec.out_csv.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(rows, field_order, spec.out_csv)
    # grounded sibling CSV: value + per-field source_text
    grounded = _results_to_grounded_rows(results, papers, field_order)
    _write_grounded_csv(grounded, grounded_scalar_header(field_order), spec.grounded_csv)
    print(f"  done -> {spec.out_csv}", flush=True)
    return 0


def _seed(spec, force: bool) -> None:
    prompt = build_seed_prompt(spec, load_schema_def(spec))
    spec.prompt_path.parent.mkdir(parents=True, exist_ok=True)
    if spec.prompt_path.exists() and not force:
        print(f"  exists (use --force to overwrite): {spec.prompt_path}")
        return
    spec.prompt_path.write_text(prompt)
    print(f"  seeded -> {spec.prompt_path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", choices=sorted(FORMS), help="which scalar form")
    ap.add_argument("--papers", type=int, default=None, help="smoke-test on first N papers")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument("--seed", action="store_true", help="(re)generate this form's prompt, then exit")
    ap.add_argument("--seed-all", action="store_true", help="generate prompts for ALL forms, then exit")
    ap.add_argument("--force", action="store_true", help="overwrite an existing prompt when seeding")
    args = ap.parse_args()

    if args.seed_all:
        for _, spec in sorted(FORMS.items()):
            _seed(spec, args.force)
        return 0
    if not args.form:
        ap.error("--form is required (or use --seed-all)")
    spec = FORMS[args.form]
    if args.seed:
        _seed(spec, args.force)
        return 0
    # Bare run_until_complete (not asyncio.run): litellm leaves non-daemon worker threads,
    # and asyncio.run()'s executor/asyncgen shutdown blocks forever on them. __main__ hard-
    # exits via os._exit, which tears those threads down.
    loop = asyncio.new_event_loop()
    return loop.run_until_complete(_main_async(spec, args.papers, args.concurrency))


if __name__ == "__main__":
    _rc = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_rc or 0)
