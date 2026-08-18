"""Grounding quality across 10 papers — is 192/192 typical or lucky?

After the derivation-clause fix, Polat 2005b returned 32 rows in one batch with
every value traceable to a quote that exists in the paper. That is one paper.
This runs the same measurement over ten, so the number means something.

Per paper: Stage 1 → one batched value call → verify EVERY value cell with the
same gate production uses (source_linker at 0.65), plus a check that the value
appears inside its own quote. Derived values are counted separately: they are
allowed to sit outside the quote provided the model declared the arithmetic in a
`derived` key, which is what the derivation clause asks for.

Usage: python zscripts/debug_grounding_10papers.py
"""

from __future__ import annotations

import os
os.environ["EXTRACTION_BATCH_VALUES"] = "1"

import asyncio
import json
import sys
import tempfile
import time
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
THRESHOLD = 0.65
DOCS = [
    ("ab62950c-099e-4f8a-b3d2-84ed21e2bad6", "Abdelbaser 2022a"),
    ("8448b9f9-6d67-49e5-8ac7-6d8c2a31c2f5", "Abdelbaser 2022b"),
    ("87b40b17-113d-4d4b-8e0b-6e08bb973480", "Abou 2019"),
    ("18a02287-0e3f-4c4e-9ebc-3eb67ddc904d", "Alshami 2021"),
    ("9615f51f-2f61-4812-b2cd-970f6d68271d", "Bahrololoomi 2019"),
    ("6b8dfd1e-96a0-4cff-bafd-1f0367245cdd", "Bennie 1997"),
    ("655d899e-e2cb-49b0-881e-3344ce6925fc", "Bernhardt 2001"),
    ("907d86f2-ec0a-4eaf-a416-ca993a310691", "Bird 2007"),
    ("45f4ef58-b501-4150-a665-cf95f0343a59", "Bogaert 2004"),
    ("4bafb0bb-4fae-4d9e-b9f8-f082024f6bfc", "Bradley 2007"),
]


async def main() -> None:
    get_dspy_model()
    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    schema_def = sb.table("forms").select("schema_def").eq("id", FORM_ID).execute().data[0]["schema_def"]

    sig_def = field_def = None
    for sig in schema_def["signatures"]:
        for of in sig.get("output_fields", []):
            if of.get("subform_fields") and of.get("extraction_strategy") == "row_then_columns":
                sig_def, field_def = sig, of
    anchors = list(field_def["anchor_columns"])
    value_cols = [c["field_name"] for c in field_def["subform_fields"]
                  if c["field_name"] not in set(anchors)]

    ExtractorCls = rb.build_keyed_extractor_class(sig_def, field_def, "ground10")
    ex = ExtractorCls()
    router = ModelRouter.get_instance()

    print(f"{'paper':<20}{'rows':>5}{'back':>6}{'cells':>7}{'quoted':>8}"
          f"{'in_quote':>10}{'derived':>9}{'bad':>5}{'secs':>6}")
    print("-" * 78)

    total = Counter()
    per_paper = []
    all_bad: list[str] = []

    for doc_id, name in DOCS:
        t0 = time.time()
        try:
            doc = sb.table("documents").select("s3_markdown_path").eq("id", doc_id).execute().data[0]
            with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf:
                tmp = tf.name
            storage_service.download_to_temp(doc["s3_markdown_path"], tmp)
            markdown = Path(tmp).read_text(errors="replace")
            os.unlink(tmp)

            async def _s1(**kw):
                return await async_dspy_forward(ex.record_discovery, **kw)

            s1 = await router.run_with_routing(
                async_callable=_s1, operation_name=f"g10:s1:{name}", markdown_content=markdown)
            rows = s1.get(field_def["name"]) or []
            records = [
                {a: (r.get(a, {}).get("value") if isinstance(r.get(a), dict) else r.get(a))
                 for a in anchors}
                for r in rows if isinstance(r, dict)
            ]
            if not records:
                print(f"{name:<20}{0:>5}{'-':>6}{'-':>7}{'-':>8}{'-':>10}{'-':>9}{'-':>5}"
                      f"{time.time()-t0:>6.0f}")
                per_paper.append({"paper": name, "rows": 0})
                continue

            async def _batch(**kw):
                return await async_dspy_forward(ex.set_slot_filler, **kw)

            out = await router.run_with_routing(
                async_callable=_batch, operation_name=f"g10:batch:{name}",
                markdown_content=markdown,
                rows_to_fill=json.dumps(records, ensure_ascii=False),
            )
            filled = out.get("filled_rows")
            if not isinstance(filled, list):
                print(f"{name:<20}{len(records):>5}{'NONE':>6}")
                per_paper.append({"paper": name, "rows": len(records), "back": None})
                total["batch_failed"] += 1
                continue

            idx = build_source_index(markdown, parse_page_boundaries(markdown))
            t = Counter()
            for r in filled:
                if not isinstance(r, dict):
                    continue
                rid = " · ".join(str(r.get(a, "")) for a in anchors)
                for col in value_cols:
                    cell = r.get(col)
                    if not isinstance(cell, dict):
                        t["malformed"] += 1
                        continue
                    val = cell.get("value")
                    quote = str(cell.get("source_text") or "")
                    if str(val).strip().upper() in ("NR", "NA", "", "NONE"):
                        t["nr"] += 1
                        continue
                    t["cells"] += 1
                    loc = locate_source(quote, idx, threshold=THRESHOLD)
                    if loc is None or getattr(loc, "confidence", 0) < THRESHOLD:
                        t["quote_missing"] += 1
                        all_bad.append(f"{name} · {rid} · {col}={val!r} quote NOT found: {quote[:60]!r}")
                        continue
                    t["quoted"] += 1
                    hay = " ".join(quote.split()).lower()
                    if str(val).strip().lower() in hay:
                        t["in_quote"] += 1
                    elif cell.get("derived"):
                        # Allowed: the derivation clause says a computed value is
                        # grounded on its INPUTS, with the arithmetic declared.
                        t["derived_ok"] += 1
                    else:
                        t["value_missing"] += 1
                        all_bad.append(f"{name} · {rid} · {col}={val!r} not in quote, no derived key: {quote[:60]!r}")

            bad = t["quote_missing"] + t["value_missing"] + t["malformed"]
            print(f"{name:<20}{len(records):>5}{len(filled):>6}{t['cells']:>7}"
                  f"{t['quoted']:>8}{t['in_quote']:>10}{t['derived_ok']:>9}{bad:>5}"
                  f"{time.time()-t0:>6.0f}")
            total.update(t)
            per_paper.append({"paper": name, "rows": len(records), "back": len(filled), **t})
        except Exception as exc:
            print(f"{name:<20}{'EXC':>5} {type(exc).__name__}: {exc}")
            total["exceptions"] += 1

    cells = total["cells"]
    good = total["in_quote"] + total["derived_ok"]
    print("\n" + "=" * 78)
    print(f"value cells checked        : {cells}")
    print(f"  quote found in the paper : {total['quoted']}"
          + (f"  ({100*total['quoted']/cells:.1f}%)" if cells else ""))
    print(f"  value inside its quote   : {total['in_quote']}")
    print(f"  derived, arithmetic given: {total['derived_ok']}")
    print(f"  GOOD (either)            : {good}"
          + (f"  ({100*good/cells:.1f}%)" if cells else ""))
    print(f"  quote not found          : {total['quote_missing']}")
    print(f"  value absent, not derived: {total['value_missing']}")
    print(f"deliberate NR cells        : {total['nr']}")
    print(f"batches that returned none : {total['batch_failed']}")

    if all_bad:
        print(f"\nungrounded cells ({len(all_bad)}), first 15:")
        for b in all_bad[:15]:
            print("  " + b)

    Path(__file__).with_name("grounding_10papers_result.json").write_text(
        json.dumps({"per_paper": per_paper, "total": dict(total)}, indent=2))
    print("\nwritten: zscripts/grounding_10papers_result.json")


if __name__ == "__main__":
    asyncio.run(main())
