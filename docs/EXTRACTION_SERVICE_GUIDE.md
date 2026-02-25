# Extraction Service & Celery Tasks Guide

Complete guide for the extraction service and background extraction tasks.

---

## 📋 Overview

The extraction service enables asynchronous extraction of structured data from processed documents using dynamically generated DSPy code.

### Architecture

```
User triggers extraction → API creates job → Celery task processes documents → Results saved to database
```

**Components:**

1. **ExtractionService** (`app/services/extraction_service.py`)
   - Wraps `core/extractor.py`
   - Handles single and batch extractions
   - Manages DSPy configuration

2. **Extraction Celery Task** (`app/workers/extraction_tasks.py`)
   - Background processing
   - Database updates
   - Progress tracking

3. **Core Extraction Logic** (`core/extractor.py`)
   - DSPy pipeline execution
   - Schema runtime management
   - Result formatting

---

## 🚀 Quick Start

### Prerequisites

1. **Redis running** (for Celery broker)
   ```bash
   redis-server
   ```

2. **FastAPI backend running**
   ```bash
   cd backend
   python -m app.main
   ```

3. **Celery worker running**
   ```bash
   cd backend
   celery -A app.workers.celery_app worker --loglevel=info
   ```

### Test the Service

```bash
cd backend
python test_extraction_service.py
```

**Expected output:**
```
Testing imports...
✓ extraction_service imported
✓ extraction_tasks imported
✓ core.extractor imported
✓ schemas imported

Testing service initialization...
Service status: {'status': 'ready', 'dspy_configured': True}
✓ Service initialization check passed

Testing schema loading...
Available schemas: ['patient_population', 'index_test', ...]
✓ Successfully loaded schema: patient_population

Testing Celery task registration...
Registered tasks: 12
  - run_extraction
  - check_extraction_service_health
  - process_pdf_document
  - generate_form_code
✓ run_extraction task registered
✓ check_extraction_service_health task registered

🎉 All tests passed!
```

---

## 📖 Usage

### 1. Single Document Extraction

**Via API (coming soon):**
```bash
POST /api/v1/extractions
{
  "project_id": "uuid",
  "form_id": "uuid",
  "document_ids": ["doc-uuid-1"]
}
```

**Direct service call:**
```python
from app.services.extraction_service import extraction_service

result = extraction_service.run_extraction(
    markdown_path="/path/to/document.md",
    schema_name="patient_population",
    ground_truth=None
)

print(result)
# {
#   "success": True,
#   "results": [{...extracted data...}],
#   "source_file": "/path/to/document.md"
# }
```

### 2. Batch Extraction

**Via API (coming soon):**
```bash
POST /api/v1/extractions
{
  "project_id": "uuid",
  "form_id": "uuid",
  "max_documents": 10
}
```

**Direct service call:**
```python
from app.services.extraction_service import extraction_service

result = extraction_service.run_extraction(
    markdown_path="/path/to/markdown_directory/",
    schema_name="patient_population",
    max_documents=10
)

print(result)
# {
#   "success": True,
#   "total_documents": 10,
#   "successful_extractions": 9,
#   "failed_extractions": 1,
#   "results": [{...}, {...}, ...]
# }
```

### 3. Background Task Execution

**Trigger via Celery:**
```python
from app.workers.extraction_tasks import run_extraction

# Queue the task
task = run_extraction.delay(
    extraction_id="extraction-uuid",
    job_id="job-uuid",
    document_ids=["doc-1", "doc-2"],
    max_documents=10
)

# Check status
print(task.status)  # 'PENDING', 'PROCESSING', 'SUCCESS', 'FAILURE'

# Get result (blocks until complete)
result = task.get()
print(result)
```

---

## 🔧 Configuration

### Environment Variables

Required in `.env`:

```bash
# LLM API Keys (at least one required)
GEMINI_API_KEY=your_gemini_key
OPENAI_API_KEY=your_openai_key
ANTHROPIC_API_KEY=your_anthropic_key

# Database
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your_service_key

# Celery
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

### DSPy Models

Configured in `core/config.py`:

```python
# Extraction model (high quality)
DEFAULT_MODEL = "gemini/gemini-3-pro-preview"

# Evaluation model (fast)
EVALUATION_MODEL = "gemini/gemini-2.5-flash"
```

### Concurrency Settings

```python
# Number of papers processed in parallel
BATCH_CONCURRENCY = 5

# Concurrent semantic matching calls
EVALUATION_CONCURRENCY = 20
```

---

## 🗄️ Database Schema

### Tables Used

**1. extractions**
```sql
CREATE TABLE extractions (
  id UUID PRIMARY KEY,
  project_id UUID REFERENCES projects(id),
  form_id UUID REFERENCES forms(id),
  status TEXT,  -- 'pending', 'processing', 'completed', 'failed'
  created_at TIMESTAMP
);
```

**2. extraction_results**
```sql
CREATE TABLE extraction_results (
  id UUID PRIMARY KEY,
  extraction_id UUID REFERENCES extractions(id),
  document_id UUID REFERENCES documents(id),
  extracted_data JSONB,  -- Extracted structured data
  created_at TIMESTAMP
);
```

**3. jobs**
```sql
CREATE TABLE jobs (
  id UUID PRIMARY KEY,
  job_type TEXT,  -- 'extraction', 'pdf_processing', 'code_generation'
  status TEXT,  -- 'pending', 'processing', 'completed', 'failed'
  progress INTEGER,  -- 0-100
  celery_task_id TEXT,
  result_data JSONB,
  error_message TEXT,
  created_at TIMESTAMP
);
```

---

## 🔄 Workflow Details

### Complete Extraction Flow

1. **User creates extraction job** (via API)
   - API validates form has generated code
   - Creates `extraction` record (status: 'pending')
   - Creates `job` record (status: 'pending')
   - Triggers Celery task

2. **Celery worker picks up task**
   - Updates job status to 'processing'
   - Loads extraction configuration from database
   - Gets schema name from form

3. **Service runs extraction**
   - Configures DSPy LM
   - Loads schema runtime
   - Processes single file or batch
   - Returns extracted results

4. **Task saves results**
   - Inserts records into `extraction_results`
   - Updates extraction status to 'completed'
   - Updates job progress to 100%

5. **User retrieves results** (via API)
   - GET `/api/v1/extractions/{id}/results`
   - Returns structured data from `extraction_results`

### Progress Updates

The task updates job progress at these milestones:
- **10%** - Task started, loading configuration
- **20%** - Documents loaded, starting extraction
- **90%** - Extraction complete, saving results
- **100%** - Results saved, job complete

---

## 🐛 Troubleshooting

### Issue: "DSPy not configured"

**Error:**
```
Service status: {'status': 'error', 'error': '...', 'dspy_configured': False}
```

**Fix:**
1. Check API keys in `.env`
2. Verify `utils/lm_config.py` can load configuration
3. Run: `python -c "from utils.lm_config import configure_lm_for_dspy; configure_lm_for_dspy()"`

---

### Issue: "Schema not found"

**Error:**
```
Schema 'my_schema' not found in registry
```

**Fix:**
1. Check schema is registered: `python -c "from schemas import list_schemas; print(list_schemas())"`
2. If using dynamic schema, verify code generation completed
3. Check `dspy_components/tasks/` directory contains schema folder

---

### Issue: "No processed documents found"

**Error:**
```
No processed documents found for project {project_id}
```

**Fix:**
1. Ensure documents have been uploaded
2. Check documents have `processing_status = 'completed'`
3. Verify markdown files exist at `s3_markdown_path`
4. Run PDF processing first: `POST /api/v1/documents/upload`

---

### Issue: "Task stays pending forever"

**Possible causes:**
1. Celery worker not running
2. Redis not running
3. Task not registered

**Fix:**
```bash
# Check worker is running
celery -A app.workers.celery_app inspect active

# Check registered tasks
celery -A app.workers.celery_app inspect registered | grep extraction

# Restart worker with verbose logging
celery -A app.workers.celery_app worker --loglevel=debug
```

---

### Issue: "Extraction fails with timeout"

**Error:**
```
LLM call timed out after 600 seconds
```

**Fix:**
1. Increase timeout in `core/config.py`:
   ```python
   LLM_TIMEOUT_SECONDS = 1200  # 20 minutes
   ```
2. Use faster model for large documents:
   ```python
   DEFAULT_MODEL = "gemini/gemini-2.5-flash"
   ```
3. Split large documents into chunks

---

## 📊 Monitoring

### Check Service Health

**Via Celery task:**
```python
from app.workers.extraction_tasks import check_extraction_service_health

result = check_extraction_service_health.delay()
print(result.get())
# {'status': 'ready', 'dspy_configured': True}
```

### Monitor Task Queue

```bash
# Active tasks
celery -A app.workers.celery_app inspect active

# Scheduled tasks
celery -A app.workers.celery_app inspect scheduled

# Worker stats
celery -A app.workers.celery_app inspect stats
```

### Check Redis Queue

```bash
redis-cli
> KEYS celery*
> LLEN extraction  # Number of tasks in extraction queue
> GET celery-task-meta-{task-id}
```

---

## 🧪 Testing

### Unit Tests (Planned)

```python
# tests/test_extraction_service.py
import pytest
from app.services.extraction_service import extraction_service

def test_single_extraction():
    result = extraction_service.run_extraction(
        markdown_path="test_data/sample.md",
        schema_name="patient_population"
    )
    assert result["success"] == True
    assert len(result["results"]) > 0

def test_batch_extraction():
    result = extraction_service.run_extraction(
        markdown_path="test_data/",
        schema_name="patient_population",
        max_documents=5
    )
    assert result["success"] == True
    assert result["total_documents"] == 5
```

### Integration Tests (Planned)

```python
# tests/test_extraction_tasks.py
import pytest
from app.workers.extraction_tasks import run_extraction

@pytest.mark.celery
def test_extraction_task():
    # Create test extraction in database
    extraction_id = create_test_extraction()
    job_id = create_test_job()

    # Run task
    result = run_extraction(extraction_id, job_id)

    assert result["status"] == "success"
    # Verify results in database
```

---

## 📈 Performance Tips

### Optimize for Speed

1. **Use faster models for simple extractions:**
   ```python
   DEFAULT_MODEL = "gemini/gemini-2.5-flash"
   ```

2. **Increase concurrency for batch processing:**
   ```python
   BATCH_CONCURRENCY = 10  # Process 10 documents in parallel
   ```

3. **Enable caching:**
   - DSPy caches LLM calls automatically
   - Caches stored in `.semantic_cache/` and `.evaluation_cache/`

### Optimize for Quality

1. **Use high-quality models:**
   ```python
   DEFAULT_MODEL = "gemini/gemini-3-pro-preview"
   # or
   DEFAULT_MODEL = "anthropic/claude-sonnet-4-5-20250929"
   ```

2. **Lower temperature for deterministic output:**
   ```python
   TEMPERATURE = 0.0  # More consistent results
   ```

3. **Enable field-level evaluation:**
   ```python
   result = await run_async_extraction_and_evaluation(
       ...,
       field_level_analysis=True,
       print_field_table=True
   )
   ```

---

## 🔗 Related Documentation

- **Celery Setup**: `CELERY_SETUP.md`
- **API Documentation**: http://localhost:8000/api/docs
- **Core Extraction**: `core/extractor.py`
- **Schema System**: `schemas/README.md` (if exists)
- **DSPy Guide**: `CLAUDE.md` (DSPy Best Practices section)

---

## ✅ Success Checklist

After setup, verify:

- [ ] Service imports work: `python test_extraction_service.py`
- [ ] DSPy is configured: Service status shows 'ready'
- [ ] Schemas are loaded: `list_schemas()` returns items
- [ ] Celery tasks registered: `run_extraction` appears in worker
- [ ] Redis is running: `redis-cli ping` → PONG
- [ ] Worker is running: See "celery@hostname ready" in logs
- [ ] Can run test extraction: Service returns results
- [ ] Results saved to database: Check `extraction_results` table

---

## 🎯 Next Steps

Once extraction service is working:

1. **Implement Extraction API** - Full CRUD for `/api/v1/extractions`
2. **Add Results API** - Endpoints to view/export results
3. **WebSocket Updates** - Real-time progress notifications
4. **Results Visualization** - Dashboard for extraction results
5. **Batch Operations** - Bulk extraction management

---

**Questions or Issues?** Check the Troubleshooting section above or refer to `CELERY_SETUP.md`.
