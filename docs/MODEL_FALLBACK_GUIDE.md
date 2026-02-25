# Model Fallback Feature - Complete Guide

**Status:** ✅ Implemented and Production-Ready
**Date:** 2026-01-23

---

## 🎯 Overview

The eviStream backend now includes **automatic model fallback** - a production-grade feature that automatically switches to alternative LLM models when the primary model fails. This ensures high availability and resilience against:

- Model API outages
- Rate limiting
- Quota exhaustion
- Network issues
- Provider-specific failures

---

## 🏗️ Architecture

### Model Hierarchy

**Primary Models:**
- **Extraction**: `gemini/gemini-3-pro-preview`
- **Evaluation**: `gemini/gemini-2.5-flash`

**Fallback Chain (Extraction):**
1. `gemini/gemini-3-pro-preview` (Primary)
2. `openai/gpt-4o` (Fallback 1)
3. `anthropic/claude-sonnet-4-5` (Fallback 2)
4. `gemini/gemini-2.0-flash-exp` (Fallback 3)

**Fallback Chain (Evaluation):**
1. `gemini/gemini-2.5-flash` (Primary)
2. `openai/gpt-4o-mini` (Fallback 1)
3. `gemini/gemini-2.0-flash-exp` (Fallback 2)
4. `anthropic/claude-3-5-haiku` (Fallback 3)

### How It Works

```
User Request
    ↓
Try Primary Model (Gemini)
    ↓
Success? → Return Result ✓
    ↓
Failure? → Try Fallback 1 (GPT-4o)
    ↓
Success? → Return Result ✓ + Log Warning
    ↓
Failure? → Try Fallback 2 (Claude)
    ↓
Success? → Return Result ✓ + Log Warning
    ↓
All Failed? → Raise Exception ✗
```

---

## 📋 Configuration

### Environment Variables (.env)

Make sure you have API keys for all fallback models:

```bash
# Primary Model (Gemini)
GEMINI_API_KEY=your_gemini_api_key

# Fallback Models
OPENAI_API_KEY=your_openai_api_key
ANTHROPIC_API_KEY=your_anthropic_api_key
```

### Core Configuration (core/config.py)

**Enable/Disable Fallback:**
```python
ENABLE_MODEL_FALLBACK: bool = True  # Set to False to disable
```

**Customize Fallback Models:**
```python
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

**Other Settings:**
```python
DEFAULT_MODEL: str = "gemini/gemini-3-pro-preview"  # Primary model
EVALUATION_MODEL: str = "gemini/gemini-2.5-flash"   # Primary eval model
MAX_TOKENS: int = 20000
TEMPERATURE: float = 1.0
LLM_TIMEOUT_SECONDS: int = 600  # 10 minutes
```

---

## 🔧 Usage

### 1. Automatic Fallback (Recommended)

**All existing code automatically uses fallback** - no changes needed!

```python
# Existing code continues to work with automatic fallback
from utils.lm_config import get_dspy_model

# This now has automatic fallback built-in
lm = get_dspy_model()
```

### 2. DSPy Extraction with Fallback

```python
from utils.dspy_fallback import call_dspy_with_fallback
import dspy

# Define your DSPy signature
class ExtractPatientData(dspy.Signature):
    """Extract patient population data from medical research paper."""
    markdown_content: str = dspy.InputField()
    patient_data_json: str = dspy.OutputField()

# Call with automatic fallback
result = call_dspy_with_fallback(
    dspy_callable=dspy.ChainOfThought(ExtractPatientData),
    markdown_content="...",
    operation_name="Extract patient data"
)
```

### 3. Async DSPy with Fallback

```python
from utils.dspy_fallback import async_call_dspy_with_fallback

# Async extraction module
async def extract_data():
    result = await async_call_dspy_with_fallback(
        dspy_callable=my_async_module,
        markdown_content="...",
        operation_name="Async extraction"
    )
    return result
```

### 4. Evaluation with Fallback

```python
from utils.dspy_fallback import call_evaluation_with_fallback

# Automatically uses EVALUATION_MODEL and EVALUATION_FALLBACK_MODELS
result = call_evaluation_with_fallback(
    evaluator_callable=my_evaluator,
    extracted="...",
    ground_truth="..."
)
```

### 5. Custom Fallback Models

```python
from utils.dspy_fallback import call_dspy_with_fallback

result = call_dspy_with_fallback(
    dspy_callable=my_signature,
    primary_model="anthropic/claude-sonnet-4-5",
    fallback_models=["openai/gpt-4o", "gemini/gemini-3-pro-preview"],
    markdown_content="...",
    operation_name="Custom extraction"
)
```

### 6. Disable Fallback for Specific Calls

```python
from utils.dspy_fallback import call_dspy_with_fallback

# Only use primary model (no fallback)
result = call_dspy_with_fallback(
    dspy_callable=my_signature,
    enable_fallback=False,
    markdown_content="...",
    operation_name="No fallback extraction"
)
```

---

## 📊 Logging

### Log Messages

**Successful Primary Model:**
```
INFO: DSPy extraction: Attempting with model gemini/gemini-3-pro-preview (1/4)
```

**Fallback Triggered:**
```
ERROR: DSPy extraction: Failed with model gemini/gemini-3-pro-preview: API rate limit exceeded
INFO: DSPy extraction: Trying fallback model...
INFO: DSPy extraction: Attempting with model openai/gpt-4o (2/4)
WARNING: DSPy extraction: Succeeded with fallback model openai/gpt-4o after 1 failures
```

**All Models Failed:**
```
ERROR: DSPy extraction: Failed with model gemini/gemini-3-pro-preview: API rate limit
ERROR: DSPy extraction: Failed with model openai/gpt-4o: Network timeout
ERROR: DSPy extraction: Failed with model anthropic/claude-sonnet-4-5: API error
ERROR: DSPy extraction: All 3 models failed
Exception: DSPy extraction failed with all 3 models. Last error: API error
```

### Check Logs

Fallback events are logged in:
- Console output (when running backend)
- Application logs (if configured)
- LLM history CSV (`outputs/logs/dspy_history.csv`)

---

## 🧪 Testing

### Test Fallback Manually

Create a test script:

```python
# test_model_fallback.py
import os
from utils.dspy_fallback import call_dspy_with_fallback
import dspy

# Define a simple signature
class TestSignature(dspy.Signature):
    """Test signature for fallback."""
    text: str = dspy.InputField()
    summary: str = dspy.OutputField()

# Test 1: Normal operation (should use primary)
print("Test 1: Normal operation")
result = call_dspy_with_fallback(
    dspy_callable=dspy.ChainOfThought(TestSignature),
    text="This is a test.",
    operation_name="Test 1"
)
print(f"Result: {result.summary}\n")

# Test 2: Simulate primary failure by using invalid model
print("Test 2: Fallback on failure")
try:
    result = call_dspy_with_fallback(
        dspy_callable=dspy.ChainOfThought(TestSignature),
        primary_model="invalid/model-name",
        fallback_models=["gemini/gemini-2.5-flash"],
        text="This is a test.",
        operation_name="Test 2"
    )
    print(f"Result: {result.summary}")
    print("✓ Fallback worked!\n")
except Exception as e:
    print(f"✗ Error: {e}\n")

# Test 3: Disable fallback
print("Test 3: Fallback disabled")
try:
    result = call_dspy_with_fallback(
        dspy_callable=dspy.ChainOfThought(TestSignature),
        enable_fallback=False,
        text="This is a test.",
        operation_name="Test 3"
    )
    print(f"Result: {result.summary}\n")
except Exception as e:
    print(f"Expected behavior: {e}\n")
```

Run the test:
```bash
cd /mnt/nlpgridio3/data/karthik9/Sprint1/Dental/eviStream
conda activate topics
python test_model_fallback.py
```

---

## 💰 Cost Implications

### Cost Tracking

All model usage is tracked in `outputs/logs/dspy_history.csv` including:
- Model used (primary or fallback)
- Token counts
- Estimated cost
- Timestamp

### Cost Optimization

**Best Practices:**
1. Set reasonable timeouts to fail fast
2. Use cheaper evaluation models (e.g., flash models)
3. Monitor fallback frequency - frequent fallbacks indicate issues
4. Consider quotas and rate limits

**Fallback Model Costs (Approximate):**
- `gemini/gemini-3-pro-preview`: Medium
- `openai/gpt-4o`: Higher
- `anthropic/claude-sonnet-4-5`: Higher
- `gemini/gemini-2.5-flash`: Lower (evaluation)

---

## ⚙️ Integration Points

### Where Fallback is Active

1. **DSPy Model Initialization** (`utils/lm_config.py`)
   - `get_dspy_model()` - Has fallback
   - `get_langchain_model()` - Has fallback

2. **Extraction Service** (`backend/app/services/extraction_service.py`)
   - Uses `get_dspy_model()` which has fallback

3. **Code Generation** (`core/generators/`)
   - LangGraph workflow uses `get_langchain_model()` with fallback

4. **Evaluation** (`core/evaluation.py`)
   - Can use `call_evaluation_with_fallback()` wrapper

5. **Celery Workers** (`backend/app/workers/`)
   - All workers use services with fallback support

---

## 🔍 Monitoring

### Key Metrics to Monitor

1. **Fallback Rate**: % of requests using fallback models
2. **Model Success Rate**: % of requests succeeding per model
3. **Average Latency**: Time to complete with fallback
4. **Cost per Request**: Track cost increases from fallback usage

### Alerting Recommendations

Set up alerts for:
- Fallback rate > 10% (indicates primary model issues)
- Any model failure rate > 50%
- Total cost increase > 20% week-over-week
- All models failing (critical)

---

## 🐛 Troubleshooting

### Fallback Not Working

**Check:**
1. `ENABLE_MODEL_FALLBACK=True` in config
2. API keys configured for all fallback models
3. Check logs for error messages
4. Verify fallback model names are correct

### All Models Failing

**Possible Causes:**
1. Network connectivity issues
2. All API keys invalid/expired
3. Quota exhausted across all providers
4. Invalid request format (would fail with all models)

**Fix:**
1. Check internet connection
2. Verify API keys in `.env`
3. Check quota limits on provider dashboards
4. Review request parameters

### High Fallback Rate

**Possible Causes:**
1. Primary model experiencing outages
2. Rate limits on primary model
3. Primary model configuration issue

**Fix:**
1. Check provider status page
2. Increase rate limits or use multiple API keys
3. Review timeout and retry settings

---

## 📝 Best Practices

1. **Always have valid API keys** for all fallback models
2. **Monitor fallback frequency** - should be < 5% normally
3. **Test fallback regularly** with manual tests
4. **Set appropriate timeouts** to fail fast (default: 600s)
5. **Use evaluation models** for non-critical tasks (cheaper)
6. **Log all fallback events** for debugging
7. **Review cost reports** weekly to catch anomalies

---

## 🚀 Production Deployment

### Pre-Deployment Checklist

- [ ] API keys configured for all models
- [ ] `ENABLE_MODEL_FALLBACK=True` in production `.env`
- [ ] Fallback models tested and working
- [ ] Monitoring/alerting set up
- [ ] Cost tracking configured
- [ ] Log aggregation working
- [ ] Timeout values appropriate for production

### Environment-Specific Settings

**Development:**
```bash
ENABLE_MODEL_FALLBACK=True
FALLBACK_MODELS=["openai/gpt-4o-mini", "gemini/gemini-2.5-flash"]  # Cheaper
```

**Staging:**
```bash
ENABLE_MODEL_FALLBACK=True
FALLBACK_MODELS=["openai/gpt-4o", "anthropic/claude-sonnet-4-5"]  # Production models
```

**Production:**
```bash
ENABLE_MODEL_FALLBACK=True
FALLBACK_MODELS=["openai/gpt-4o", "anthropic/claude-sonnet-4-5", "gemini/gemini-2.0-flash-exp"]
LLM_TIMEOUT_SECONDS=600
```

---

## 📚 Code Reference

**Configuration:** `core/config.py` lines 55-80
**LM Config:** `utils/lm_config.py`
**DSPy Fallback:** `utils/dspy_fallback.py`
**Extraction Service:** `backend/app/services/extraction_service.py`

---

## ✅ Summary

**Model fallback is now production-ready with:**

✓ **Automatic fallback** across 3-4 models per use case
✓ **Configurable** via environment variables
✓ **Comprehensive logging** of all fallback events
✓ **Cost tracking** for all model usage
✓ **Async support** for concurrent extractions
✓ **Easy to test** and monitor
✓ **Zero code changes** needed for existing code

**Your backend is now resilient against model failures! 🎉**
