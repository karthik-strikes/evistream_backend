"""LangChain callback that captures per-call LLM usage into llm_history.

evistream's codegen path (decomposition + signature generation) goes through
LangChain ChatModels, not DSPy. DSPy's history-flushing in utils/logging.py
therefore can't see those calls — we hook them via this callback instead.

Wire by attaching to the model via `.with_config(callbacks=[handler])` or by
passing `config={"callbacks": [handler]}` to `.invoke()`.

Cost: LangChain doesn't compute USD for us. We read `token_usage` from
`response.llm_output` and leave cost=0 — aggregate spend can be derived
downstream by multiplying tokens × model pricing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from utils.run_context import get_current_job_id

_logger = logging.getLogger(__name__)


class LLMHistoryCallbackHandler(BaseCallbackHandler):
    """Writes one row to `llm_history` per LangChain LLM call."""

    def __init__(self, source_file: str, schema_name: Optional[str] = None):
        self.source_file = source_file
        self.schema_name = schema_name

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        try:
            self._persist(response)
        except Exception:
            _logger.warning("LLMHistoryCallback failed (non-fatal)", exc_info=True)

    def _persist(self, response: LLMResult) -> None:
        llm_output = response.llm_output or {}
        token_usage = (
            llm_output.get("token_usage")
            or llm_output.get("usage")
            or {}
        )
        model = (
            llm_output.get("model_name")
            or llm_output.get("model")
            or ""
        )

        prompt_tokens = int(token_usage.get("prompt_tokens", token_usage.get("input_tokens", 0)) or 0)
        completion_tokens = int(token_usage.get("completion_tokens", token_usage.get("output_tokens", 0)) or 0)
        total_tokens = int(token_usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)

        if total_tokens == 0:
            return  # nothing to record

        # Some providers (Anthropic, newer OpenAI) attach usage per-generation
        # rather than llm_output; fall back if needed.
        if total_tokens == 0 and response.generations:
            for gen_list in response.generations:
                for gen in gen_list:
                    info = getattr(gen, "generation_info", None) or {}
                    usage = info.get("usage") or info.get("token_usage") or {}
                    prompt_tokens += int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
                    completion_tokens += int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
            total_tokens = prompt_tokens + completion_tokens
            if total_tokens == 0:
                return

        call_uuid = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        call_hash = hashlib.md5(
            json.dumps({"uuid": call_uuid, "ts": timestamp}).encode()
        ).hexdigest()

        data = {
            "call_hash": call_hash,
            "call_uuid": call_uuid,
            "call_timestamp": timestamp,
            "model": model,
            "cost": 0.0,  # LangChain doesn't compute USD; derive downstream
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cache_hit": False,
            "messages": [],
            "system_prompt": "",
            "user_prompt": "",
            "assistant_response": "",
            "source_file": self.source_file,
            "schema_name": self.schema_name,
            "job_id": get_current_job_id(),
            "metadata": {"transport": "langchain"},
        }

        try:
            from utils.supabase_client import get_supabase_client
            client = get_supabase_client()
            if client and client.is_available():
                client.client.table("llm_history").upsert(
                    data, on_conflict="call_hash"
                ).execute()
        except Exception:
            _logger.warning("Failed to upsert LangChain LLM call to llm_history", exc_info=True)


def make_callback_config(source_file: str, schema_name: Optional[str] = None) -> Dict[str, List[Any]]:
    """Convenience: build the dict you can pass as `config=...` to `.invoke()`."""
    return {"callbacks": [LLMHistoryCallbackHandler(source_file, schema_name)]}
