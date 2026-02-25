# eviStream Backend

FastAPI backend for the eviStream AI-powered medical data extraction platform.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI |
| Language | Python 3.11+ |
| Database | Supabase (PostgreSQL) |
| Cache / Broker | Redis |
| Task Queue | Celery |
| Auth | JWT (python-jose) |
| File Storage | AWS S3 |
| LLM | Gemini / OpenAI / Anthropic via DSPy |

---

## Getting Started

### Prerequisites

- Python 3.11+
- Redis running on `localhost:6379`
- Supabase project

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env
# Fill in values (see Environment Variables section)

# Start the API server
bash start_backend.sh
```

API available at `http://localhost:8000`
- Swagger UI: `http://localhost:8000/api/docs`
- ReDoc: `http://localhost:8000/api/redoc`
- Health check: `http://localhost:8000/health`

### Start Workers

```bash
# All workers in one terminal
bash start_workers.sh

# Or each worker in a separate terminal
bash start_workers_separate_terminals.sh

# Stop all workers
bash stop_workers.sh
```

---

## Environment Variables

```env
# Application
DEBUG=true
ENVIRONMENT=development

# Security
SECRET_KEY=                        # openssl rand -hex 32

# Database
SUPABASE_URL=                      # https://your-project.supabase.co
SUPABASE_KEY=                      # anon key
SUPABASE_SERVICE_KEY=              # service role key

# Redis
REDIS_URL=redis://localhost:6379/0

# AWS S3
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1
S3_BUCKET=evistream-production

# CORS
BACKEND_CORS_ORIGINS=["http://localhost:3000"]

# LLM Keys (at least one required)
GEMINI_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
```

---

## Project Structure

```
backend/
├── app/
│   ├── main.py                     # FastAPI app entry point
│   ├── config.py                   # Pydantic settings
│   ├── database.py                 # Supabase client
│   ├── dependencies.py             # Shared FastAPI dependencies
│   ├── rate_limits.py              # Rate limiting rules
│   │
│   ├── api/v1/                     # REST API routes
│   │   ├── router.py               # Route aggregator
│   │   ├── auth.py                 # /auth — register, login, me
│   │   ├── projects.py             # /projects — CRUD
│   │   ├── documents.py            # /documents — upload, list, status
│   │   ├── forms.py                # /forms — create, generate DSPy code
│   │   ├── extractions.py          # /extractions — run jobs
│   │   ├── results.py              # /results — view, export
│   │   ├── jobs.py                 # /jobs — monitor async jobs
│   │   ├── activities.py           # /activities — activity feed
│   │   ├── notifications.py        # /notifications — user notifications
│   │   └── websocket.py            # /ws — real-time job log streaming
│   │
│   ├── models/
│   │   ├── schemas.py              # Pydantic request/response models
│   │   └── enums.py                # Shared enums (JobStatus, etc.)
│   │
│   ├── services/                   # Business logic layer
│   │   ├── auth_service.py         # JWT auth, password hashing
│   │   ├── extraction_service.py   # Orchestrates extraction pipeline
│   │   ├── code_generation_service.py  # Wraps core/generators workflow
│   │   ├── pdf_processing_service.py   # PDF → markdown conversion
│   │   ├── storage_service.py      # S3 upload/download
│   │   ├── cache_service.py        # Redis caching helpers
│   │   ├── activity_service.py     # Activity feed writes
│   │   └── notification_service.py # User notification delivery
│   │
│   ├── workers/                    # Celery async tasks
│   │   ├── celery_app.py           # Celery app + queue config
│   │   ├── extraction_tasks.py     # Run extraction pipelines
│   │   ├── generation_tasks.py     # Run DSPy code generation
│   │   ├── pdf_tasks.py            # Process uploaded PDFs
│   │   ├── log_broadcaster.py      # Stream logs over WebSocket
│   │   └── watchdog_tasks.py       # Job timeout / cleanup
│   │
│   └── [core, dspy_components, schemas, utils]  # Symlinks to root packages
│
├── migrations/                     # DB migration SQL files
├── tests/                          # Test suite
├── docs/                           # API documentation
├── logs/                           # Runtime logs
├── output/                         # Generated outputs
│
├── database_schema.sql             # Full DB schema reference
├── PRIORITY1_MIGRATION.sql         # Migration patches
├── create_dev_user.py              # Seed a dev user
├── requirements.txt
├── start_backend.sh
├── start_workers.sh
├── start_workers_separate_terminals.sh
└── stop_workers.sh
```

---

## API Reference

| Method | Route | Description |
|---|---|---|
| POST | `/api/v1/auth/register` | Register new user |
| POST | `/api/v1/auth/login` | Login, returns JWT |
| GET | `/api/v1/auth/me` | Current user info |
| GET/POST | `/api/v1/projects` | List / create projects |
| GET/PUT/DELETE | `/api/v1/projects/{id}` | Project detail |
| GET/POST | `/api/v1/documents` | List / upload PDFs |
| GET/POST | `/api/v1/forms` | List / create forms |
| POST | `/api/v1/forms/{id}/generate` | Trigger DSPy code generation |
| POST | `/api/v1/extractions` | Start extraction job |
| GET | `/api/v1/extractions/{id}` | Job status |
| GET | `/api/v1/results` | Browse results |
| GET | `/api/v1/jobs` | All async jobs |
| GET | `/api/v1/activities` | Activity feed |
| GET | `/api/v1/notifications` | User notifications |
| WS | `/api/v1/ws/jobs/{job_id}` | Real-time log streaming |

All routes except `/auth/register` and `/auth/login` require `Authorization: Bearer <token>`.

---

## Architecture

```
Frontend (Next.js :3000)
        │
        ▼
FastAPI (:8000)
  ├── Auth middleware (JWT)
  ├── Rate limiting
  ├── API routes → Services
  └── WebSocket (job logs)
        │
        ├── Supabase (PostgreSQL) — persistent data
        ├── Redis — cache + Celery broker
        └── Celery Workers
              ├── PDF processing (pdf_tasks)
              ├── DSPy code generation (generation_tasks)
              ├── Extraction pipeline (extraction_tasks)
              └── Log broadcaster (WebSocket relay)
```

---

## Development

### Create a dev user

```bash
python create_dev_user.py
```

### Run tests

```bash
pytest tests/
```

### Database schema

Full schema reference in `database_schema.sql`. Apply migrations with `migrations/`.

### Adding a new endpoint

1. Create route file in `app/api/v1/`
2. Register router in `app/api/v1/router.py`
3. Add service logic in `app/services/`
4. Use `Depends(get_current_user)` for protected routes
