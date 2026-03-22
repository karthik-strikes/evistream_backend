# Celery Reference

Complete reference for the eviStream Celery background task system.

---

## 1. Quick Start

You need 3 processes running simultaneously:

```bash
# Terminal 1 — Redis (port 6380)
redis-server --port 6380

# Terminal 2 — FastAPI
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8001

# Terminal 3 — Celery worker (all queues, development)
cd backend
celery -A app.workers.celery_app worker --loglevel=info

# Terminal 4 — Beat scheduler (periodic watchdog tasks)
cd backend
celery -A app.workers.celery_app beat --loglevel=info
```

**Production — dedicated workers per queue:**
```bash
celery -A app.workers.celery_app worker -Q pdf_processing  --pool=prefork --concurrency=4
celery -A app.workers.celery_app worker -Q code_generation --pool=gevent  --concurrency=20
celery -A app.workers.celery_app worker -Q extraction      --pool=gevent  --concurrency=30
```

---

## 2. Configuration (`app/workers/celery_app.py`)

```python
celery_app = Celery(
    "evistream_workers",
    broker=settings.CELERY_BROKER_URL,      # redis://localhost:6380/0
    backend=settings.CELERY_RESULT_BACKEND, # redis://localhost:6380/1
)
```

| Setting | Value | Purpose |
|---|---|---|
| `task_serializer` | `json` | All messages serialized as JSON |
| `task_track_started` | `True` | Track when task actually begins |
| `worker_prefetch_multiplier` | `1` | Each worker pulls 1 task at a time (fair queue) |
| `worker_max_tasks_per_child` | `1000` | Recycle workers after 1000 tasks (prevents memory leaks) |
| `task_time_limit` | `3600` | Hard kill after 1 hour |
| `task_soft_time_limit` | `3300` | Graceful shutdown warning at 55 minutes |
| `task_acks_late` | `True` | Only ack task after it completes, not on pickup |
| `task_reject_on_worker_lost` | `True` | Re-queue task if worker crashes mid-execution |
| `result_expires` | `86400` | Results auto-expire after 24 hours |
| `result_compression` | `gzip` | Compress results in Redis to save memory |

**Redis DB layout:**

| DB | Purpose |
|---|---|
| `/0` | Celery broker (task queue) |
| `/1` | Celery result backend |
| `/2` | App cache (`cache_service`) |
| `/3` | Session / WebSocket pub-sub |

Redis runs on port **6380** (intentional, avoids conflict with default 6379).

---

## 3. Task Queues & Routing

| Queue | Tasks |
|---|---|
| `pdf_processing` | `process_pdf_document`, `check_pdf_processor_health` |
| `code_generation` | `generate_form_code`, `resume_after_approval`, `resume_after_rejection`, `check_code_generator_health` |
| `extraction` | `run_extraction`, `check_extraction_service_health` |
| `celery` (default) | `watchdog_cleanup_stuck_jobs` |

---

## 4. All Tasks

### `workers/pdf_tasks.py`

**`process_pdf_document(document_id, job_id)`**
`max_retries=2` · `autoretry_for=(IOError, OSError, ConnectionError)`

Converts an uploaded PDF to Markdown using `pdf_processing_service`.

```
1. Update job → PROCESSING (10%)
2. Load document from Supabase
3. Update document status → PROCESSING (30%)
4. Call pdf_processing_service.process_pdf_to_markdown(pdf_path)
5. Success → update document COMPLETED, store markdown path, job 100%
6. Failure → update document and job FAILED with error message
```

---

### `workers/generation_tasks.py`

**`generate_form_code(form_id, job_id, enable_review)`**
`max_retries=1` · `autoretry_for=(ConnectionError, TimeoutError)`

Runs the full AI code generation pipeline via `WorkflowOrchestrator`.

```
1. Initialize CeleryLogBroadcaster(job_id) for real-time WebSocket streaming
2. Update job → PROCESSING (10%)
3. Load form definition from Supabase
4. Update form → GENERATING
5. Call code_generation_service.generate_extraction_code()
6. Branch:
   - Awaiting Review → store thread_id + decomposition in form.metadata
                     → form → awaiting_review, job paused at 50%
   - Complete       → write files to dspy_components/tasks/{name}/
                     → register schema, form → active, job 100%
   - Failed         → form and job → FAILED
```

**`resume_after_approval(form_id, job_id, thread_id, task_name)`**
`max_retries=1` · `autoretry_for=(ConnectionError, TimeoutError)`

Resumes code generation after user approves the decomposition.

```
1. Load approved decomposition from form.metadata
2. WorkflowOrchestrator(human_review_enabled=False)
3. generate_from_approved_decomposition()
4. Validate syntax of generated signatures.py and modules.py
5. Write files to disk, register schema
6. form → active, job → COMPLETED (100%)
```

**`resume_after_rejection(form_id, job_id, thread_id, task_name, feedback)`**
`max_retries=1` · `autoretry_for=(ConnectionError, TimeoutError)`

Re-runs generation after user rejects with feedback.

```
1. Load previous decomposition from form.metadata
2. Inject human_feedback + previous_decomposition into form_data
3. Re-run generate_extraction_code() — can trigger another review cycle
```

---

### `workers/extraction_tasks.py`

**`run_extraction(extraction_id, job_id, document_ids, max_documents)`**
`max_retries=3` · `autoretry_for=(ConnectionError, TimeoutError)` · `retry_backoff_max=300s`

Executes DSPy extraction on one or many documents.

```
1. Update job → PROCESSING (10%)
2. Load extraction config + schema_name from form
3. Get processed documents from project (filter by document_ids if provided)
4. For each document → extraction_service.run_extraction(markdown_path, schema_name)
   - Documents now processed in parallel (asyncio.gather, semaphore=5)
5. Store each result in extraction_results table
6. Determine status:
   - All succeeded → completed
   - All failed    → failed
   - Partial       → completed (with partial counts)
7. Update job with {total, succeeded, failed} summary
```

**Result record stored per document:**
```json
{
    "extraction_id": "...",
    "job_id": "...",
    "project_id": "...",
    "form_id": "...",
    "document_id": "...",
    "extracted_data": { ... }
}
```

---

### `workers/watchdog_tasks.py`

**`watchdog_cleanup_stuck_jobs()`** — runs every 5 minutes via Beat

```
1. Cutoff = now - (TASK_TIME_LIMIT + 300s) ≈ now - 65 minutes
2. Query jobs with status IN (pending, processing) AND created_at < cutoff
3. For each stuck job:
   - Mark job FAILED: "Timed out: job exceeded maximum processing time"
   - Cascade to associated resource by job_type:
     - PDF_PROCESSING  → document → FAILED
     - FORM_GENERATION → form     → FAILED
     - EXTRACTION      → extraction → failed
```

---

### `workers/log_broadcaster.py`

**`CeleryLogBroadcaster(job_id)`**

Used inside worker tasks to stream logs to WebSocket clients in real time.

```python
broadcaster = CeleryLogBroadcaster(job_id)
broadcaster.info("Starting generation...")
broadcaster.progress(30, "Decomposition complete")
broadcaster.stage("signature_gen", "Generating DSPy signatures")
broadcaster.success("Code generation completed!")
broadcaster.error("Failed to parse form definition")
broadcaster.data({"schema_name": "task_abc123"}, "Schema registered")
```

If no WebSocket client is connected, messages are cached in Redis list `ws_messages:{job_id}` (1hr TTL) for replay when a client connects.

---

## 5. How API Routes Trigger Tasks

Every trigger follows the same 4-step pattern:
```
1. Create resource record in Supabase (document / form / extraction)
2. Create a job record in jobs table (status: pending)
3. Call task.delay(...) → Celery returns celery_task_id
4. Store celery_task_id back in the job record (for cancellation)
```

| API Route | Task Triggered |
|---|---|
| `POST /documents/upload` | `process_pdf_document.delay(document_id, job_id)` |
| `POST /forms` | `generate_form_code.delay(form_id, job_id, enable_review)` |
| `POST /forms/{id}/regenerate` | `generate_form_code.delay(form_id, job_id, enable_review)` |
| `POST /forms/{id}/approve-decomposition` | `resume_after_approval.delay(form_id, job_id, thread_id, task_name)` |
| `POST /forms/{id}/reject-decomposition` | `resume_after_rejection.delay(form_id, job_id, thread_id, task_name, feedback)` |
| `POST /extractions` | `run_extraction.delay(extraction_id, job_id, document_ids, max_documents)` |
| `POST /jobs/{id}/cancel` | `current_app.control.revoke(celery_task_id, terminate=True)` |

---

## 6. Real-Time WebSocket Updates

```
Celery Worker
    └─ CeleryLogBroadcaster.info("Generating signatures...")
            └─ ConnectionManager.broadcast_to_job(job_id, message)
                    ├─ Client connected  → push immediately
                    └─ No client        → cache in Redis ws_messages:{job_id} (1hr TTL)

Client connects to: ws://host/api/v1/ws/jobs/{job_id}
    ├─ Receive all cached messages (replay)
    └─ Then receive live updates
```

**Message format:**
```json
{
    "type": "log | progress | stage | data",
    "level": "info | success | warning | error",
    "message": "Human readable message",
    "progress": 0,
    "timestamp": "2026-03-02T00:00:00Z"
}
```

---

## 7. Complete Data Flows

### Document Upload → PDF Processing
```
POST /api/v1/documents/upload
    ├─ Save PDF to storage
    ├─ Create document record (status: pending)
    ├─ Create job record (type: PDF_PROCESSING)
    └─ process_pdf_document.delay(document_id, job_id)
            ├─ job: 10% → 100%
            ├─ document: pending → processing → completed/failed
            └─ Store markdown path in document record
```

### Form Creation → Code Generation
```
POST /api/v1/forms
    ├─ Create form record (status: generating)
    ├─ Create job record (type: FORM_GENERATION)
    └─ generate_form_code.delay(form_id, job_id, enable_review)
            ├─ AI decomposition via WorkflowOrchestrator
            ├─ IF enable_review:
            │   ├─ Store decomposition in form.metadata
            │   ├─ form → awaiting_review
            │   └─ PAUSE at job 50%
            └─ ELSE:
                ├─ Generate DSPy signatures & modules
                ├─ Write to dspy_components/tasks/{task_name}/
                ├─ Register schema
                └─ form → active, job 100%
```

### Human Review Loop
```
POST /forms/{id}/approve-decomposition
    └─ resume_after_approval.delay(...)
            ├─ Load decomposition from form.metadata
            ├─ Generate code, validate syntax, write files
            └─ form → active, job 100%

POST /forms/{id}/reject-decomposition
    └─ resume_after_rejection.delay(..., feedback)
            ├─ Inject feedback into generation
            └─ Can trigger another review cycle OR complete directly
```

### Extraction Execution
```
POST /api/v1/extractions
    ├─ Verify form.status == "active"
    ├─ Create extraction record
    ├─ Create job record (type: EXTRACTION)
    └─ run_extraction.delay(extraction_id, job_id, ...)
            ├─ Load schema_name from form
            ├─ Get processed documents from project
            ├─ Parallel extraction (asyncio.gather, semaphore=5)
            ├─ Insert results into extraction_results table
            └─ Update job with {total, succeeded, failed}
```

---

## 8. Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| Worker not starting | Missing packages | `pip install celery redis gevent` |
| Redis connection refused | Redis not running | `redis-server --port 6380` |
| Task stays pending forever | Worker not running OR wrong Redis URL | Check `CELERY_BROKER_URL` in `.env`; verify worker is up |
| PDF processor not found | Wrong sys.path | Check `pdf_processor/` exists at project root |
| Tasks not re-queued after crash | `task_acks_late` not set | Already configured — verify `celery_app.py` |

**Useful diagnostic commands:**
```bash
# Check registered tasks
celery -A app.workers.celery_app inspect registered

# Check active tasks
celery -A app.workers.celery_app inspect active

# Check queue depths
celery -A app.workers.celery_app inspect reserved

# Ping all workers
celery -A app.workers.celery_app inspect ping
```
