# Celery Setup Guide for Background PDF Processing

This guide explains how to set up and test Celery workers for background PDF processing.

---

## 🎯 What We Just Implemented

### ✅ Components Created:

1. **Celery Configuration** (`app/workers/celery_app.py`)
   - Redis broker setup
   - Task queues (pdf_processing, extraction, code_generation)
   - Task time limits

2. **PDF Processing Service** (`app/services/pdf_processing_service.py`)
   - Wraps existing `pdf_processor/pdf_processor.py`
   - Converts PDF to Markdown
   - Returns processing results

3. **Celery Task** (`app/workers/pdf_tasks.py`)
   - `process_pdf_document` - Background PDF processing
   - Updates document status in database
   - Tracks job progress

4. **Updated Document Upload**
   - Creates job record in database
   - Triggers Celery task automatically
   - Returns job_id for tracking

---

## 🚀 How It Works Now

### **Before (What We Had):**
```
1. Upload PDF → Saved to disk
2. Status: "pending" (forever)
3. No conversion to markdown
```

### **After (What We Have Now):**
```
1. Upload PDF → Saved to disk
2. Create job record (status: "pending")
3. Trigger Celery task → Background processing starts
4. Status updates: "pending" → "processing" → "completed"
5. Markdown file saved
6. Database updated with markdown path
```

---

## 📋 Prerequisites

### **1. Check if Redis is Installed**

```bash
redis-cli --version
```

**If not installed:**
```bash
# For Ubuntu/Debian
sudo apt-get update
sudo apt-get install redis-server

# For macOS
brew install redis

# For Conda (if you prefer)
conda install -c conda-forge redis
```

### **2. Install Redis Python Client** (if not already installed)

```bash
conda activate topics
pip install redis celery
```

---

## 🏃 Starting the System

You need **3 terminals** running simultaneously:

### **Terminal 1: Redis Server**

```bash
# Start Redis
redis-server

# You should see:
# Ready to accept connections
```

**Test Redis is working:**
```bash
redis-cli ping
# Should return: PONG
```

---

### **Terminal 2: FastAPI Backend**

```bash
conda activate topics
cd /mnt/nlpgridio3/data/karthik9/Sprint1/Dental/eviStream/backend
python -m app.main
```

**Should see:**
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete.
```

---

### **Terminal 3: Celery Worker**

```bash
conda activate topics
cd /mnt/nlpgridio3/data/karthik9/Sprint1/Dental/eviStream/backend

# Start Celery worker
celery -A app.workers.celery_app worker --loglevel=info
```

**Should see:**
```
-------------- celery@hostname v5.3.6 (emerald-rush)
--- ***** -----
-- ******* ---- Linux-x.x.x-x
- *** --- * ---
- ** ---------- [config]
- ** ---------- .> app:         evistream_workers
- ** ---------- .> transport:   redis://localhost:6379/0
- ** ---------- .> results:     redis://localhost:6379/0
- *** --- * --- .> concurrency: 8 (prefork)
-- ******* ---- .> task events: OFF
--- ***** -----
 -------------- [queues]
                .> pdf_processing   exchange=pdf_processing(direct)
                .> extraction       exchange=extraction(direct)
                .> code_generation  exchange=code_generation(direct)

[tasks]
  . app.workers.pdf_tasks.process_pdf_document
  . app.workers.pdf_tasks.check_pdf_processor_health
  . app.workers.extraction_tasks.run_extraction
  . app.workers.generation_tasks.generate_form_code

[2026-01-22 20:45:00,000: INFO/MainProcess] Connected to redis://localhost:6379/0
[2026-01-22 20:45:00,000: INFO/MainProcess] mingle: searching for neighbors
[2026-01-22 20:45:01,000: INFO/MainProcess] mingle: all alone
[2026-01-22 20:45:01,000: INFO/MainProcess] celery@hostname ready.
```

---

## 🧪 Testing the Complete Workflow

### **Step 1: Upload a PDF**

Go to Swagger UI: http://localhost:8000/api/docs

**POST** `/api/v1/documents/upload`
- project_id: (your project ID)
- file: Select a PDF

**Expected Response:**
```json
{
  "id": "document-uuid",
  "filename": "test.pdf",
  "unique_filename": "generated-uuid.pdf",
  "project_id": "project-uuid",
  "job_id": "real-job-uuid",  ← NOT all zeros anymore!
  "status": "pending"
}
```

---

### **Step 2: Watch Celery Worker Terminal**

You should see logs like:
```
[2026-01-22 20:46:00,000: INFO/MainProcess] Task process_pdf_document received
[2026-01-22 20:46:00,001: INFO/ForkPoolWorker-1] Starting PDF processing for document xxx
[2026-01-22 20:46:00,002: INFO/ForkPoolWorker-1] Processing PDF at: /path/to/file.pdf
[2026-01-22 20:46:05,000: INFO/ForkPoolWorker-1] PDF processing successful for xxx
[2026-01-22 20:46:05,001: INFO/MainProcess] Task process_pdf_document succeeded
```

---

### **Step 3: Check Document Status**

**GET** `/api/v1/documents/{document_id}`

**Watch the status change:**
```json
{
  "processing_status": "pending"      ← Initially
  "processing_status": "processing"   ← During processing
  "processing_status": "completed"    ← After done!
  "s3_markdown_path": "/path/to/file.md"  ← Markdown file!
}
```

---

### **Step 4: Check Job Status**

**GET** `/api/v1/jobs/{job_id}` (endpoint to be created, or check Supabase)

In Supabase → Table Editor → `jobs` table:

You should see:
- `status`: "completed"
- `progress`: 100
- `celery_task_id`: (Celery task ID)
- `result_data`: (markdown path, metadata)

---

### **Step 5: Verify Markdown File**

```bash
ls -lh /mnt/nlpgridio3/data/karthik9/Sprint1/Dental/eviStream/storage/processed/extracted_pdfs/
```

You should see your markdown file!

```bash
cat /path/to/your/file.md
```

View the converted markdown content!

---

## 🔍 Monitoring & Debugging

### **Check Redis Connection:**
```bash
redis-cli
> KEYS *
> GET celery-task-meta-<task-id>
> EXIT
```

### **Check Celery Tasks:**
```bash
# In Python
from app.workers.celery_app import celery_app
from app.workers.pdf_tasks import process_pdf_document

# Check registered tasks
print(celery_app.tasks.keys())

# Test health check
result = check_pdf_processor_health.delay()
print(result.get())
```

### **Check Logs:**
- **Celery Worker**: Terminal 3 output
- **FastAPI**: Terminal 2 output
- **Redis**: Terminal 1 output

---

## 🐛 Troubleshooting

### **Issue: Celery worker not starting**

**Error:** `ModuleNotFoundError: No module named 'celery'`

**Fix:**
```bash
conda activate topics
pip install celery redis
```

---

### **Issue: Redis connection refused**

**Error:** `Error connecting to Redis: Connection refused`

**Fix:**
1. Make sure Redis is running: `redis-cli ping`
2. Check Redis is on default port: `redis-cli -p 6379 ping`
3. Start Redis if not running: `redis-server`

---

### **Issue: PDF processor not found**

**Error:** `ModuleNotFoundError: No module named 'pdf_processor'`

**Fix:** The pdf_processor should be in your project root. Check:
```bash
ls /mnt/nlpgridio3/data/karthik9/Sprint1/Dental/eviStream/pdf_processor/
```

---

### **Issue: Task stays "pending" forever**

**Possible causes:**
1. Celery worker not running → Start Terminal 3
2. Redis not running → Start Terminal 1
3. Wrong Redis URL in .env → Check CELERY_BROKER_URL

**Check:**
```bash
# Is worker running?
celery -A app.workers.celery_app inspect active

# Are tasks registered?
celery -A app.workers.celery_app inspect registered
```

---

### **Issue: PDF processing fails**

**Check Celery worker terminal for error messages.**

Common issues:
- PDF file not found → Check path in database
- API key missing → Check DATALAB_API_KEY in .env
- Permission error → Check file permissions

---

## 📊 System Architecture

```
┌─────────────┐
│   Browser   │
└──────┬──────┘
       │ 1. Upload PDF
       ▼
┌─────────────────┐
│  FastAPI        │
│  (Terminal 2)   │
│                 │
│  - Save PDF     │
│  - Create job   │
│  - Trigger task │
└────┬────────────┘
     │ 2. Queue task
     ▼
┌─────────────────┐
│  Redis Broker   │
│  (Terminal 1)   │
└────┬────────────┘
     │ 3. Deliver task
     ▼
┌─────────────────┐
│  Celery Worker  │
│  (Terminal 3)   │
│                 │
│  - Process PDF  │
│  - Convert to MD│
│  - Update DB    │
└─────────────────┘
```

---

## ✅ Success Checklist

After setup, verify:

- [ ] Redis is running (`redis-cli ping` → PONG)
- [ ] FastAPI server is running (http://localhost:8000/health)
- [ ] Celery worker is running (see "celery@hostname ready" in Terminal 3)
- [ ] Upload PDF → Gets job_id (not all zeros)
- [ ] Celery terminal shows task received
- [ ] Document status changes to "processing"
- [ ] Document status changes to "completed"
- [ ] Markdown file exists in `storage/processed/extracted_pdfs/`
- [ ] Job record in Supabase shows "completed"

---

## 🎯 Next Steps

Once PDF processing is working:

1. **Add Jobs API** - Endpoints to check job status
2. **WebSocket Updates** - Real-time progress notifications
3. **Forms API** - Create extraction forms
4. **Extraction Workers** - Run extractions on markdown
5. **Results API** - View extraction results

---

## 🚀 Quick Start Commands

**Start everything in 3 terminals:**

```bash
# Terminal 1 - Redis
redis-server

# Terminal 2 - FastAPI
conda activate topics && cd backend && python -m app.main

# Terminal 3 - Celery
conda activate topics && cd backend && celery -A app.workers.celery_app worker --loglevel=info
```

**Test upload:**
- Go to http://localhost:8000/api/docs
- POST /documents/upload with a PDF
- Watch Terminal 3 for processing logs
- Check document status changes

---

**Questions or Issues?** Check the Troubleshooting section above!
