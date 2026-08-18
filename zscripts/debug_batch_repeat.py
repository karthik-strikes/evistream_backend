"""Run the SAME batched value call N times to catch the failure and see its cause.

The failure (job e5ecbfe5) was `filled_rows: None` with no exception and no
adapter error. One occurrence told us nothing because `_fill_slots_set` returned
before checking truncation and never logged the raw completion. This reproduces
the call repeatedly and, on every attempt, records:

  - whether the answer field was present
  - finish_reason (is it a max_tokens cutoff?)
  - output token count vs the ceiling
  - the length of `reasoning` (ChainOfThought writes it BEFORE the answer, so a
    runaway reasoning can consume the budget and leave the answer unwritten)

Usage: python zscripts/debug_batch_repeat.py [n=5]
"""

from __future__ import annotations

import os
os.environ["EXTRACTION_BATCH_VALUES"] = "1"

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
DOC_ID = "9a0fedbe-5a81-4f5e-9a4c-560a3c93d429"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5

# The 8 identities Stage 1 found on this paper — hardcoded so every attempt is
# byte-identical and Stage 1 is not re-billed.
ROWS = [
    {"comparison": c, "outcome_type": "pain_intensity", "reporter": "child",
     "timepoint": t, "scale": "VAS"}
    for c in ("placebo", "paracetamol", "naproxen_sodium", "aspirin")
    for t in ("2h_to_24h", "24h_to_7d")
]


def last_call_stats(lm):
    """finish_reason + token usage from the LM's last history entry."""
    hist = getattr(lm, "history", None) or []
    if not hist:
        return {}
    h = hist[-1]
    resp = h.get("response") if isinstance(h, dict) else None
    out = {"max_tokens": (h.get("kwargs") or {}).get("max_tokens")}
    try:
        choices = resp.get("choices") if hasattr(resp, "get") else getattr(resp, "choices", None)
        first = choices[0]
        out["finish_reason"] = (
            first.get("finish_reason") if hasattr(first, "get")
            else getattr(first, "finish_reason", None)
        )
    except Exception:
        out["finish_reason"] = None
    try:
        usage = resp.get("usage") if hasattr(resp, "get") else getattr(resp, "usage", None)
        out["completion_tokens"] = (
            usage.get("completion_tokens") if hasattr(usage, "get")
            else getattr(usage, "completion_tokens", None)
        )
    except Exception:
        out["completion_tokens"] = None
    return out


async def main() -> None:
    lm = get_dspy_model()
    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    form = sb.table("forms").select("schema_def").eq("id", FORM_ID).execute().data[0]
    doc = sb.table("documents").select("s3_markdown_path").eq("id", DOC_ID).execute().data[0]

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf:
        tmp = tf.name
    storage_service.download_to_temp(doc["s3_markdown_path"], tmp)
    markdown = Path(tmp).read_text(errors="replace")
    os.unlink(tmp)

    sig_def = field_def = None
    for sig in form["schema_def"]["signatures"]:
        for of in sig.get("output_fields", []):
            if of.get("subform_fields") and of.get("extraction_strategy") == "row_then_columns":
                sig_def, field_def = sig, of
    ExtractorCls = rb.build_keyed_extractor_class(sig_def, field_def, "repeat")
    ex = ExtractorCls()
    router = ModelRouter.get_instance()
    payload = json.dumps(ROWS, ensure_ascii=False)

    print(f"paper {len(markdown):,} chars · {len(ROWS)} rows asked · {N} attempts\n")
    print(f"{'#':>2} {'answer':>7} {'rows':>5} {'finish':>12} {'out_tok':>8} "
          f"{'max_tok':>8} {'reasoning':>10}")
    print("-" * 62)

    fails = 0
    for i in range(1, N + 1):
        async def _batch(**kw):
            return await async_dspy_forward(ex.set_slot_filler, **kw)

        try:
            out = await router.run_with_routing(
                async_callable=_batch, operation_name=f"repeat:{i}",
                markdown_content=markdown, rows_to_fill=payload,
            )
        except Exception as exc:
            print(f"{i:>2} {'EXC':>7} {'-':>5} {type(exc).__name__:>12}")
            fails += 1
            continue

        filled = out.get("filled_rows") if hasattr(out, "get") else None
        reason = out.get("reasoning") if hasattr(out, "get") else None
        st = last_call_stats(lm)
        ok = isinstance(filled, list)
        if not ok:
            fails += 1
        print(f"{i:>2} {('yes' if ok else 'NONE'):>7} "
              f"{(len(filled) if ok else 0):>5} {str(st.get('finish_reason')):>12} "
              f"{str(st.get('completion_tokens')):>8} {str(st.get('max_tokens')):>8} "
              f"{len(str(reason or '')):>10}")
        if not ok:
            print("\n   *** FAILURE CAUGHT ***")
            print("   keys returned:", list(out.keys()) if hasattr(out, "keys") else None)
            print("   finish_reason:", st.get("finish_reason"))
            print("   reasoning (full):")
            print("  ", str(reason or "(none)")[:3000].replace("\n", "\n   "))

    print(f"\n{fails}/{N} attempts returned no answer field.")
    if fails == 0:
        print("Not caught in this sample — the failure is rarer than 1 in "
              f"{N}, so a retry is the right mitigation regardless.")


if __name__ == "__main__":
    asyncio.run(main())
