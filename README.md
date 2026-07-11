# eviStream — Backend

FastAPI backend for **eviStream**, an AI-powered platform for extracting structured data from research papers (systematic reviews, meta-analyses, evidence synthesis).

**Live:** https://evistreams.com · **Try it (no login):** https://evistreams.com/demo

---

## What it does

Users design an extraction schema ("form"); the platform compiles it into a runtime **DSPy** pipeline, runs it over uploaded PDFs, and supports a full double-review + adjudication workflow with reviewer blinding. Form *code generation* is driven by a **LangGraph** state machine with a human-in-the-loop review pause.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI (Python 3.11+) |
| Database | Supabase (PostgreSQL) — accessed via service-role key, scoping enforced in app code |
| Cache / Broker | Redis (port **6380**) |
| Task Queue | Celery — 3 queues: `pdf_processing`, `code_generation`, `extraction` |
| Auth | Custom HS256 JWT (python-jose) + bcrypt |
| Object Storage | AWS S3 |
| LLM orchestration | DSPy (runtime extraction) + LangGraph (form code generation) |
| Models | Anthropic / OpenAI / Gemini via Bedrock & native APIs |

---

## Getting Started

### Prerequisites
- Python 3.11+
- Redis on `localhost:6380`
- A Supabase project

### Setup
```bash
pip install -r requirements.txt
cp .env.example .env            # fill in the values below

bash start_backend.sh           # API on http://localhost:8001
bash start_workers.sh           # Celery workers (pdf / codegen / extraction)
```

- Health check: `http://localhost:8001/health`
- Swagger UI (when `DEBUG=true`): `http://localhost:8001/api/docs`
- ReDoc: `http://localhost:8001/api/redoc`

### Environment Variables
See `.env.example` for the full list. Key ones:
```env
SECRET_KEY=                 # openssl rand -hex 32
SUPABASE_URL=               # https://your-project.supabase.co
SUPABASE_KEY=               # anon key
SUPABASE_SERVICE_KEY=       # service-role key (admin ops)
REDIS_URL=redis://localhost:6380/0
AWS_ACCESS_KEY_ID=          # S3 + Bedrock
AWS_SECRET_ACCESS_KEY=
S3_BUCKET=
# LLM keys (at least one): ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY
BACKEND_CORS_ORIGINS=["http://localhost:3000"]
```
> Secrets are never committed — `.env` is git-ignored (only `.env.example` is tracked). In production, secrets are loaded via `utils/secrets_loader.py`.

---

## Project Structure

```
backend/
├── app/
│   ├── main.py                     # FastAPI app entry (uvicorn :8001)
│   ├── config.py                   # Pydantic settings
│   ├── dependencies.py             # get_current_user, require_admin, ...
│   ├── rate_limit.py               # slowapi limiter
│   ├── api/v1/                     # REST routes (see API Reference)
│   ├── models/                     # Pydantic schemas + enums
│   ├── services/                   # Business logic (auth, extraction, adjudication, blinding, ...)
│   └── workers/                    # Celery tasks (pdf / generation / extraction / watchdog)
│
├── core/generators/                # LangGraph form-code-generation workflow + prompts
├── dspy_components/                # Runtime DSPy signature/module builders
├── schemas/                        # StagedPipeline runtime + DynamicSchemaConfig
├── utils/                          # secrets_loader, caching_adapter, pilot_feedback, ...
├── migrations/                     # DB migration SQL
├── tests/                          # Test suite
├── deploy/                         # systemd units + nginx config + setup scripts
├── database_schema.sql             # Full DB schema reference
├── requirements.txt
└── start_backend.sh / start_workers.sh / stop_workers.sh
```

---

## API Reference (`/api/v1`)

| Area | Routes |
|---|---|
| Auth | `/auth` — register, login, refresh, me, forgot/reset-password, **`/auth/demo`** (zero-login demo session) |
| Projects | `/projects` (CRUD), `/projects/{id}/members`, `/project-invitations` |
| Documents | `/documents` — upload PDFs, processing status |
| Forms | `/forms` — create + LangGraph code generation, decomposition review |
| Extraction | `/extractions`, `/results`, `/jobs`, `/pilot` (calibration) |
| Review workflow | `/assignments`, `/adjudication`, `/qa`, `/vocabularies`, `/data-cleaning` |
| Ops / meta | `/dashboard`, `/activities`, `/notifications`, `/audit`, `/usage`, `/admin`, `/settings`, `/issues`, `/client-logs` |
| Realtime | `WS /ws/jobs/{job_id}` — live job log streaming (Redis pub/sub relay) |

All routes except the auth entry points require `Authorization: Bearer <token>`.

### Demo mode
`POST /api/v1/auth/demo` issues a session for a shared, sandboxed demo account (no credentials). The account is capped to a fixed number of projects, its seeded showcase projects are delete-protected, and it can only see its own data. Toggled via `DEMO_MODE_ENABLED` in config.

---

## Architecture

```
Next.js (:3000) ──► nginx ──► FastAPI (:8001)
                                 │
     ┌───────────────────────────┼───────────────────────────┐
     ▼                           ▼                            ▼
 Supabase (Postgres)        Redis (:6380)              Celery workers
  persistent data        cache + broker + pub/sub    ├─ pdf_processing
  (app-level scoping)                                 ├─ code_generation (LangGraph)
                                                       └─ extraction (DSPy StagedPipeline)
```

- **Form = schema + compiled DSPy program + completion ledger**, unified in one `forms` table.
- **Form generation** runs a LangGraph `StateGraph` (`decompose → validate → human_review → generate_signatures → finalize`) that pauses for human decomposition review.
- **Runtime extraction** is pure DSPy: signatures/modules are built at runtime from `schema_def` (no `.py` files on disk) and cached in a 3-tier cache (memory → Redis → Supabase).
- **Three HITL loops:** decomposition review, pilot calibration, and R1/R2 adjudication — with reviewer blinding enforced on all result reads/exports.

---

## Deployment

Production runs on a single host via **systemd**: `evistream-fastapi`, `evistream-nextjs`, and three Celery worker units (`evistream-worker-{pdf,codegen,extraction}`), fronted by **nginx** (`/api` → `:8001`, `/` → `:3000`, `/ws` → WebSocket). See `deploy/` for units, nginx config, and setup scripts.

---

## Development

```bash
python create_dev_user.py     # seed a local dev user
pytest tests/                 # run tests
```

Adding an endpoint: create the route in `app/api/v1/`, register it in `router.py`, put logic in `app/services/`, and gate it with `Depends(get_current_user)`.
