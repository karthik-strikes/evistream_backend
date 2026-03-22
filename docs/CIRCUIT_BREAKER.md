# Circuit Breaker — LLM Model Routing

Prevents cascading rate-limit failures by tracking per-model health and routing
requests away from degraded providers automatically. Replaces the old blind
retry-then-fallback approach with a stateful, memory-aware circuit.

---

## Why This Was Needed

### Old behavior (before circuit breaker)

```
Request arrives
  → Try Gemini
      → 429 rate limit returned by provider
      → LiteLLM internal retries: wait 5s... wait 10s... wait 15s...
      → ~30 seconds wasted before our code sees the error
      → Switch to GPT-4o (would have worked 30s ago)

Next request arrives 2 seconds later
  → Try Gemini again  ← no memory that it was just rate-limited
      → 429 again
      → Another 30 seconds wasted
```

**Two additional bugs existed alongside this:**

1. **Race condition**: `dspy.configure(lm=lm)` mutates global state. With 20 concurrent
   async coroutines, coroutine A sets Gemini, coroutine B immediately overrides with GPT-4o,
   and coroutine A silently runs against the wrong model. No error is thrown.

2. **Blanket fallback**: `except Exception` caught everything — including auth errors,
   context-window-too-long errors, and malformed response errors. The system wasted time
   trying all 4 models even when no fallback could possibly help.

### New behavior (with circuit breaker)

```
Request arrives
  → Check Gemini circuit breaker: OPEN (tripped 15s ago)
      → Skip Gemini instantly — no network call, no wait
      → Route to GPT-4o directly
      → Success, return result in milliseconds

After 60 seconds:
  → Circuit enters HALF_OPEN
  → One probe request sent to Gemini
  → Probe succeeds → CLOSED (fully recovered)
  → Probe fails   → OPEN again (reset 60s timer)
```

---

## State Machine

Each LLM provider has exactly one `ModelCircuitBreaker` instance with three states:

```
                   3 rate limits hit
    CLOSED ─────────────────────────────► OPEN
      ▲                                     │
      │                               60 seconds pass
      │                                     │
      │                                     ▼
      └──── 2 successful probes ───── HALF_OPEN
                                           │
                                     probe fails
                                           │
                                           ▼
                                         OPEN  (60s timer reset)
```

| State | Meaning | Requests Accepted? |
|---|---|---|
| `CLOSED` | Healthy — all requests flow through | Yes, all |
| `OPEN` | Tripped — provider is rate-limited | No — skipped instantly |
| `HALF_OPEN` | Recovering — testing if provider is back | Yes, one probe at a time |

### Transition rules

| Transition | Trigger |
|---|---|
| CLOSED → OPEN | `CB_FAILURE_THRESHOLD` consecutive 429 errors |
| OPEN → HALF_OPEN | `CB_RECOVERY_TIMEOUT` seconds have elapsed |
| HALF_OPEN → CLOSED | `CB_HALF_OPEN_SUCCESSES` successful probe requests |
| HALF_OPEN → OPEN | Probe request returns 429 (reset the recovery timer) |
| CLOSED: failure counter reset | Any successful request clears the count |

---

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────────┐
│  StagedPipeline._run_extractor_with_retry()                 │
│  (schemas/config.py)                                        │
│                                                             │
│    for each extraction attempt:                             │
│      router.run_with_routing(extractor, ...)                │
│         │                                                   │
│         ▼                                                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  ModelRouter  (utils/circuit_breaker.py)            │   │
│  │  process-level singleton                            │   │
│  │                                                     │   │
│  │  1. _get_ordered_candidates()                       │   │
│  │     CLOSED models first → HALF_OPEN → eligible OPEN │   │
│  │                                                     │   │
│  │  2. for each candidate model:                       │   │
│  │     with dspy.context(lm=model_lm):  ← no global   │   │
│  │         result = await extractor(...)               │   │
│  │     on success: cb.record_success()                 │   │
│  │     on 429:     cb.record_rate_limit_failure()      │   │
│  │                 try next model                      │   │
│  │     on other:   re-raise immediately                │   │
│  │                                                     │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐  │   │
│  │  │ Gemini   │ │ GPT-4o   │ │ Claude   │ │Flash  │  │   │
│  │  │   CB     │ │   CB     │ │   CB     │ │  CB   │  │   │
│  │  │ OPEN ✗   │ │ CLOSED ✓ │ │ CLOSED ✓ │ │CLOSED │  │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └───────┘  │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### File map

| File | Role |
|---|---|
| `utils/circuit_breaker.py` | `ModelCircuitBreaker`, `ModelRouter`, `AllModelsUnavailableError`, `_is_rate_limit_error()` |
| `core/config.py` | CB configuration constants (`CB_ENABLED`, `CB_FAILURE_THRESHOLD`, etc.) |
| `schemas/config.py` | Calls `router.run_with_routing()` inside `_run_extractor_with_retry()` |
| `utils/dspy_fallback.py` | Code generation path — also uses `dspy.context()` + rate-limit-only switching |

---

## The Race Condition Fix

### Problem

```python
# BEFORE — global state, unsafe for concurrent coroutines:
dspy.configure(lm=gemini_lm)   # Coroutine A sets Gemini

# Simultaneously, coroutine B runs:
dspy.configure(lm=gpt4_lm)    # Coroutine B overrides to GPT-4o

# Coroutine A now runs its extraction using GPT-4o — silent wrong model
```

### Fix

```python
# AFTER — contextvars, each coroutine is isolated:
with dspy.context(lm=gemini_lm):    # Only THIS coroutine uses Gemini
    result = await extractor(...)   # Coroutine B uses GPT-4o concurrently — no conflict
```

`dspy.context()` uses Python's `contextvars.ContextVar`. When `loop.run_in_executor()`
copies the current context into the thread, the LM override propagates correctly into
the executor thread. No global state is touched.

---

## Rate Limit Detection

`_is_rate_limit_error(exc)` (in `utils/circuit_breaker.py`) returns `True` **only** for
HTTP 429 / quota errors. Everything else returns `False` and causes an immediate re-raise.

Detection order:
1. `litellm.RateLimitError` — LiteLLM wraps all providers into this class
2. `exc.status_code == 429` — any HTTP exception with 429 attribute
3. String matching — fallback for edge cases: `"rate limit"`, `"429"`, `"quota exceeded"`, `"too many requests"`

**What is NOT a rate limit error** (fails fast, no fallback tried):
- Invalid API key / auth failure
- Context window too long for the model
- Malformed response / JSON parse error
- Network timeout
- Server 5xx errors

---

## The `num_retries=0` Setting

LiteLLM has internal retry logic that runs before our code sees the error. Without
disabling it, each 429 wastes ~30 seconds (5s + 10s + 15s backoff) before bubbling up.

```python
# In ModelRouter.__init__() — applied to every model's LM instance:
self._lm_cache[model] = dspy.LM(
    model,
    max_tokens=max_tokens,
    temperature=temperature,
    num_retries=0,   # ← Disables LiteLLM's internal retries
                     #   Our circuit breaker handles all retry/fallback logic
)
```

The same setting is applied in `utils/dspy_fallback.py` for the code generation path.

---

## Configuration

All knobs live in `core/config.py` and can be set via environment variables.

| Variable | Default | Description |
|---|---|---|
| `CB_ENABLED` | `True` | Set to `False` to bypass the circuit breaker entirely (debugging) |
| `CB_FAILURE_THRESHOLD` | `3` | 429 errors before a model's breaker trips to OPEN |
| `CB_RECOVERY_TIMEOUT` | `60` | Seconds in OPEN before testing recovery (HALF_OPEN probe) |
| `CB_HALF_OPEN_SUCCESSES` | `2` | Successful probes required to return to CLOSED |

### Tuning for different environments

**Development / fast iteration:**
```bash
CB_FAILURE_THRESHOLD=1    # Trip on first 429 — immediately test fallback path
CB_RECOVERY_TIMEOUT=10    # Only 10 seconds between recovery attempts
CB_HALF_OPEN_SUCCESSES=1  # One success is enough
```

**Production (conservative):**
```bash
CB_FAILURE_THRESHOLD=2    # Less tolerance — trip faster
CB_RECOVERY_TIMEOUT=120   # Wait 2 minutes before retrying a degraded model
CB_HALF_OPEN_SUCCESSES=3  # Need 3 successful probes before fully trusting model
```

**Disable circuit breaker (debugging only):**
```bash
CB_ENABLED=False
```

---

## Model Priority

The router always tries models in this priority order:

```
1. CLOSED models  →  in the declared order (Gemini, GPT-4o, Claude, Flash)
2. HALF_OPEN models  →  one probe allowed, in declared order
3. OPEN models whose recovery_timeout has elapsed  →  eligible for HALF_OPEN probe
4. OPEN models whose timeout has NOT elapsed  →  skipped entirely
```

Example — Gemini tripped 40 seconds ago (timeout=60s):

```
Candidates: [GPT-4o (CLOSED), Claude (CLOSED), Flash (CLOSED)]
             Gemini is OPEN and only 40s have passed — skipped

After 65 seconds:
Candidates: [GPT-4o (CLOSED), Claude (CLOSED), Flash (CLOSED), Gemini (eligible)]
             Gemini now gets one probe request
```

---

## What Happens When All Models Are Unavailable

If every model's circuit breaker is OPEN and no recovery timeout has elapsed:

1. `ModelRouter.run_with_routing()` raises `AllModelsUnavailableError`
2. `_run_extractor_with_retry()` in `schemas/config.py` catches it and returns `{}`
3. The pipeline treats `{}` the same as an all-NR extraction result
4. The retry loop inside `_run_extractor_with_retry` will attempt again on the next iteration
5. Eventually the pipeline returns whatever partial results it has

```python
# schemas/config.py — how AllModelsUnavailableError is handled:
try:
    router = ModelRouter.get_instance()
    result = await router.run_with_routing(
        async_callable=extractor,
        operation_name=f"Extractor:{sig_name}",
        markdown_content=markdown_content,
        **kwargs
    )
except AllModelsUnavailableError as e:
    logger.error(
        f"[StagedPipeline] {sig_name}: All models unavailable. "
        f"Returning empty result. CB states: {e.model_states}"
    )
    result = {}
```

---

## ModelRouter Singleton

`ModelRouter` is a process-level singleton created on first call to `ModelRouter.get_instance()`.
State (circuit breaker trip/recovery) persists for the lifetime of the process.

```python
router = ModelRouter.get_instance()
# All subsequent calls return the same instance — breaker state is preserved
```

**Important:** Each Celery worker process gets its own `ModelRouter` instance. If Gemini is
rate-limited and trips the breaker in worker A, worker B starts with a fresh CLOSED state.
This is intentional — each worker independently learns about provider health.

---

## Logs

Circuit breaker state changes are logged at clear levels:

```
# Normal operation
DEBUG: [ModelRouter] Extractor:ExtractPatientPopulation: using primary model=gemini/gemini-3-pro-preview

# Rate limit hit — counting toward threshold
WARNING: [CircuitBreaker] gemini/gemini-3-pro-preview: rate limit 1/3
WARNING: [CircuitBreaker] gemini/gemini-3-pro-preview: rate limit 2/3
WARNING: [CircuitBreaker] gemini/gemini-3-pro-preview: rate limit 3/3

# Breaker trips
ERROR: [CircuitBreaker] gemini/gemini-3-pro-preview: CLOSED → OPEN (tripped! will retry after 60s)

# Routing to fallback
WARNING: [ModelRouter] Extractor:ExtractPatientPopulation: routing to fallback model=openai/gpt-4o (primary=gemini/gemini-3-pro-preview state=OPEN)

# Recovery probe
INFO: [CircuitBreaker] gemini/gemini-3-pro-preview: OPEN → HALF_OPEN after 62.3s
DEBUG: [CircuitBreaker] gemini/gemini-3-pro-preview: probe success 1/2
DEBUG: [CircuitBreaker] gemini/gemini-3-pro-preview: probe success 2/2

# Fully recovered
INFO: [CircuitBreaker] gemini/gemini-3-pro-preview: HALF_OPEN → CLOSED (fully recovered)

# All models unavailable
ERROR: [ModelRouter] Extractor:ExtractIndexTest: ALL models exhausted with rate limits. States: {gemini=OPEN, gpt4o=OPEN, claude=OPEN, flash=OPEN}
```

---

## Pipeline Dependency Wiring (Related Fix)

Alongside the circuit breaker, a second bug was fixed in how multi-stage pipelines pass
data between stages. This is documented here because the fixes landed in the same files.

### Problem

Downstream pipeline stages (Stage 1+) declared `dspy.InputField` entries for upstream
outputs, but those fields were never reaching the DSPy call. DSPy emitted:

```
WARNING: Not all input fields were provided to module.
Present: ['markdown_content']
Missing: ['population_nonsuspicious_lesions', 'population_suspicious_lesions', 'population_healthy']
```

Two root causes:

1. **`schemas/config.py`** ignored `requires_fields` in `pipeline_stages` metadata.
   It passed ALL accumulated results to every stage, rather than filtering to only
   the fields that stage declared it needed.

2. **Generated `modules.py`** used `**kwargs` passthrough without explicitly mapping
   field names. If the upstream extractor returned `{"population": {...}}` but the
   downstream signature expected `population_nonsuspicious_lesions`, they never connected.

### Fix in `schemas/config.py`

```python
# Now filters stage kwargs to only required upstream fields:
requires_fields = stage_info.get("requires_fields", [])
if requires_fields:
    relevant_accumulated = {
        k: v for k, v in accumulated_results.items()
        if k in requires_fields
    }
    stage_kwargs = {**kwargs, **relevant_accumulated}
else:
    stage_kwargs = {**kwargs, **accumulated_results}  # backward-compatible
```

### Fix in `core/generators/module_gen.py`

Generated downstream modules now explicitly extract and remap each required field:

```python
# Generated code for a downstream module with requires_fields=["pop_a", "pop_b"]:
async def __call__(self, markdown_content: str, **kwargs):
    pop_a = kwargs.get("pop_a", "NR")
    pop_b = kwargs.get("pop_b", "NR")

    def _extract():
        return self.extract(
            markdown_content=markdown_content,
            pop_a=json.dumps(pop_a) if isinstance(pop_a, dict) else str(pop_a),
            pop_b=json.dumps(pop_b) if isinstance(pop_b, dict) else str(pop_b),
        )
    ...
```

`depends_on` from `decomposition.py` (already being computed) is now passed through
`workflow.py → generate_module() → generate_module_code()` as `requires_fields`.
Existing static hand-written tasks (which have no `requires_fields` in their stage config)
are unaffected — they continue to use the `**kwargs` passthrough path.

---

## Testing

### Unit test: breaker trips at threshold

```python
import asyncio
from utils.circuit_breaker import ModelCircuitBreaker, CircuitState

async def test_trips():
    cb = ModelCircuitBreaker("test-model", failure_threshold=3, recovery_timeout=60, half_open_successes=2)
    assert cb.state == CircuitState.CLOSED

    await cb.record_rate_limit_failure()  # 1/3
    await cb.record_rate_limit_failure()  # 2/3
    assert cb.state == CircuitState.CLOSED  # not yet

    await cb.record_rate_limit_failure()  # 3/3 → trips
    assert cb.state == CircuitState.OPEN   # ✓

asyncio.run(test_trips())
```

### Unit test: recovery cycle

```python
async def test_recovery():
    cb = ModelCircuitBreaker("test-model", failure_threshold=1, recovery_timeout=2, half_open_successes=2)
    await cb.record_rate_limit_failure()
    assert cb.state == CircuitState.OPEN

    await asyncio.sleep(3)  # Wait past recovery_timeout

    # Router transitions OPEN → HALF_OPEN on next routing attempt
    async with cb._lock:
        await cb.try_transition_to_half_open()
    assert cb.state == CircuitState.HALF_OPEN

    await cb.record_success()  # probe 1/2
    await cb.record_success()  # probe 2/2 → fully recovered
    assert cb.state == CircuitState.CLOSED  # ✓

asyncio.run(test_recovery())
```

### Unit test: non-rate-limit error does not trip breaker

```python
async def test_other_errors_ignored():
    cb = ModelCircuitBreaker("test-model", failure_threshold=1, recovery_timeout=60, half_open_successes=2)

    # Auth error — should NOT affect breaker state
    from utils.circuit_breaker import _is_rate_limit_error
    auth_error = Exception("Invalid API key: 401 Unauthorized")
    assert _is_rate_limit_error(auth_error) is False

    # Breaker remains CLOSED
    assert cb.state == CircuitState.CLOSED
```

### Manual integration test

```bash
# Set aggressive thresholds to see the breaker trip quickly
export CB_FAILURE_THRESHOLD=1
export CB_RECOVERY_TIMEOUT=10

python run.py single --schema index_test --source data/sample.json --target /tmp/out.json

# Expected log output:
# ERROR: [CircuitBreaker] gemini/gemini-3-pro-preview: CLOSED → OPEN (tripped! ...)
# WARNING: [ModelRouter] routing to fallback model=openai/gpt-4o
# Extraction completes using GPT-4o
```

### No-race-condition test

```python
# Confirm 20 concurrent extractions don't interfere with each other
import asyncio
from core.extractor import run_async_extraction

async def test_no_race():
    tasks = [run_async_extraction(markdown=sample_text) for _ in range(20)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [r for r in results if isinstance(r, RuntimeError)]
    assert len(errors) == 0, f"Race condition errors: {errors}"

asyncio.run(test_no_race())
```

---

## Health Check Endpoint

`ModelRouter.get_all_statuses()` returns the state of every circuit breaker.
Useful for surfacing model health in the `/health` API response.

```python
from utils.circuit_breaker import ModelRouter

router = ModelRouter.get_instance()
statuses = router.get_all_statuses()

# Example output:
[
    {
        "model": "gemini/gemini-3-pro-preview",
        "state": "OPEN",
        "failure_count": 3,
        "elapsed_since_open_seconds": 23.4,
        "time_until_recovery_seconds": 36.6,
    },
    {
        "model": "openai/gpt-4o",
        "state": "CLOSED",
        "failure_count": 0,
        "elapsed_since_open_seconds": None,
        "time_until_recovery_seconds": None,
    },
    ...
]
```

---

## Before vs After Summary

| Scenario | Before | After |
|---|---|---|
| Gemini returns 429 | Wait 30s (3 LiteLLM retries), then switch | Instant switch to GPT-4o in milliseconds |
| Same model rate-limited repeatedly | Retried on every request — no memory | OPEN state — skipped entirely until recovered |
| Recovery after rate limit window | Never automatic — stuck on fallback forever | HALF_OPEN probe after 60s → back to CLOSED |
| 20 concurrent extractions | Race condition on global `dspy.configure()` | Each coroutine isolated via `dspy.context()` |
| Auth error on GPT-4o | Wastefully tries Claude, Flash too | Fail fast — re-raises immediately |
| All models rate-limited | Hangs retrying indefinitely | Returns `{}` with clear error log |
| Downstream stage missing upstream fields | DSPy warning, empty extraction | Fields explicitly mapped and passed correctly |

---

## Code Reference

| File | Key Symbol | Purpose |
|---|---|---|
| `utils/circuit_breaker.py` | `ModelCircuitBreaker` | Per-model state machine |
| `utils/circuit_breaker.py` | `ModelRouter` | Singleton router, `run_with_routing()` |
| `utils/circuit_breaker.py` | `AllModelsUnavailableError` | Raised when all CBs are OPEN |
| `utils/circuit_breaker.py` | `_is_rate_limit_error()` | Detects 429 vs other errors |
| `core/config.py` | `CB_ENABLED`, `CB_FAILURE_THRESHOLD`, `CB_RECOVERY_TIMEOUT`, `CB_HALF_OPEN_SUCCESSES` | Tuning knobs |
| `schemas/config.py` | `StagedPipeline._run_extractor_with_retry()` | Integration point |
| `schemas/config.py` | `StagedPipeline.__call__()` | `requires_fields` filtering |
| `utils/dspy_fallback.py` | `call_dspy_with_fallback()` | Code gen path — also fixed |
| `core/generators/module_gen.py` | `generate_module_code()` | Downstream field remapping template |
