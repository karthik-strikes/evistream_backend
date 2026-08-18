"""Reproduce the batched-value failure on the EXACT form and paper that hit it.

Job e5ecbfe5 (Aug 11 2026) ran form afcee451 "CD015432 — Continuous Outcomes v2"
over document 9a0fedbe. Stage 1 found 8 rows; the batched value call returned
`filled_rows` as None, twice, and the per-row fallback did the work — 12 calls
where per-row alone costs 9.

Earlier attempts used `eval/studies/ablation/base_schemas/*.json`, which declare
`input_fields: []` — so no paper ever reached the prompt and every run returned
zero rows. That was a harness fault. This pulls the LIVE schema_def, which
declares markdown_content properly.

Two calls: Stage 1, then ONE batched value call with its raw return dumped.

Usage: python zscripts/debug_batch_live.py
"""

from __future__ import annotations

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
from supabase import create_client  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.storage_service import storage_service  # noqa: E402
from utils.circuit_breaker import ModelRouter  # noqa: E402
from utils.dspy_async import async_dspy_forward  # noqa: E402
from utils.lm_config import get_dspy_model  # noqa: E402
from dspy_components import runtime_builders as rb  # noqa: E402

FORM_ID = "afcee451-a73e-478d-8a83-98edb5cc4557"
DOC_ID = os.environ.get("DEBUG_DOC_ID", "9a0fedbe-5a81-4f5e-9a4c-560a3c93d429")


def rule(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def fetch():
    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    form = sb.table("forms").select("form_name, schema_def").eq("id", FORM_ID).execute().data[0]
    doc = sb.table("documents").select(
        "filename, s3_markdown_path"
    ).eq("id", DOC_ID).execute().data[0]
    return form, doc


def pick_table_sig(schema_def: dict):
    for sig in schema_def.get("signatures", []):
        for of in sig.get("output_fields", []):
            if of.get("subform_fields") and of.get("extraction_strategy") == "row_then_columns":
                return sig, of
    raise SystemExit("no row_then_columns table field in this form's schema_def")


async def main() -> None:
    get_dspy_model()
    form, doc = fetch()
    sig_def, field_def = pick_table_sig(form["schema_def"])

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf:
        tmp_md = tf.name
    storage_service.download_to_temp(doc["s3_markdown_path"], tmp_md)
    markdown = Path(tmp_md).read_text(errors="replace")
    os.unlink(tmp_md)
    anchors = list(field_def.get("anchor_columns") or [])
    value_cols = [c["field_name"] for c in field_def["subform_fields"]
                  if c["field_name"] not in set(anchors)]

    print(f"form        : {form['form_name']}")
    print(f"document    : {doc['filename']}  ({len(markdown):,} chars)")
    print(f"field       : {field_def['name']}")
    print(f"inputs      : {[f.get('name') for f in sig_def.get('input_fields', [])]}")
    print(f"anchors     : {anchors}")
    print(f"value cols  : {value_cols}")

    ExtractorCls = rb.build_keyed_extractor_class(sig_def, field_def, "debuglive")
    ex = ExtractorCls()
    router = ModelRouter.get_instance()

    # ── Call 1: Stage 1 ─────────────────────────────────────────────────────
    rule("CALL 1 — Stage 1 (row discovery)")

    async def _s1(**kw):
        return await async_dspy_forward(ex.record_discovery, **kw)

    s1 = await router.run_with_routing(
        async_callable=_s1, operation_name="debuglive:s1", markdown_content=markdown,
    )
    raw_rows = s1.get(field_def["name"]) if hasattr(s1, "get") else None
    rows = raw_rows if isinstance(raw_rows, list) else []
    if isinstance(raw_rows, str):
        try:
            rows = json.loads(raw_rows)
        except Exception:
            rows = []
    records = [
        {a: (r.get(a, {}).get("value") if isinstance(r.get(a), dict) else r.get(a))
         for a in anchors}
        for r in rows if isinstance(r, dict)
    ]
    print(f"rows found : {len(records)}")
    for r in records[:4]:
        print("   ", json.dumps(r, ensure_ascii=False))
    if not records:
        print("reasoning:", str(s1.get('reasoning'))[:800] if hasattr(s1, 'get') else '')
        raise SystemExit("Stage 1 returned no rows — cannot test the batch")

    # ── Call 2: the batched value call that failed ──────────────────────────
    rule("CALL 2 — batched value extraction (the call that returned None)")

    async def _batch(**kw):
        return await async_dspy_forward(ex.set_slot_filler, **kw)

    raw = await router.run_with_routing(
        async_callable=_batch, operation_name="debuglive:batch",
        markdown_content=markdown,
        rows_to_fill=json.dumps(records, ensure_ascii=False),
    )

    keys = list(raw.keys()) if hasattr(raw, "keys") else None
    filled = raw.get("filled_rows") if hasattr(raw, "get") else None
    print(f"return type   : {type(raw).__name__}")
    print(f"keys returned : {keys}")
    print(f"filled_rows   : {type(filled).__name__}")

    rule("VERDICT")
    if filled is None:
        print("REPRODUCED — filled_rows is None.")
        print("keys the model DID fill:", keys)
        if hasattr(raw, "get") and raw.get("reasoning"):
            print("\nreasoning (says what it thought it was doing):")
            print(str(raw["reasoning"])[:2000])
    elif isinstance(filled, list):
        print(f"NOT reproduced — {len(filled)} row(s) returned for {len(records)} asked.")
        if filled:
            print("\nfirst row:", json.dumps(filled[0], ensure_ascii=False)[:900])
        matched = sum(
            1 for it in filled if isinstance(it, dict)
            and rb._canonical_record_key(it, anchors) in {rb._canonical_record_key(a, anchors) for a in records}
        )
        print(f"identity-matched rows: {matched}/{len(records)}")
    else:
        print(f"filled_rows is {type(filled).__name__} → a parse/coercion problem.")

    rule("RAW RETURN (first 3000 chars)")
    print(repr(raw)[:3000])


if __name__ == "__main__":
    asyncio.run(main())
