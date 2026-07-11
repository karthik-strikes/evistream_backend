"""Single-call PROMPT arm for a table form: litellm -> JSON rows -> wide-long CSV.

Replaces the flag-drop `to_single_call` arm with a hand-tunable prompt file per form.
Uses the SAME model as the production row_then_columns extraction
(anthropic/claude-sonnet-4-6) so the Δ vs the production CSV is attributable to the
extraction strategy, not the model.

Usage:
    python -m eval.studies.table_ablation.run_prompt_arm --seed-all          # generate all 5 prompts
    python -m eval.studies.table_ablation.run_prompt_arm --form perio_interventions --seed [--force]
    python -m eval.studies.table_ablation.run_prompt_arm --form perio_interventions --papers 2   # smoke
    python -m eval.studies.table_ablation.run_prompt_arm --form perio_interventions              # full
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/evistream")

from eval.studies.table_ablation.forms_registry import FORMS
from eval.studies.table_ablation.seed_prompts import build_seed_prompt, load_schema_def, table_field_def
from eval.studies.table_ablation.transform import (
    explode_table_grounded, explode_table_results, grounded_table_header, subform_cols,
)
from eval.studies.ablation.run_extraction import _write_grounded_csv

MODEL = os.environ.get("PROMPT_ARM_MODEL", "anthropic/claude-sonnet-4-6")
MAX_TOKENS = int(os.environ.get("PROMPT_ARM_MAX_TOKENS", "20000"))
TEMPERATURE = float(os.environ.get("PROMPT_ARM_TEMPERATURE", "1.0"))
CALL_TIMEOUT = int(os.environ.get("PROMPT_ARM_TIMEOUT", "300"))   # seconds per LLM call
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


def _write_csv(rows: list[dict], cols: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ["Paper", "paper"] + cols
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore", quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    n_papers = len({r["Paper"] for r in rows}) if rows else 0
    print(f"    wrote {len(rows)} rows ({n_papers} papers) -> {path}")


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


def _parse_rows(text: str | None):
    """Pull the row list out of a model reply. Returns list, or None if unparseable."""
    if not text:
        return []
    t = text.strip()
    if t.startswith("```"):                       # strip a leading ```json fence
        parts = t.split("```")
        if len(parts) >= 2:
            t = parts[1]
            if t[:4].lower() == "json":
                t = t[4:]
        t = t.strip()
    candidates = [t]
    if "{" in t and "}" in t:
        candidates.append(t[t.find("{"): t.rfind("}") + 1])
    if "[" in t and "]" in t:
        candidates.append(t[t.find("["): t.rfind("]") + 1])
    for chunk in candidates:
        for parser in _json_loaders():
            try:
                obj = parser(chunk)
            except Exception:
                continue
            if isinstance(obj, dict):
                return obj.get("rows") or obj.get("data") or []
            if isinstance(obj, list):
                return obj
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
    """One paper -> ONE LLM call (system=instructions, user=paper). No staging.

    Robustness: ≤2 attempts. Attempt 2 retries on API error, unparseable JSON, or truncation
    (`finish_reason == "length"`) — streamed at a higher cap. A legitimately empty result (`[]`)
    is NOT retried. Always returns (doc_id, list).
    """
    doc = paper["doc_id"]
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",
         "content": f"Extract the table from the following paper.\n\n{paper['markdown_content']}"},
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
            rows = _parse_rows(text)
            if rows is not None:
                print(f"    · {doc}: {len(rows)} rows", flush=True)
                return doc, rows
            print(f"    ! {doc}: unparseable JSON (attempt {attempt+1}); head={text[:120]!r}", flush=True)
        print(f"    ✗ {doc}: giving up after retries -> empty", flush=True)
        return doc, []


async def _main_async(spec, papers_limit: int | None, concurrency: int) -> int:
    schema_def = load_schema_def(spec)
    field = table_field_def(schema_def)["name"]
    cols = subform_cols(schema_def, field)

    if not spec.prompt_path.exists():
        sys.exit(f"ERROR: prompt missing: {spec.prompt_path}\n"
                 f"  Seed it: python -m eval.studies.table_ablation.run_prompt_arm --form {spec.key} --seed")
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
    print(f"  model={MODEL}  form={spec.key}  table_field={field}  cols={len(cols)}  "
          f"papers={len(papers)}  concurrency={concurrency}", flush=True)

    sem = asyncio.Semaphore(concurrency)
    pairs = await asyncio.gather(*[_extract_one(system_prompt, p, sem) for p in papers])
    results = {doc_id: {field: rows} for doc_id, rows in pairs}
    rows = explode_table_results(results, papers, field, cols)
    _write_csv(rows, cols, spec.out_csv)
    # grounded sibling CSV: value + per-cell source_text
    grounded = explode_table_grounded(results, papers, field, cols)
    _write_grounded_csv(grounded, grounded_table_header(cols), spec.grounded_csv)
    print(f"  done -> {spec.out_csv}")
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
    ap.add_argument("--form", choices=sorted(FORMS), help="which table form")
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
