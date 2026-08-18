"""A/B: does the ROW IDENTITY block make Stage 1 return zero rows?

Shipped unconditionally on Aug 11 2026 to every live row_then_columns form:

    ROW IDENTITY: a row is one unique combination of <anchors>. Two rows must
    never share all of those values ...
    Create a row ONLY for combinations the document actually reports. Do NOT
    generate combinations by pairing identity values that appear separately ...

Stage 1 then returned 0 rows on Kohli 2011, a paper with 28 rows on record. This
runs the SAME Stage 1 signature twice on the SAME paper — once with the block,
once with it stripped — so the block is the only difference.

Usage: python zscripts/debug_stage1_ab.py [paper.md]
"""

from __future__ import annotations

import os
os.environ.setdefault("EXTRACTION_BATCH_VALUES", "0")

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

from utils.lm_config import get_dspy_model  # noqa: E402
from dspy_components import runtime_builders as rb  # noqa: E402

SCHEMA = Path("eval/studies/ablation/base_schemas/ibuprofen_continuous_outcomes.json")
PAPER = Path(sys.argv[1] if len(sys.argv) > 1
             else "eval/sheets/markdown_Ibuprofen/Kohli 2011.md")
ANCHORS = ["comparison", "outcome_type", "reporter", "timepoint", "scale"]

# Matches the block as composed in _build_record_discovery_sig_def.
BLOCK = re.compile(r"ROW IDENTITY: a row is one unique combination.*?two rows, not four\.\n\n",
                   re.S)


def load() -> tuple[dict, dict]:
    schema = json.loads(SCHEMA.read_text())
    for sig in schema["signatures"]:
        for of in sig.get("output_fields", []):
            if of.get("subform_fields"):
                of["extraction_strategy"] = "row_then_columns"
                of["anchor_columns"] = ANCHORS
                return sig, of
    raise SystemExit("no table field in schema")


async def run_one(label: str, sig_def: dict, markdown: str) -> int:
    import dspy
    from utils.circuit_breaker import ModelRouter
    from utils.dspy_async import async_dspy_forward

    cls = rb.build_signature_class(sig_def, f"ab_{label}")
    cot = dspy.ChainOfThought(cls)

    async def _call(**kw):
        return await async_dspy_forward(cot, **kw)

    out = await ModelRouter.get_instance().run_with_routing(
        async_callable=_call, operation_name=f"ab:{label}", markdown_content=markdown,
    )
    rows = out.get("outcomes") if hasattr(out, "get") else None
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except Exception:
            rows = []
    n = len(rows) if isinstance(rows, list) else 0
    print(f"\n[{label}] rows = {n}")
    if n:
        for r in (rows or [])[:3]:
            flat = {a: (r.get(a, {}).get("value") if isinstance(r.get(a), dict) else r.get(a))
                    for a in ANCHORS}
            print("   ", json.dumps(flat, ensure_ascii=False))
    reason = out.get("reasoning") if hasattr(out, "get") else None
    if reason:
        print(f"    reasoning: {str(reason)[:600]}")
    return n


async def main() -> None:
    get_dspy_model()
    sig, field = load()
    markdown = PAPER.read_text()
    print(f"paper   : {PAPER.name} ({len(markdown):,} chars)")
    print(f"anchors : {ANCHORS}")

    with_block = rb._build_record_discovery_sig_def(sig, field)
    desc = with_block["output_fields"][0]["description"]
    assert "ROW IDENTITY" in desc, "block not present — nothing to A/B"

    without = json.loads(json.dumps(with_block))
    without["class_name"] = with_block["class_name"] + "NoBlock"
    stripped, n_sub = BLOCK.subn("", desc)
    assert n_sub == 1, f"block regex matched {n_sub} times, expected 1"
    without["output_fields"][0]["description"] = stripped

    print(f"\nblock is {len(desc) - len(stripped)} chars of the {len(desc)}-char field description")

    a = await run_one("WITH the ROW IDENTITY block", with_block, markdown)
    b = await run_one("WITHOUT the block", without, markdown)

    print("\n" + "=" * 70)
    print(f"WITH block    : {a} rows")
    print(f"WITHOUT block : {b} rows")
    if a == 0 and b > 0:
        print("VERDICT: the ROW IDENTITY block is suppressing rows. Revert it.")
    elif a == 0 and b == 0:
        print("VERDICT: not the block — Stage 1 finds nothing either way on this paper.")
    elif a < b:
        print("VERDICT: the block reduces rows. Worth softening.")
    else:
        print("VERDICT: the block is not costing rows here.")


if __name__ == "__main__":
    asyncio.run(main())
