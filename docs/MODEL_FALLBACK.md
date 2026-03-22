# LLM Model Fallback

Automatic fallback when the primary LLM model fails. Covers API outages, rate limiting, quota exhaustion, and network errors.

---

## Model Hierarchy

**Extraction:**
1. `gemini/gemini-3-pro-preview` (primary)
2. `openai/gpt-4o`
3. `anthropic/claude-sonnet-4-5`
4. `gemini/gemini-2.0-flash-exp`

**Evaluation:**
1. `gemini/gemini-2.5-flash` (primary)
2. `openai/gpt-4o-mini`
3. `gemini/gemini-2.0-flash-exp`
4. `anthropic/claude-3-5-haiku`

**Flow:**
```
Try primary → success? return ✓
           → fail?    try fallback 1 → success? return ✓ + log warning
                                    → fail?    try fallback 2 → ...
                                                              → all failed? raise exception
```

---

## Configuration (`core/config.py`)

```python
DEFAULT_MODEL: str = "gemini/gemini-3-pro-preview"
EVALUATION_MODEL: str = "gemini/gemini-2.5-flash"
ENABLE_MODEL_FALLBACK: bool = True
LLM_TIMEOUT_SECONDS: int = 600

FALLBACK_MODELS: list = [
    "openai/gpt-4o",
    "anthropic/claude-sonnet-4-5",
    "gemini/gemini-2.0-flash-exp"
]
EVALUATION_FALLBACK_MODELS: list = [
    "openai/gpt-4o-mini",
    "gemini/gemini-2.0-flash-exp",
    "anthropic/claude-3-5-haiku"
]
```

**Required API keys in `.env`:**
```bash
GEMINI_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
```

---

## Usage

**Automatic** — existing code requires no changes. `get_dspy_model()` and `get_langchain_model()` in `utils/lm_config.py` have fallback built in.

**Manual DSPy call with fallback:**
```python
from utils.dspy_fallback import call_dspy_with_fallback

result = call_dspy_with_fallback(
    dspy_callable=dspy.ChainOfThought(MySignature),
    markdown_content="...",
    operation_name="Extract patient data"
)
```

**Async:**
```python
from utils.dspy_fallback import async_call_dspy_with_fallback

result = await async_call_dspy_with_fallback(
    dspy_callable=my_async_module,
    markdown_content="...",
    operation_name="Async extraction"
)
```

**Evaluation:**
```python
from utils.dspy_fallback import call_evaluation_with_fallback

result = call_evaluation_with_fallback(
    evaluator_callable=my_evaluator,
    extracted="...",
    ground_truth="..."
)
```

**Custom models:**
```python
result = call_dspy_with_fallback(
    dspy_callable=my_sig,
    primary_model="anthropic/claude-sonnet-4-5",
    fallback_models=["openai/gpt-4o", "gemini/gemini-3-pro-preview"],
    ...
)
```

**Disable for a specific call:**
```python
result = call_dspy_with_fallback(
    dspy_callable=my_sig,
    enable_fallback=False,
    ...
)
```

---

## Where Fallback is Active

| Component | File | Notes |
|---|---|---|
| DSPy model init | `utils/lm_config.py` → `get_dspy_model()` | All DSPy extractions |
| LangChain model init | `utils/lm_config.py` → `get_langchain_model()` | Code generation (LangGraph) |
| Extraction service | `backend/app/services/extraction_service.py` | Via `get_dspy_model()` |
| Evaluation | `core/evaluation.py` | Via `call_evaluation_with_fallback()` |
| Celery workers | `backend/app/workers/` | Via services above |

---

## Logs

```
# Primary succeeds
INFO: DSPy extraction: Attempting with model gemini/gemini-3-pro-preview (1/4)

# Fallback triggered
ERROR: DSPy extraction: Failed with model gemini/gemini-3-pro-preview: API rate limit exceeded
INFO:  DSPy extraction: Trying fallback model...
INFO:  DSPy extraction: Attempting with model openai/gpt-4o (2/4)
WARN:  DSPy extraction: Succeeded with fallback model openai/gpt-4o after 1 failures

# All failed
ERROR: DSPy extraction: All 3 models failed
```

LLM history including model used and token counts is saved to `outputs/logs/dspy_history.csv`.

---

## Monitoring

| Metric | Alert Threshold |
|---|---|
| Fallback rate | > 10% — primary model likely degraded |
| Single model failure rate | > 50% |
| All models failing | Immediate — critical |
| Week-over-week cost increase | > 20% |

---

## Troubleshooting

| Problem | Check |
|---|---|
| Fallback not triggering | `ENABLE_MODEL_FALLBACK=True` in config; API keys set for fallback models |
| All models failing | Network connectivity; API key validity; provider quota |
| High fallback rate | Provider status page; rate limit settings; primary model config |

---

## Code Reference

| File | Purpose |
|---|---|
| `core/config.py` | `FALLBACK_MODELS`, `EVALUATION_FALLBACK_MODELS`, `ENABLE_MODEL_FALLBACK` |
| `utils/lm_config.py` | `get_dspy_model()`, `get_langchain_model()` with fallback |
| `utils/dspy_fallback.py` | `call_dspy_with_fallback()`, `async_call_dspy_with_fallback()`, `call_evaluation_with_fallback()` |
