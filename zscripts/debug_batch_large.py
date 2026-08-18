"""Large-batch test: are the values CORRECT when one call carries 25+ rows?

Batch size is capped at 40 on Claude, but every earlier test was 2-9 rows. The
anchor-legend fix produced a 32-row batch on Polat 2005b that returned 32/32 —
so completeness at that size is shown. This checks the harder property: that the
numbers are real.

For every value cell returned, it verifies:
  - the cell's own quote appears in the paper (via source_linker, the same gate
    production uses), and
  - the value appears inside its quote.

A model that pads a long batch by inventing plausible numbers fails both.

Usage: python zscripts/debug_batch_large.py
"""

from __future__ import annotations

import os
os.environ["EXTRACTION_BATCH_VALUES"] = "1"

import asyncio
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

from supabase import create_client  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.storage_service import storage_service  # noqa: E402
from utils.circuit_breaker import ModelRouter  # noqa: E402
from utils.dspy_async import async_dspy_forward, was_truncated  # noqa: E402
from utils.lm_config import get_dspy_model  # noqa: E402
from utils.source_linker import build_source_index, locate_source, parse_page_boundaries  # noqa: E402
from dspy_components import runtime_builders as rb  # noqa: E402

FORM_ID = "afcee451-a73e-478d-8a83-98edb5cc4557"
DOC_ID = "9a0fedbe-5a81-4f5e-9a4c-560a3c93d429"   # Polat 2005b — 32 rows
THRESHOLD = 0.65


async def main() -> None:
    get_dspy_model()
    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    schema_def = sb.table("forms").select("schema_def").eq("id", FORM_ID).execute().data[0]["schema_def"]
    doc = sb.table("documents").select("filename, s3_markdown_path").eq("id", DOC_ID).execute().data[0]

    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf:
        tmp = tf.name
    storage_service.download_to_temp(doc["s3_markdown_path"], tmp)
    markdown = Path(tmp).read_text(errors="replace")
    os.unlink(tmp)

    sig_def = field_def = None
    for sig in schema_def["signatures"]:
        for of in sig.get("output_fields", []):
            if of.get("subform_fields") and of.get("extraction_strategy") == "row_then_columns":
                sig_def, field_def = sig, of
    anchors = list(field_def["anchor_columns"])
    value_cols = [c["field_name"] for c in field_def["subform_fields"]
                  if c["field_name"] not in set(anchors)]

    ExtractorCls = rb.build_keyed_extractor_class(sig_def, field_def, "large")
    ex = ExtractorCls()
    router = ModelRouter.get_instance()

    async def _s1(**kw):
        return await async_dspy_forward(ex.record_discovery, **kw)

    s1 = await router.run_with_routing(
        async_callable=_s1, operation_name="large:s1", markdown_content=markdown)
    rows = s1.get(field_def["name"]) or []
    records = [
        {a: (r.get(a, {}).get("value") if isinstance(r.get(a), dict) else r.get(a)) for a in anchors}
        for r in rows if isinstance(r, dict)
    ]
    print(f"paper       : {doc['filename']} ({len(markdown):,} chars)")
    print(f"rows locked : {len(records)}")
    print(f"batch size  : {rb._records_per_call(len(value_cols), ex.set_slot_filler)} rows/call")
    if len(records) < 25:
        print(f"NOTE: only {len(records)} rows — below the 25 this test is for.")

    async def _batch(**kw):
        return await async_dspy_forward(ex.set_slot_filler, **kw)

    out = await router.run_with_routing(
        async_callable=_batch, operation_name="large:batch",
        markdown_content=markdown,
        rows_to_fill=json.dumps(records, ensure_ascii=False),
    )
    filled = out.get("filled_rows")
    print(f"rows back   : {len(filled) if isinstance(filled, list) else 'NONE'} / {len(records)}")
    print(f"truncated   : {was_truncated(ex.set_slot_filler)}")
    if not isinstance(filled, list):
        raise SystemExit("batch returned no rows")

    keys = {rb._canonical_record_key(a, anchors) for a in records}
    matched = sum(1 for r in filled if isinstance(r, dict) and rb._canonical_record_key(r, anchors) in keys)
    dupes = len(filled) - len({rb._canonical_record_key(r, anchors) for r in filled if isinstance(r, dict)})
    print(f"identity ok : {matched}/{len(filled)}   duplicate identities: {dupes}")

    # ── the real question: are the numbers real? ────────────────────────────
    idx = build_source_index(markdown, parse_page_boundaries(markdown))
    tally = Counter()
    bad: list[str] = []
    for r in filled:
        if not isinstance(r, dict):
            continue
        rid = " · ".join(str(r.get(a, "")) for a in anchors)
        for col in value_cols:
            cell = r.get(col)
            if not isinstance(cell, dict):
                tally["malformed"] += 1
                continue
            val, quote = cell.get("value"), str(cell.get("source_text") or "")
            if str(val).strip().upper() in ("NR", "NA", "", "NONE"):
                tally["nr"] += 1
                continue
            tally["values"] += 1
            loc = locate_source(quote, idx, threshold=THRESHOLD)
            if loc is None or getattr(loc, "confidence", 0) < THRESHOLD:
                tally["quote_not_in_paper"] += 1
                bad.append(f"  {rid} · {col}={val!r}  quote NOT found: {quote[:70]!r}")
                continue
            tally["quote_ok"] += 1
            hay = " ".join(str(quote).split()).lower()
            if str(val).strip().lower() in hay:
                tally["value_in_quote"] += 1
            else:
                tally["value_not_in_quote"] += 1
                bad.append(f"  {rid} · {col}={val!r}  not inside its quote: {quote[:70]!r}")

    print("\n" + "=" * 68)
    print(f"cells with a value      : {tally['values']}")
    print(f"  quote found in paper  : {tally['quote_ok']}")
    print(f"  quote NOT found       : {tally['quote_not_in_paper']}")
    print(f"  value inside its quote: {tally['value_in_quote']}")
    print(f"  value NOT in its quote: {tally['value_not_in_quote']}")
    print(f"deliberate NR cells     : {tally['nr']}")
    if bad:
        print(f"\nsuspect cells ({len(bad)}):")
        for b in bad[:20]:
            print(b)
    else:
        print("\nevery value traces to a quote that exists in the paper.")


if __name__ == "__main__":
    asyncio.run(main())
