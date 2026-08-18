# Table-extraction timing profiler

`table_extraction_timing.ipynb` — pick any **production form + PDF**, run that form's table field
with per-operation instrumentation, and see where the wall clock goes.

```
table_extraction_timing.ipynb   the notebook (start here)
table_timing.py                 instrumentation, prod pickers, analysis helpers
outputs/<stamp>_<field>/        calls.csv · spans.csv · step_summary.csv · paper_summary.csv · run.json
```

## Running it

Kernel must be the **`topics` conda env** (`/home/ubuntu/miniconda3/envs/topics/bin/python`) — the
backend's DSPy/litellm stack is not in `base`. `tt.bootstrap()` loads prod secrets from AWS
Secrets Manager (`AWS_SECRETS_NAME=evistream/production`), so Supabase + S3 + Bedrock all work
from the notebook with no extra setup.

There is no headless runner in `topics` (no `nbconvert`/`nbclient`, and installing into that env
would touch the one serving production). To run it non-interactively, import `table_timing` from a
plain script instead — every notebook cell is a call into that module.

## What it measures

| Step | Calls per paper |
|---|---|
| `record_discovery` | 1 |
| `recall_audit` | 1 |
| `slot_fill_set` (default) | `ceil(records / ~40)` |
| `slot_fill_row` (`EXTRACTION_BATCH_VALUES=0`, or set-call fallback) | 1 per record |
| `refill` | 0–2 rounds, only for records that came back empty |

Per call: our wall clock, litellm's `_response_ms`, tokens, prompt-cache read/write, cost,
finish reason, response shape (a `json` shape means DSPy's parse retry fired). Per step:
`busy_s` (union of intervals — the real wall-clock contribution) vs `api_s_sum` (all latencies
added), whose ratio is the concurrency actually achieved.

Step names come from the extractor's own sub-modules, stamped at construction — not from call
ordering — and are cross-checked against `utils/llm_call_labels.classify_step`, the code that
labels production cost rows. `tt.label_check(run)` shows any disagreement.

## Gotchas

- **`EXTRACTION_BATCH_VALUES` defaults to `1`** (`runtime_builders.py:1339`), so production runs
  **set-at-a-time**, despite the older comment block above `__call__` saying per-row is the
  default. Read `slot_filling` in the runtime table rather than trusting either.
- `bootstrap()` disables DSPy's response cache. A cached response returns in microseconds, so
  timing one is not timing the pipeline.
- Section 5 reads `llm_history` / `jobs` and costs nothing. It is the only place the **Celery
  queue wait** is visible — an in-process profiler cannot see it.
- Rows in `llm_history` older than the Aug 2026 per-call labels have no `duration_ms`; their step
  is recovered from the stored prompt, so they count toward volume and cost but not latency.
