"""Why does the batched value call return `filled_rows: None`?

Reproduces the failure seen on job e5ecbfe5 (Aug 11 2026) in isolation: two LLM
calls, not twelve. Stage 1 for real row identities, then ONE batched value call
with its raw return dumped, so we can tell which of three things happened:

  1. the model returned nothing at all,
  2. the model answered but under different key names,
  3. the model answered correctly and DSPy failed to parse it.

Each needs a different fix, so this replaces guessing.

Usage:  python zscripts/debug_batch_none.py [paper.md]
"""

from __future__ import annotations

# Flags are read at MODULE IMPORT time, so they must be set before any
# runtime_builders import happens anywhere in the process.
import os
os.environ["EXTRACTION_BATCH_VALUES"] = "1"
os.environ["DEBUG_TWO_STAGE"] = "1"

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

import dspy  # noqa: E402
from utils.lm_config import get_dspy_model  # noqa: E402
from utils.dspy_async import async_dspy_forward  # noqa: E402
from dspy_components import runtime_builders as rb  # noqa: E402

SCHEMA = Path("eval/studies/ablation/base_schemas/ibuprofen_continuous_outcomes.json")
PAPER = Path(sys.argv[1] if len(sys.argv) > 1
             else "eval/sheets/markdown_Ibuprofen/Abdelbaser 2022a.md")

# The failing job's exact configuration:
#   Two-stage extractor for ExtractAllContinuousOutcomes —
#   anchors=['comparison','outcome_type','reporter','timepoint','scale'], value_cols=6
ANCHORS = ["comparison", "outcome_type", "reporter", "timepoint", "scale"]


def build_sig_def() -> dict:
    schema = json.loads(SCHEMA.read_text())
    for sig in schema.get("signatures", []):
        for of in sig.get("output_fields", []):
            if of.get("subform_fields"):
                of["extraction_strategy"] = "row_then_columns"
                of["anchor_columns"] = ANCHORS
                return sig, of
    raise SystemExit("no table field found in the schema")


def rule(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


async def main() -> None:
    """Run the WHOLE extractor through ModelRouter, exactly as production does.

    A bare `dspy.context(lm=...)` is not enough: CachingChatAdapter is installed
    at module load and has to survive context entry, which ModelRouter handles.
    Without it the paper never reaches the prompt — Stage 1 came back with
    "No source document text was provided in the input for this task", which is
    a script artifact, not the production bug.
    """
    get_dspy_model()
    from utils.circuit_breaker import ModelRouter

    sig_def, field_def = build_sig_def()
    markdown = PAPER.read_text()
    value_cols = [c["field_name"] for c in field_def["subform_fields"]
                  if c["field_name"] not in set(ANCHORS)]

    print(f"paper       : {PAPER.name}  ({len(markdown):,} chars)")
    print(f"field       : {field_def['name']}")
    print(f"anchors     : {ANCHORS}")
    print(f"value cols  : {value_cols}")

    ExtractorCls = rb.build_keyed_extractor_class(sig_def, field_def, "debug")
    ex = ExtractorCls()
    print(f"batch sig   : {ExtractorCls._set_slot_fill_class.__name__}")
    print(f"batch out   : {list(ExtractorCls._set_slot_fill_class.output_fields.keys())}")
    print(f"batch in    : {list(ExtractorCls._set_slot_fill_class.input_fields.keys())}")
    print(f"batch size  : {rb._records_per_call(len(value_cols), ex.set_slot_filler)} rows/call")

    # ── Full run through the router, with the batch enabled ─────────────────
    rule("FULL EXTRACTION (router-routed, batch enabled, DEBUG_TWO_STAGE on)")
    router = ModelRouter.get_instance()
    result = await router.run_with_routing(
        async_callable=ex,
        operation_name="debug:batch_none",
        markdown_content=markdown,
    )
    env = result.get(field_def["name"], {}) if isinstance(result, dict) else {}
    rows_out = env.get("value") if isinstance(env, dict) else None
    print(f"envelope status : {env.get('status') if isinstance(env, dict) else '(none)'}")
    print(f"rows returned   : {len(rows_out) if isinstance(rows_out, list) else repr(rows_out)}")
    if isinstance(env, dict) and env.get("unfilled_rows"):
        print(f"unfilled rows   : {env['unfilled_rows']}")

    rule("THE ANSWER — grep the debug log for what the batch actually returned")
    dbg_path = Path("../logs/keyed_debug.log").resolve()
    print(f"debug log: {dbg_path}")
    if dbg_path.exists():
        tail = dbg_path.read_text(errors="replace").splitlines()[-400:]
        for line in tail:
            if any(k in line for k in ("[S2-BATCH", "[PLAN-LOCKED]", "[S1-AFTER-NORM]", "[CENSUS-RAW]")):
                print(line[:1500])
    return


async def _unused(ex, field_def, markdown):
    s1 = await async_dspy_forward(ex.record_discovery, markdown_content=markdown)
    print(f"record_discovery return type : {type(s1).__name__}")
    print(f"record_discovery keys        : {list(s1.keys()) if hasattr(s1, 'keys') else '(not a mapping)'}")
    raw_rows = s1.get(field_def["name"]) if hasattr(s1, "get") else None
    print(f"record_discovery field type  : {type(raw_rows).__name__}")
    print(f"record_discovery raw (2000c) : {repr(raw_rows)[:2000]}")
    _reason = s1.get("reasoning") if hasattr(s1, "get") else None
    if _reason:
        print("--- record_discovery reasoning (why it returned what it did) ---")
        print(str(_reason)[:2500])

    rows = raw_rows or []
    if isinstance(rows, str):
        rows = json.loads(rows)
    if isinstance(rows, dict):
        # An envelope or a row-keyed object rather than a list — the same shapes
        # _TwoStageExtractor normalizes. Mirror that instead of silently
        # reporting "0 rows".
        vals = list(rows.values())
        rows = vals[0] if len(vals) == 1 and isinstance(vals[0], list) else vals
    records = [
        {a: (r.get(a, {}).get("value") if isinstance(r.get(a), dict) else r.get(a))
         for a in ANCHORS}
        for r in rows if isinstance(r, dict)
    ]
    print(f"rows found: {len(records)}")
    for r in records[:4]:
        print("  ", json.dumps(r, ensure_ascii=False))
    if not records:
        raise SystemExit("Stage 1 found no rows — pick a paper with a results table")

    # ── Stage 2: ONE batched call, raw output dumped ────────────────────────
    rule("CALL 2 — batched value extraction (the one that returned None)")
    payload = json.dumps(records, ensure_ascii=False)
    raw = await async_dspy_forward(
        ex.set_slot_filler, markdown_content=markdown, rows_to_fill=payload,
    )

    print(f"return type   : {type(raw).__name__}")
    print(f"keys returned : {list(raw.keys()) if hasattr(raw, 'keys') else '(not a mapping)'}")
    filled = raw.get("filled_rows") if hasattr(raw, "get") else None
    print(f"filled_rows   : {type(filled).__name__}")

    rule("VERDICT")
    if filled is None:
        print("REPRODUCED — filled_rows is None.")
        if hasattr(raw, "keys") and list(raw.keys()):
            print("The model DID answer, but under other keys:", list(raw.keys()))
            print("→ cause 2: key-name mismatch between prompt and signature.")
        else:
            print("Nothing came back at all → cause 1 or 3.")
    elif isinstance(filled, list):
        print(f"NOT reproduced — got {len(filled)} row(s) back.")
        print("→ the failure is data-dependent (this paper works, the other did not).")
    else:
        print(f"filled_rows is a {type(filled).__name__}, not a list → cause 3 (parse).")

    rule("RAW RETURN (first 4000 chars)")
    print(repr(raw)[:4000])

    hist = getattr(lm, "history", None) or []
    if hist:
        rule("RAW MODEL TEXT — what the model actually sent (first 3000 chars)")
        last = hist[-1]
        for key in ("outputs", "response"):
            v = last.get(key) if isinstance(last, dict) else None
            if v:
                print(f"[{key}]", repr(v)[:3000])
                break


if __name__ == "__main__":
    asyncio.run(main())
