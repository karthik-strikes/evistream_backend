"""Unit tests for CachingChatAdapter — prompt-cache reordering must never lose
or duplicate prompt content.

Replaces the old test/cache_adapter_demo.py, which mirrored the adapter's logic
in a second copy rather than importing it. That copy drifted into the same bug
as production and, because it only printed the user message instead of asserting
on it, the corruption went unnoticed. Everything here imports the real adapter.

The adapter branches on `dspy.settings.lm.model`, so every test sets the active
model via `dspy.context(lm=...)` — the same mechanism ModelRouter uses
(utils/circuit_breaker.py:464-468).
"""

import hashlib
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import dspy  # noqa: E402
from dspy.adapters.chat_adapter import ChatAdapter  # noqa: E402

from dspy_components.runtime_builders import build_signature_class  # noqa: E402
from utils.caching_adapter import CachingChatAdapter  # noqa: E402

# DSPy's trailing output instruction. Its deletion was the original bug.
DSPY_OUTPUT_INSTRUCTION = "Respond with the corresponding output fields"

PAPER = "# A Randomized Trial of X\n\n" + ("Lorem ipsum body text " * 200).strip()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _sig(inputs, outputs=("out",), name="S"):
    """Build a signature through the production builder, not a hand-rolled
    dspy.Signature subclass, so tests exercise real generated shapes."""
    sig_def = {
        "class_name": name,
        "docstring": f"{name} extraction signature.",
        "input_fields": [{"name": n, "type": "str", "desc": f"input {n}"} for n in inputs],
        "output_fields": [
            {"name": o, "type": "Dict[str, Any]", "description": f"field {o}",
             "source_grounded": True}
            for o in outputs
        ],
    }
    return build_signature_class(sig_def, f"cachetest_{name}")


def _fmt(signature, inputs, model="anthropic/claude-sonnet-5"):
    """Format through CachingChatAdapter with `model` as the active LM."""
    lm = dspy.LM(model, max_tokens=1000)
    with dspy.context(lm=lm):
        return CachingChatAdapter().format(signature, demos=[], inputs=inputs)


def _user_text(messages):
    c = messages[-1]["content"]
    return c if isinstance(c, str) else "".join(b.get("text", "") for b in c)


def _system_blocks(messages):
    c = messages[0]["content"]
    return c if isinstance(c, list) else [{"type": "text", "text": c}]


def _prefix_hash(messages, up_to_block=None):
    """Hash the system-message prefix, optionally stopping after a block index."""
    h = hashlib.sha256()
    for m in messages:
        if m["role"] != "system":
            break
        content = m["content"]
        if isinstance(content, str):
            h.update(content.encode())
        else:
            for i, blk in enumerate(content):
                h.update(blk.get("text", "").encode())
                if up_to_block is not None and i == up_to_block:
                    return h.hexdigest()
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Branch B — paper promoted to system (single-call)
# --------------------------------------------------------------------------- #

class TestBranchBIntegrity:
    def test_dspy_output_instruction_survives(self):
        """THE REGRESSION. markdown_content as the only input field used to make
        the boundary scan land inside DSPy's own trailing prose, deleting
        'Respond with the corresponding output fields, starting with the field `'."""
        msgs = _fmt(_sig(["markdown_content"], name="Solo"), {"markdown_content": PAPER})
        assert DSPY_OUTPUT_INSTRUCTION in _user_text(msgs)

    def test_user_message_does_not_start_with_a_field_marker(self):
        """A user turn opening with '[[ ## ' looks like an answer section, not a
        request — that was the visible symptom of the deletion."""
        msgs = _fmt(_sig(["markdown_content"], name="Solo2"), {"markdown_content": PAPER})
        assert not _user_text(msgs).lstrip().startswith("[[ ## ")

    def test_paper_appears_exactly_once_in_the_request(self):
        msgs = _fmt(_sig(["markdown_content"], name="Once"), {"markdown_content": PAPER})
        blocks = _system_blocks(msgs)
        assert blocks[0]["text"] == PAPER, "paper must be system block 0, verbatim"
        assert PAPER not in _user_text(msgs), "paper must be gone from the user message"
        whole = "".join(b.get("text", "") for b in blocks) + _user_text(msgs)
        assert whole.count(PAPER) == 1

    def test_anthropic_gets_cache_control_on_the_paper_block(self):
        msgs = _fmt(_sig(["markdown_content"], name="CC"), {"markdown_content": PAPER})
        assert _system_blocks(msgs)[0].get("cache_control") == {"type": "ephemeral"}

    def test_paper_containing_a_literal_field_marker_is_fully_removed(self):
        """Second failure mode of the old boundary scan: a paper whose body
        contains '[[ ## ' truncated the excision, leaking the paper's tail into
        the user message while the full paper also sat in system."""
        tricky = "Intro text.\n[[ ## fake ## ]]\nTail after the fake marker."
        msgs = _fmt(_sig(["markdown_content"], name="Tricky"), {"markdown_content": tricky})
        user = _user_text(msgs)
        assert "Tail after the fake marker." not in user
        assert "Intro text." not in user
        assert _system_blocks(msgs)[0]["text"] == tricky
        assert DSPY_OUTPUT_INSTRUCTION in user

    def test_upstream_dependency_input_survives(self):
        """Signatures with requires_fields carry extra inputs after the paper;
        those sections must be preserved intact."""
        sig = _sig(["markdown_content", "study_design"], name="Dep")
        msgs = _fmt(sig, {"markdown_content": PAPER, "study_design": "RCT_UPSTREAM_VALUE"})
        user = _user_text(msgs)
        assert "[[ ## study_design ## ]]" in user
        assert "RCT_UPSTREAM_VALUE" in user
        assert DSPY_OUTPUT_INSTRUCTION in user
        assert PAPER not in user

    def test_cache_prefix_is_identical_across_different_signatures(self):
        """The demo's original proof point, now an assertion: promoting the paper
        to block 0 makes the cached prefix byte-identical across signatures whose
        instructions differ, which is the whole reason Branch B exists."""
        a = _sig(["markdown_content"], outputs=("study_design", "sample_size"), name="PrefA")
        b = _sig(["markdown_content"], outputs=("mean_age", "pct_female"), name="PrefB")
        ma = _fmt(a, {"markdown_content": PAPER})
        mb = _fmt(b, {"markdown_content": PAPER})
        assert _prefix_hash(ma, up_to_block=0) == _prefix_hash(mb, up_to_block=0)
        # ...and the full system prefix still differs, since instructions vary.
        assert _prefix_hash(ma) != _prefix_hash(mb)
        # Stock adapter: no shared prefix at all.
        stock_a = ChatAdapter().format(a, demos=[], inputs={"markdown_content": PAPER})
        stock_b = ChatAdapter().format(b, demos=[], inputs={"markdown_content": PAPER})
        assert _prefix_hash(stock_a) != _prefix_hash(stock_b)


class TestBranchBProviders:
    @pytest.mark.parametrize("model", ["openai/gpt-4o", "gemini/gemini-2.5-flash"])
    def test_promoted_without_cache_control(self, model):
        """OpenAI/Gemini cache automatically on prefix match; Bedrock gpt-oss 403s
        when the cache_control key is present (see module docstring)."""
        msgs = _fmt(_sig(["markdown_content"], name="Prov"), {"markdown_content": PAPER}, model=model)
        block0 = _system_blocks(msgs)[0]
        assert block0["text"] == PAPER
        assert "cache_control" not in block0
        assert DSPY_OUTPUT_INSTRUCTION in _user_text(msgs)

    def test_unlisted_provider_is_untouched(self):
        sig = _sig(["markdown_content"], name="Bed")
        inputs = {"markdown_content": PAPER}
        got = _fmt(sig, inputs, model="bedrock/qwen.qwen3-next-80b-a3b")
        expected = ChatAdapter().format(sig, demos=[], inputs=inputs)
        assert got == expected


class TestBranchBFailSafe:
    def test_unrecognized_layout_returns_messages_unchanged(self):
        """The defect behind the bug was the absence of an 'I don't recognize this
        layout' path: an unexpected format produced silent damage instead of a
        no-op. Losing a cache hit is cheap; losing prompt integrity is not."""
        sig = _sig(["markdown_content"], name="Safe")
        messages = ChatAdapter().format(sig, demos=[], inputs={"markdown_content": PAPER})
        before = [dict(m) for m in messages]
        out = CachingChatAdapter()._promote_paper_to_system(
            messages, "A PAPER THAT IS NOT IN THIS MESSAGE", add_cache_control=True
        )
        assert out == before


# --------------------------------------------------------------------------- #
# Branch A — Stage 2 row caching (must remain untouched by the Branch B fix)
# --------------------------------------------------------------------------- #

class TestBranchARowAnchor:
    def test_splits_at_row_anchor_and_loses_nothing(self):
        sig = _sig(["markdown_content", "row_anchor"], name="Stage2")
        inputs = {"markdown_content": PAPER, "row_anchor": '{"outcome": "pain"}'}
        stock = ChatAdapter().format(sig, demos=[], inputs=inputs)
        stock_user = stock[-1]["content"]

        msgs = _fmt(sig, inputs)
        content = msgs[-1]["content"]
        assert isinstance(content, list) and len(content) == 2
        assert content[0].get("cache_control") == {"type": "ephemeral"}
        assert "cache_control" not in content[1]
        assert "[[ ## row_anchor ## ]]" in content[1]["text"]
        # Branch A splits rather than deletes: rejoining must lose no content.
        # Compared whitespace-insensitively because the split intentionally
        # rstrip()s the cached half, dropping the blank line before the marker.
        rejoined = content[0]["text"] + content[1]["text"]
        assert "".join(rejoined.split()) == "".join(stock_user.split())
        assert DSPY_OUTPUT_INSTRUCTION in _user_text(msgs)

    def test_paper_stays_in_user_message(self):
        """Stage 2 caches via the user-message split, so the paper must NOT be
        promoted to system here — Branch A returns before Branch B can run."""
        sig = _sig(["markdown_content", "row_anchor"], name="Stage2b")
        msgs = _fmt(sig, {"markdown_content": PAPER, "row_anchor": "{}"})
        assert PAPER in _user_text(msgs)
        assert isinstance(msgs[0]["content"], str), "system must stay a plain string"


# --------------------------------------------------------------------------- #
# Branch C — neither field present
# --------------------------------------------------------------------------- #

class TestBranchCNoop:
    def test_signature_without_paper_or_anchor_is_unchanged(self):
        sig = _sig(["question"], name="NoPaper")
        inputs = {"question": "What is the sample size?"}
        got = _fmt(sig, inputs)
        expected = ChatAdapter().format(sig, demos=[], inputs=inputs)
        assert got == expected

    def test_empty_paper_value_is_unchanged(self):
        sig = _sig(["markdown_content"], name="EmptyPaper")
        inputs = {"markdown_content": ""}
        got = _fmt(sig, inputs)
        expected = ChatAdapter().format(sig, demos=[], inputs=inputs)
        assert got == expected
