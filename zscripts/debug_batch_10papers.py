"""Batched value extraction across 10 different papers — how often does it fail?

One production failure (job e5ecbfe5) returned `filled_rows: None`. Six repeats
of that exact call all succeeded, so the failure is rarer than 1 in 6 on a single
input. This widens the sample across DIFFERENT papers, which vary in length,
table structure and how many rows Stage 1 finds — the things most likely to
trigger it.

Per paper: Stage 1 (real row identities), then ONE batched value call. Records
whether the answer field came back, how many rows, and on failure the reasoning
text and truncation flag.

Usage: python zscripts/debug_batch_10papers.py
Writes a JSON summary next to itself for later reference.
"""

from __future__ import annotations

import os
os.environ["EXTRACTION_BATCH_VALUES"] = "1"

import asyncio
import json
import sys
import tempfile
import time
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
from dspy_components import runtime_builders as rb  # noqa: E402

FORM_ID = "afcee451-a73e-478d-8a83-98edb5cc4557"
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


def markdown_for(sb, doc_id: str) -> str:
    row = sb.table("documents").select("s3_markdown_path").eq("id", doc_id).execute().data[0]
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf:
        tmp = tf.name
    storage_service.download_to_temp(row["s3_markdown_path"], tmp)
    text = Path(tmp).read_text(errors="replace")
    os.unlink(tmp)
    return text


async def main() -> None:
    get_dspy_model()
    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    schema_def = sb.table("forms").select("schema_def").eq("id", FORM_ID).execute().data[0]["schema_def"]

    sig_def = field_def = None
    for sig in schema_def["signatures"]:
        for of in sig.get("output_fields", []):
            if of.get("subform_fields") and of.get("extraction_strategy") == "row_then_columns":
                sig_def, field_def = sig, of
    anchors = list(field_def.get("anchor_columns") or [])

    ExtractorCls = rb.build_keyed_extractor_class(sig_def, field_def, "tenpapers")
    ex = ExtractorCls()
    router = ModelRouter.get_instance()

    print(f"form field : {field_def['name']}   anchors: {anchors}")
    print(f"{'paper':<20}{'chars':>8}{'s1_rows':>9}{'answer':>8}{'rows':>6}{'trunc':>7}{'secs':>7}")
    print("-" * 65)

    results = []
    for doc_id, name in DOCS:
        t0 = time.time()
        rec = {"paper": name, "s1_rows": 0, "answer": None, "rows": 0, "truncated": None}
        try:
            md = markdown_for(sb, doc_id)
            rec["chars"] = len(md)

            async def _s1(**kw):
                return await async_dspy_forward(ex.record_discovery, **kw)

            s1 = await router.run_with_routing(
                async_callable=_s1, operation_name=f"10p:s1:{name}", markdown_content=md,
            )
            raw = s1.get(field_def["name"]) if hasattr(s1, "get") else None
            rows = raw if isinstance(raw, list) else []
            records = [
                {a: (r.get(a, {}).get("value") if isinstance(r.get(a), dict) else r.get(a))
                 for a in anchors}
                for r in rows if isinstance(r, dict)
            ]
            rec["s1_rows"] = len(records)

            if not records:
                rec["answer"] = "skip"
                print(f"{name:<20}{len(md):>8}{0:>9}{'skip':>8}{0:>6}{'-':>7}{time.time()-t0:>7.0f}")
                results.append(rec)
                continue

            async def _batch(**kw):
                return await async_dspy_forward(ex.set_slot_filler, **kw)

            out = await router.run_with_routing(
                async_callable=_batch, operation_name=f"10p:batch:{name}",
                markdown_content=md,
                rows_to_fill=json.dumps(records, ensure_ascii=False),
            )
            filled = out.get("filled_rows") if hasattr(out, "get") else None
            ok = isinstance(filled, list)
            rec["answer"] = "yes" if ok else "NONE"
            rec["rows"] = len(filled) if ok else 0
            rec["truncated"] = bool(was_truncated(ex.set_slot_filler))
            rec["reasoning_len"] = len(str(out.get("reasoning") or "")) if hasattr(out, "get") else 0

            print(f"{name:<20}{len(md):>8}{rec['s1_rows']:>9}{rec['answer']:>8}"
                  f"{rec['rows']:>6}{str(rec['truncated']):>7}{time.time()-t0:>7.0f}")

            if not ok:
                print("\n   *** FAILURE CAUGHT ***")
                print("   keys :", list(out.keys()) if hasattr(out, "keys") else None)
                print("   trunc:", rec["truncated"])
                print("   reasoning:")
                print("  ", str(out.get("reasoning") or "(none)")[:2500].replace("\n", "\n   "))
                print()
        except Exception as exc:
            rec["answer"] = f"EXC:{type(exc).__name__}"
            print(f"{name:<20}{'':>8}{'':>9}{rec['answer']:>8}")
        results.append(rec)

    asked = [r for r in results if r["answer"] in ("yes", "NONE")]
    fails = [r for r in asked if r["answer"] == "NONE"]
    short = [r for r in asked if r["answer"] == "yes" and r["rows"] < r["s1_rows"]]

    print("\n" + "=" * 65)
    print(f"batch attempted : {len(asked)} papers")
    print(f"no answer field : {len(fails)}")
    print(f"short (fewer rows than asked): {len(short)}"
          + (f"  {[(r['paper'], r['rows'], r['s1_rows']) for r in short]}" if short else ""))
    print(f"skipped (Stage 1 found nothing): {sum(1 for r in results if r['answer'] == 'skip')}")

    out_path = Path(__file__).with_name("batch_10papers_result.json")
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nwritten: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
