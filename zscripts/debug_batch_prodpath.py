"""Run the WHOLE extractor exactly as production does, with everything visible.

Every earlier harness called individual predictors (`record_discovery`, then `set_slot_filler`)
through separate `run_with_routing` calls. Production calls the whole extractor
once — `StagedPipeline._run_extractor_with_retry` → `run_with_routing(extractor)`
— so record_discovery, the census checker and the batch all run inside ONE dspy.context.
That is the only structural difference left between 15+ harness successes and
4/4 production failures.

Also unmutes DSPy's own logger. `dspy/utils/logging_utils.py:67` sets
`propagate = False` and attaches DSPy's own handler, so warnings like
"Failed to use structured output format, falling back to JSON mode" never reach
our log files OR the journal. They may have been firing on every production call
this whole time, unobserved.

Combined with the raw-completion logging already in `_fill_slots_set_once`, one run
answers: did the model emit the array, or did the adapter fail to parse it?

Usage:
    python zscripts/debug_batch_prodpath.py            # full production path
    python zscripts/debug_batch_prodpath.py --no-census  # bisect step 2
"""

from __future__ import annotations

import os
os.environ["EXTRACTION_BATCH_VALUES"] = "1"     # read at import; must be first

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Make everything audible before any dspy import settles its handlers.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-7s %(name)s | %(message)s",
    stream=sys.stdout,
)

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

import dspy  # noqa: E402

# Undo DSPy's logger isolation so its adapter warnings become visible.
for _name in ("dspy", "dspy.adapters", "dspy.adapters.json_adapter",
              "dspy.adapters.chat_adapter", "dspy.predict"):
    _lg = logging.getLogger(_name)
    _lg.propagate = True
    _lg.setLevel(logging.DEBUG)

from supabase import create_client  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.storage_service import storage_service  # noqa: E402
from utils.circuit_breaker import ModelRouter  # noqa: E402
from utils.lm_config import get_dspy_model  # noqa: E402
from dspy_components import runtime_builders as rb  # noqa: E402

FORM_ID = "afcee451-a73e-478d-8a83-98edb5cc4557"
DOC_ID = os.environ.get("DEBUG_DOC_ID", "9a0fedbe-5a81-4f5e-9a4c-560a3c93d429")
NO_CENSUS = "--no-census" in sys.argv


async def main() -> None:
    get_dspy_model()
    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    form = sb.table("forms").select("form_name, schema_def").eq("id", FORM_ID).execute().data[0]
    doc = sb.table("documents").select("filename, s3_markdown_path").eq("id", DOC_ID).execute().data[0]

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
    if field_def is None:
        raise SystemExit("no row_then_columns field on this form")

    print("=" * 78)
    print(f"form     : {form['form_name']}")
    print(f"document : {doc['filename']} ({len(markdown):,} chars)")
    print(f"census   : {'DISABLED (bisect)' if NO_CENSUS else 'enabled (production default)'}")
    print(f"batching : {rb._SET_AT_A_TIME}")
    print(f"adapter  : {type(dspy.settings.adapter).__name__ if dspy.settings.adapter else None}")
    print("=" * 78)

    ExtractorCls = rb.build_keyed_extractor_class(sig_def, field_def, "prodpath")

    if NO_CENSUS:
        # Step 2 of the plan: is the preceding census call what breaks the batch?
        async def _skip(self, markdown_content, rows, ctx_kwargs, dbg):
            return rows
        ExtractorCls._audit_record_set = _skip

    ex = ExtractorCls()
    router = ModelRouter.get_instance()

    # THE production call shape: the whole extractor, one context.
    result = await router.run_with_routing(
        async_callable=ex, operation_name="prodpath", markdown_content=markdown,
    )

    env = result.get(field_def["name"], {}) if isinstance(result, dict) else {}
    rows = env.get("value") if isinstance(env, dict) else None
    print("\n" + "=" * 78)
    print("RESULT")
    print("=" * 78)
    print(f"status        : {env.get('status') if isinstance(env, dict) else '(none)'}")
    print(f"rows          : {len(rows) if isinstance(rows, list) else repr(rows)}")
    if isinstance(env, dict) and env.get("unfilled_rows"):
        print(f"unfilled rows : {env['unfilled_rows']}")
    if isinstance(rows, list) and rows:
        vals = [c for r in rows if isinstance(r, dict) for c in r.values()
                if isinstance(c, dict) and str(c.get("value")).strip().upper() not in ("NR", "NA", "")]
        print(f"non-NR cells  : {len(vals)}")
    print("\nRead the log above for:")
    print("  · 'Failed to use structured output format' — the adapter fallback")
    print("  · 'RAW completion' — what the model actually sent back")


if __name__ == "__main__":
    asyncio.run(main())
