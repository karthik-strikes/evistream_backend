# System Parallelism & Concurrency

Complete reference for every concurrent, parallel, and async pattern in the evistream backend.
Organized by layer, from the outermost (Celery workers) to the innermost (LRU cache).

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Celery Worker Queues](#2-celery-worker-queues)
3. [Signature Generation — Parallel Enrichment](#3-signature-generation--parallel-enrichment)
4. [Runtime Extraction — Async Pipeline](#4-runtime-extraction--async-pipeline)
5. [WebSocket Live Progress — Redis Pub/Sub](#5-websocket-live-progress--redis-pubsub)
6. [FastAPI Async Endpoints](#6-fastapi-async-endpoints)
7. [Cache Layer](#7-cache-layer)
8. [DSPy Class LRU Cache](#8-dspy-class-lru-cache)
9. [Extraction Retry & Quality Gate](#9-extraction-retry--quality-gate)
10. [LangGraph Workflow Checkpointer](#10-langgraph-workflow-checkpointer)
11. [Rate Limit Budget — 4k calls/min](#11-rate-limit-budget--4k-callsmin)
12. [What Breaks If You Remove X](#12-what-breaks-if-you-remove-x)

---

## 1. Architecture Overview

```
User request
    │
    ▼
FastAPI (async def endpoints)
    │                         │
    ▼                         ▼
Celery task queue         WebSocket
(3 queues)                (Redis pub/sub)
    │
    ├── pdf_processing worker
    │       └── serial S3/text extract
    │
    ├── code_generation worker
    │       └── LangGraph workflow
    │               └── ThreadPoolExecutor  ← signatures in parallel
    │                       └── ThreadPoolExecutor  ← columns in parallel
    │                               └── threading.Semaphore(20)  ← rate cap
    │
    └── extraction worker
            └── asyncio.run()  ← bridge to async world
                    └── StagedPipeline
                            └── asyncio.gather  ← papers × signatures
                                    └── asyncio.Semaphore(N)  ← rate cap
```

---

## 2. Celery Worker Queues

**File:** `app/workers/celery_app.py`

Three queues, each running as a separate OS process group.

| Queue | Tasks | Typical concurrency |
|---|---|---|
| `pdf_processing` | `process_pdf_document`, `check_pdf_processor_health` | 2 workers |
| `code_generation` | `generate_form_code`, `resume_after_approval`, `resume_after_rejection` | 2 workers |
| `extraction` | `run_extraction` | 4 workers |

**Key config:**

```python
worker_prefetch_multiplier = 1      # fetch only the next task after completing current
worker_max_tasks_per_child = 1000   # recycle process every 1000 tasks (memory safety)
task_acks_late = True               # only ack after completion — re-queued if worker dies
task_reject_on_worker_lost = True   # reject (not drop) if worker is killed mid-task
```

`prefetch=1` is critical: LLM tasks can take 30–120s. Without it a worker prefetches
multiple tasks and starves other workers.

**Beat schedule:** `watchdog_cleanup_stuck_jobs` and `watchdog_cleanup_stuck_forms` run every
300s via Celery Beat.

---

## 3. Signature Generation — Parallel Enrichment

Form code generation happens inside the `code_generation` Celery worker. It has
**two levels of parallelism**, both using `threading` (not asyncio) because LangGraph
nodes are synchronous.

### 3a. Level 1 — Parallel Signatures

**File:** `core/generators/workflow.py`, `_node_generate_signatures` (~line 296)

Previously sequential with a 6-second sleep between each signature. Now fully parallel.

```python
MAX_CONCURRENT_LLM_CALLS = 20
semaphore = threading.Semaphore(MAX_CONCURRENT_LLM_CALLS)

with ThreadPoolExecutor(max_workers=len(all_signatures)) as pool:
    futures = [
        pool.submit(_generate_one, (idx, sig))
        for idx, sig in enumerate(all_signatures, 1)
    ]
    for future in as_completed(futures):
        idx, sig_name, enriched_sig, result, err = future.result()
        ...

# Re-sort into original index order (futures complete out of order)
for _, entry in sorted(completed_items, key=lambda x: x[0]):
    if entry is not None:
        signatures_code.append(entry)
```

`max_workers = len(all_signatures)` — one thread per signature.
All threads share a single `threading.Semaphore(20)` that flows down into every LLM call.

### 3b. Level 2 — Parallel Column Enrichment

**File:** `core/generators/signature_gen.py`, `_enrich_subform_columns_independently` (~line 138)

Each subform table field (e.g. `interventions` with 8 columns) spawns a second
`ThreadPoolExecutor` — one thread per column. All threads share the **same semaphore**
passed down from Level 1, so the cap is global across signatures AND columns.

```python
def _enrich_one(col):
    # builds target_sig with sibling context + new_field for this column
    result = self.enrich_new_field(target_sig, new_field, semaphore=semaphore)
    ...

with ThreadPoolExecutor(max_workers=len(user_subfields)) as pool:
    futures = {pool.submit(_enrich_one, col): col for col in user_subfields}
    for future in as_completed(futures):
        original_col, enriched_col = future.result()
        results_by_col[cname] = enriched_col or original_col

# Preserve original column order
out_field["subform_fields"] = [
    results_by_col[col.get("field_name", "")] for col in user_subfields
]
```

### 3c. Semaphore Flow

```
threading.Semaphore(20)                      ← created once in _node_generate_signatures
    │
    ├── generate_signature(semaphore=sem)
    │       ├── _generate_spec_from_enriched_sig(semaphore=sem)
    │       │       └── with semaphore: structured_model.invoke(...)  ← acquired here
    │       │
    │       └── _enrich_subform_columns_independently(semaphore=sem)
    │               └── _enrich_one (× N columns in parallel threads)
    │                       └── enrich_new_field(semaphore=sem)
    │                               └── with semaphore: structured_model.invoke(...)  ← acquired here
    │
    ├── generate_signature(semaphore=sem)  ← Signature 2 (same semaphore)
    └── generate_signature(semaphore=sem)  ← Signature 3
```

Every LLM `.invoke()` call in the enrichment path acquires the semaphore before calling
the API and releases it immediately after. At most 20 calls are in-flight at any moment
across all threads combined.

### 3d. Wall-clock time comparison

| Scenario | Before (sequential) | After (parallel) |
|---|---|---|
| 3 signatures, no subforms | 3 × 3s + 2 × 6s sleep = **21s** | ~**3s** |
| 1 signature, 8-column table | 1 × 3s + 8 × 3s = **27s** | ~**3s** |
| 3 sigs, one with 8 columns | ~**51s** | ~**3s** |

### 3e. Why ThreadPoolExecutor, not asyncio

LangGraph node functions (`_node_generate_signatures`) are synchronous `def`, not
`async def`. Mixing `asyncio.gather` into a sync context requires `asyncio.run()` which
cannot be called inside an already-running event loop (Celery workers have no loop by
default, but it's fragile). `ThreadPoolExecutor` is the correct sync equivalent of
`asyncio.gather` for blocking I/O.

---

## 4. Runtime Extraction — Async Pipeline

**File:** `schemas/config.py`, `StagedPipeline` class

Extraction uses `asyncio` throughout (bridge via `asyncio.run()` in the Celery task).

### 4a. Single document execution

```python
async def __call__(self, markdown_content: str, **kwargs):
    results = {}
    for stage in self.pipeline_stages:
        if stage.execution == "parallel":
            # All extractors in this stage run simultaneously
            stage_results = await asyncio.gather(
                *[self._run_extractor_with_retry(sig_name, ...) for sig_name in stage.signatures],
                return_exceptions=True
            )
        else:
            # Sequential: output of each extractor is input to the next
            for sig_name in stage.signatures:
                result = await self._run_extractor_with_retry(sig_name, ...)
                results[sig_name] = result
```

Stage execution mode (`"parallel"` or `"sequential"`) is set in `schema_def.pipeline_stages`
at form generation time.

### 4b. Batch document execution

```python
async def run_batch(
    self,
    papers: list,
    task_semaphore: asyncio.Semaphore,
    on_paper_done: callable = None,
):
```

**Parallel stages** fan out ALL (paper × signature) combinations at once:

```python
tasks = [
    _run_one(paper["doc_id"], sig_name, paper["markdown_content"], ...)
    for paper in papers
    for sig_name in valid_sig_names   # Cartesian product
]
stage_results = await asyncio.gather(*tasks, return_exceptions=True)
```

For 50 papers × 3 signatures = 150 concurrent async tasks, capped by semaphore.

**Sequential stages** fan out papers but keep extractors in-order per paper:

```python
paper_tasks = [_run_paper_sequential(paper) for paper in papers]
await asyncio.gather(*paper_tasks, return_exceptions=True)
# inside _run_paper_sequential: extractors run one-by-one
```

**Semaphore acquisition:**

```python
async def _run_one(doc_id, sig_name, content, kwargs):
    async with task_semaphore:          # ← acquired here, limits in-flight LLM calls
        return await extractor.forward(content, **kwargs)
```

`task_semaphore` is passed in by the Celery extraction task. Its value is set based on
workload (typically 10–20 concurrent LLM calls per batch job).

### 4c. Progress callback

```python
async def on_paper_done(doc_id, paper_result):
    completed_papers += 1
    progress = 20 + int((completed_papers / total_papers) * 70)  # 20–90%
    broadcaster.progress(progress, f"Processed {completed_papers}/{total_papers}")
```

Fires after ALL pipeline stages for a paper complete, not after each stage.

---

## 5. WebSocket Live Progress — Redis Pub/Sub

**Files:** `app/api/v1/websocket.py`, `app/workers/log_broadcaster.py`

### 5a. Publish path (Celery worker → Redis)

```python
# log_broadcaster.py
channel = f"ws_jobs:{job_id}"

def _broadcast_message(self, payload: dict):
    cache_service.redis_client.publish(channel, json.dumps(payload))   # live
    cache_service.lpush(f"ws_messages:{job_id}", json.dumps(payload))  # replay cache
    cache_service.expire(f"ws_messages:{job_id}", 3600)                # 1h TTL
```

Published message types:
- `progress` — percentage + message
- `stage` — stage name transition
- `data` — structured payloads (`field_list`, `field_done`)
- `complete` / `error` — terminal events

### 5b. Subscribe path (FastAPI → client)

```python
# websocket.py
sub_ready = asyncio.Event()

async def _redis_subscriber():
    async with redis_client.pubsub() as pubsub:
        await pubsub.subscribe(channel)
        sub_ready.set()                          # signal: subscription live
        async for message in pubsub.listen():
            await websocket.send_text(message["data"])

sub_task = asyncio.create_task(_redis_subscriber())

# Wait until subscription is live before replaying cached messages
await asyncio.wait_for(sub_ready.wait(), timeout=5.0)
cached = cache_service.lrange(f"ws_messages:{job_id}", 0, -1)
for msg in cached:
    await websocket.send_text(msg)              # replay history for late-join clients

# Heartbeat loop
while True:
    try:
        await asyncio.wait_for(websocket.receive_text(), timeout=30)
    except asyncio.TimeoutError:
        await websocket.send_text(json.dumps({"type": "ping"}))
```

The `sub_ready` gate prevents a race condition: without it, a message published between
"connection opened" and "subscription registered" would be lost.

---

## 6. FastAPI Async Endpoints

All endpoints in `app/api/v1/` are `async def`. FastAPI runs them on a shared asyncio
event loop via Uvicorn's threadpool.

**Pattern: fire Celery task and return immediately**

```python
@router.post("/forms/{form_id}/generate")
async def generate_form(form_id: UUID, background_tasks: BackgroundTasks, ...):
    job_id = create_job_record(...)
    background_tasks.add_task(generate_form_code.delay, str(form_id), str(job_id))
    return {"job_id": job_id}
```

No blocking I/O in the endpoint — the heavy work is handed off to Celery.
Supabase calls use the sync Python client (blocking), which FastAPI runs in a threadpool
automatically for `async def` endpoints via `run_in_executor`.

---

## 7. Cache Layer

**File:** `app/services/cache_service.py`

All operations are **synchronous** (blocking Redis calls). No async methods.

| Cache key pattern | Purpose | TTL |
|---|---|---|
| `forms:project:{project_id}:*` | Form list per project | 120s |
| `forms:detail:{form_id}` | Single form detail | 300s |
| `dspy:result:{sig}:{hash}` | Cached LLM extraction result | 3600s |
| `semantic:{h1}:{h2}` | Semantic similarity score | 86400s |
| `ws_messages:{job_id}` | WebSocket message history | 3600s |

**Invalidation:**

```python
# After any form mutation:
cache_service.delete_pattern(f"forms:project:{project_id}:*")
cache_service.delete(f"forms:detail:{form_id}")
```

Pattern deletion uses `SCAN` + `DEL` (not `KEYS`) to avoid blocking Redis.

---

## 8. DSPy Class LRU Cache

**File:** `dspy_components/runtime_builders.py`

DSPy `Signature` and `Module` subclasses are built at runtime via `type()`. This is
fast but not free — caching avoids rebuilding the same class on every extraction call.

```python
@lru_cache(maxsize=512)
def _build_signature_class_cached(
    class_name: str,
    task_name: str,
    content_hash: str,     # 16-char SHA256 of canonical JSON
    sig_def_json: str,     # full serialized sig_def
) -> Type[dspy.Signature]:
    return _build_signature_class_impl(class_name, task_name, sig_def_json)
```

Cache key includes `content_hash` so when `schema_def` changes (user edits a field
prompt), the old class is not returned.

`clear_class_cache()` is called after schema invalidation events (e.g. after
`update_field_prompts` saves new hints/rules).

---

## 9. Extraction Retry & Quality Gate

**File:** `schemas/config.py`, `_run_extractor_with_retry` (~line 163)

Each extractor call gets up to `MAX_EXTRACTOR_RETRIES + 1 = 3` attempts.

```python
for attempt in range(MAX_EXTRACTOR_RETRIES + 1):
    result = await extractor.forward(content, **kwargs)

    quality = validate_extraction_output(result_dict)

    if quality.has_real_data:        # ≥1 field is not NR/empty
        return result                # accept on first good result

    if attempt < MAX_EXTRACTOR_RETRIES:
        continue                     # retry with same input

return best_result_across_attempts  # return least-empty result
```

The quality gate prevents returning an all-NR response without retrying. A retry is
triggered only when ALL output fields are NR/empty — partial results are accepted
immediately.

---

## 10. LangGraph Workflow Checkpointer

**File:** `core/generators/workflow.py`, `__init__` (~line 78)

LangGraph persists workflow state between nodes so that the human-review interrupt
(pause between `validate_decomposition` and `human_review` nodes) can survive across
processes.

```python
if _POSTGRES_AVAILABLE and DATABASE_URL:
    self._pg_conn = psycopg.connect(db_url)
    self.checkpointer = PostgresSaver(self._pg_conn)   # cross-worker, persistent
else:
    self.checkpointer = MemorySaver()                  # in-process only
```

**Workaround for cross-worker resume:** LangGraph's `interrupt_before` pauses *before*
running a node. When Celery restarts the workflow on a different worker, the in-memory
`MemorySaver` is empty. Solution: `_node_validate_decomposition` calls
`human_review_handler.backup_state_for_review(state)` to Supabase **before** the
interrupt fires. `resume_after_approval` reads from Supabase (not LangGraph) and calls
`generate_from_approved_decomposition()` directly, bypassing LangGraph resume entirely.

---

## 11. Rate Limit Budget — 4k calls/min

With 4 000 API calls/min and ~2–3s latency per call:

```
Sustainable concurrent calls = (4000 / 60) × 3 = ~200 concurrent
```

How the budget is allocated:

| Workload | Calls per job | Semaphore cap | Notes |
|---|---|---|---|
| Form generation (enrichment) | 1–50 total | `threading.Semaphore(20)` | 1 main + N column calls per sig |
| Batch extraction (50 papers) | up to 150 | `asyncio.Semaphore(10–20)` | passed by caller |
| Single-paper extraction | 1–5 | No semaphore (direct) | one call per stage sig |
| `enrich_new_field` (add field UI) | 1 | No semaphore | interactive, one-shot |

The two semaphore caps (enrichment=20, extraction=10–20) mean two simultaneous form
generations + one large extraction job use at most 60 concurrent calls — well within
the 200-call budget.

**To increase throughput:** raise `MAX_CONCURRENT_LLM_CALLS` in `workflow.py` and the
`task_semaphore` value in `extraction_tasks.py`. Both are the only places to change.

---

## 12. What Breaks If You Remove X

| Component | File + line | If removed |
|---|---|---|
| `prefetch_multiplier=1` | `celery_app.py:36` | Fast tasks starve slow LLM tasks; LLM tasks timeout |
| `task_acks_late=True` | `celery_app.py:42` | Crashed workers drop tasks instead of re-queuing |
| `threading.Semaphore(20)` | `workflow.py:306` | Unbounded concurrent LLM calls → rate limit 429s |
| Outer `ThreadPoolExecutor` (sigs) | `workflow.py:311` | Back to sequential signatures + 6s sleeps |
| Inner `ThreadPoolExecutor` (cols) | `signature_gen.py:235` | Back to sequential column enrichment |
| Column order sort after gather | `signature_gen.py:240` | Columns arrive shuffled → wrong extraction mapping |
| `asyncio.Semaphore` in run_batch | `config.py:411,448` | Rate limit 429s on large batches |
| `asyncio.gather` in parallel stages | `config.py:318` | Sequential extraction (~N× slower) |
| `sub_ready.wait()` gate | `websocket.py:356` | Race condition: messages published before subscription registered are silently lost |
| Redis message cache (lpush) | `log_broadcaster.py:50` | Late-join WebSocket clients see no history |
| `content_hash` in lru_cache key | `runtime_builders.py:407` | Stale DSPy classes served after schema_def changes |
| `clear_class_cache()` | `runtime_builders.py:669` | Stale classes survive schema invalidation indefinitely |
| Supabase state backup before interrupt | `workflow.py:249` | `resume_after_approval` finds no state → generation fails after human review |

---

*Last updated: 2026-05-18*
