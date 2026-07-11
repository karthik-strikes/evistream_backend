"""
CachingChatAdapter — DSPy ChatAdapter subclass that reorders prompts to
maximize prompt-cache hits, across Anthropic, OpenAI, and Gemini.

Branch A: Stage 2 row_then_columns (signature has `row_anchor`)
  Splits the user message into two blocks at the `row_anchor` marker. The
  cached prefix is system + stable inputs (incl. markdown_content); only the
  small varying row_anchor block is re-processed per call. This works because
  all Stage 2 row calls share the same signature → identical system message.
  Anthropic-only: the default flat prompt is already an optimal shared prefix
  for OpenAI/Gemini's automatic prefix-cache, so splitting into content
  blocks here would add risk (different tokenization) without any benefit.

Branch B: Single-call scalar fields (signature has `markdown_content` but no
  `row_anchor`)
  Promotes markdown_content to a leading system-message text block, and
  strips it from the user message. The cached prefix is just the paper bytes,
  which are byte-identical across calls whose signatures produce different
  system instructions. This unlocks cross-signature cache hits for forms
  where the decomposer creates multiple parallel signatures per paper.
  Runs for Anthropic, OpenAI, and Gemini alike — all three cache on a shared
  prompt prefix, and all three document "static content first, variable
  content last" as the way to maximize cache hits (Anthropic explicitly via
  `cache_control`; OpenAI/Gemini automatically, no marker needed).

  Position rationale: cache prefix is matched sequentially from the start of
  the request. For the paper prefix to match across different signatures,
  nothing varying can sit before it — so paper has to be the FIRST content of
  the system message, with the per-signature instructions coming after.
  Verified empirically via test/cache_adapter_demo.py (block-0 hashes match
  across two distinct signatures).

  `cache_control` is only attached for Anthropic. Bedrock gpt-oss rejects the
  key with a 403 ("You invoked an unsupported model or your request did not
  allow prompt caching"), and OpenAI/Gemini don't need it — their caching is
  automatic on prefix match alone.

Branch C: Neither field present → no-op (codegen, evaluation, other LangChain
  paths, and any provider not listed above).
"""
from __future__ import annotations

import logging
from typing import Any

import dspy
from dspy.adapters.chat_adapter import ChatAdapter
from dspy.signatures.signature import Signature

logger = logging.getLogger(__name__)


class CachingChatAdapter(ChatAdapter):
    """ChatAdapter that places Anthropic cache breakpoints around the stable
    paper bytes — either as a leading system block (single-call) or as a
    user-message split before `row_anchor` (Stage 2 row_then_columns)."""

    DEFAULT_BREAK_FIELD = "row_anchor"
    DEFAULT_PAPER_FIELD = "markdown_content"

    def __init__(
        self,
        cache_break_before: str = DEFAULT_BREAK_FIELD,
        paper_field: str = DEFAULT_PAPER_FIELD,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.cache_break_before = cache_break_before
        self.paper_field = paper_field

    def format(
        self,
        signature: type[Signature],
        demos: list[dict[str, Any]],
        inputs: dict[str, Any],
    ) -> list[dict[str, Any]]:
        messages = super().format(signature, demos, inputs)

        active_model = getattr(getattr(dspy.settings, "lm", None), "model", "") or ""
        is_anthropic = active_model.startswith("anthropic/")
        is_openai = active_model.startswith("openai/")
        is_gemini = active_model.startswith("gemini/")
        if not (is_anthropic or is_openai or is_gemini):
            return messages

        input_fields = getattr(signature, "input_fields", {})

        # Branch A: row_anchor present → Stage 2 row caching (user-msg split).
        # Anthropic-only — see module docstring.
        if is_anthropic and self.cache_break_before in input_fields:
            return self._split_user_at_break_field(messages)

        # Branch B: markdown_content present, no row_anchor → promote paper.
        # Runs for all three providers; cache_control only attached for Anthropic.
        if self.paper_field in input_fields and inputs.get(self.paper_field):
            return self._promote_paper_to_system(
                messages, inputs[self.paper_field], add_cache_control=is_anthropic
            )

        # Branch C: codegen / eval / other → unchanged
        return messages

    # ------------------------------------------------------------------ #
    # Branch A — Stage 2 (existing behavior, preserved)
    # ------------------------------------------------------------------ #
    def _split_user_at_break_field(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not messages or messages[-1].get("role") != "user":
            return messages
        last = messages[-1]
        content = last.get("content")
        if not isinstance(content, str):
            return messages

        marker = f"[[ ## {self.cache_break_before} ## ]]"
        idx = content.find(marker)
        if idx <= 0:
            return messages

        cached_part = content[:idx].rstrip()
        fresh_part = content[idx:]
        if not cached_part or not fresh_part:
            return messages

        last["content"] = [
            {
                "type": "text",
                "text": cached_part,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": fresh_part},
        ]
        return messages

    # ------------------------------------------------------------------ #
    # Branch B — Single-call (new)
    # ------------------------------------------------------------------ #
    def _promote_paper_to_system(
        self, messages: list[dict[str, Any]], paper_text: str, add_cache_control: bool
    ) -> list[dict[str, Any]]:
        if not messages or messages[0].get("role") != "system":
            return messages
        if messages[-1].get("role") != "user":
            return messages

        last_user = messages[-1]
        user_content = last_user.get("content")
        if not isinstance(user_content, str):
            return messages

        marker = f"[[ ## {self.paper_field} ## ]]"
        start = user_content.find(marker)
        if start < 0:
            return messages
        next_marker_start = user_content.find("[[ ## ", start + len(marker))
        if next_marker_start < 0:
            next_marker_start = len(user_content)

        stripped_user = (
            user_content[:start] + user_content[next_marker_start:]
        ).strip()
        if not stripped_user:
            return messages
        last_user["content"] = stripped_user

        old_system = messages[0].get("content")
        if isinstance(old_system, str):
            old_system_blocks = [{"type": "text", "text": old_system}]
        elif isinstance(old_system, list):
            old_system_blocks = old_system
        else:
            return messages

        paper_block: dict[str, Any] = {"type": "text", "text": paper_text}
        if add_cache_control:
            paper_block["cache_control"] = {"type": "ephemeral"}

        messages[0]["content"] = [paper_block, *old_system_blocks]
        return messages


__all__ = ["CachingChatAdapter"]
