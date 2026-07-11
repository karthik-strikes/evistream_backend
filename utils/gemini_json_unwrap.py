"""
Gemini-only fix for DSPy JSONAdapter list-wrapping.

Gemini's JSON mode wraps list outputs as ``{"value": [...]}`` (or
``{"items": [...]}``, etc.) instead of returning a bare list. DSPy's
``JSONAdapter.parse`` then fails pydantic validation against
``list[dict[str, Any]]`` before our downstream unwrap in
``dspy_components/runtime_builders.py`` can run.

We monkey-patch ``JSONAdapter.parse`` once at startup. For Gemini calls
only, we detect single-key dict wrappers around list-typed output fields
and unwrap them before delegating to the original parse. For every other
provider the original parse runs unchanged.

A subclass installed via ``dspy.configure(adapter=...)`` would not catch
this — ``ChatAdapter.acall`` instantiates ``JSONAdapter()`` directly for
its JSON fallback path, bypassing the configured adapter. A targeted
patch on the class method is the only intercept point in the actual
failing call chain.
"""

import json
import logging
import os
import typing

import dspy
from dspy.adapters.json_adapter import JSONAdapter

logger = logging.getLogger(__name__)

_INSTALLED_FLAG = "_evistream_gemini_unwrap_installed"
_unwrap_logged = False

_RAW_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "logs", "gemini_raw_responses.log",
)
_raw_logger = None


def _get_raw_logger():
    global _raw_logger
    if _raw_logger is not None:
        return _raw_logger
    os.makedirs(os.path.dirname(_RAW_LOG_PATH), exist_ok=True)
    lg = logging.getLogger("gemini_raw_responses")
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    h = logging.FileHandler(_RAW_LOG_PATH, mode="a")
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    lg.addHandler(h)
    _raw_logger = lg
    return lg


def _is_list_annotation(annotation) -> bool:
    if annotation is list:
        return True
    origin = typing.get_origin(annotation)
    return origin in (list, typing.List)


def _unwrap_if_single_key_list(value):
    if isinstance(value, dict) and len(value) == 1:
        (only_key, only_val) = next(iter(value.items()))
        if isinstance(only_val, list):
            return only_val, only_key
    return value, None


def install_gemini_json_unwrap() -> None:
    if getattr(JSONAdapter, _INSTALLED_FLAG, False):
        return

    original_parse = JSONAdapter.parse

    def parse(self, signature, completion):
        lm = getattr(dspy.settings, "lm", None)
        model = getattr(lm, "model", "") or ""
        if not model.startswith("gemini/"):
            return original_parse(self, signature, completion)

        sig_name = getattr(signature, "__name__", repr(signature)[:60])
        raw_lg = _get_raw_logger()
        raw_lg.debug("=" * 80)
        raw_lg.debug("[SIG=%s] [MODEL=%s] output_fields=%s",
                     sig_name, model, list(signature.output_fields.keys()))
        raw_lg.debug("[RAW-COMPLETION] %s", completion[:4000])

        try:
            parsed = json.loads(completion)
        except (json.JSONDecodeError, TypeError) as e:
            raw_lg.debug("[PARSE-ERROR] %s — falling through to original parse", e)
            return original_parse(self, signature, completion)

        if not isinstance(parsed, dict):
            raw_lg.debug("[NON-DICT] type=%s — falling through", type(parsed).__name__)
            return original_parse(self, signature, completion)

        for field_name in signature.output_fields:
            if field_name in parsed:
                val = parsed[field_name]
                raw_lg.debug("[FIELD=%s] type=%s repr=%s",
                             field_name, type(val).__name__, repr(val)[:1500])

        mutated = False
        for field_name, field_info in signature.output_fields.items():
            if field_name not in parsed:
                continue
            if not _is_list_annotation(field_info.annotation):
                continue
            unwrapped, wrapper_key = _unwrap_if_single_key_list(parsed[field_name])
            if wrapper_key is not None:
                parsed[field_name] = unwrapped
                mutated = True
                raw_lg.debug("[UNWRAPPED] field=%s wrapper_key=%s new_len=%d",
                             field_name, wrapper_key,
                             len(unwrapped) if isinstance(unwrapped, list) else -1)
                global _unwrap_logged
                if not _unwrap_logged:
                    _unwrap_logged = True
                    logger.info(
                        '[gemini_json_unwrap] unwrapped {"%s": [...]} for field=%s '
                        "(further unwraps will run silently)",
                        wrapper_key,
                        field_name,
                    )

        if not mutated:
            return original_parse(self, signature, completion)

        return original_parse(self, signature, json.dumps(parsed))

    JSONAdapter.parse = parse
    setattr(JSONAdapter, _INSTALLED_FLAG, True)
    logger.info("[gemini_json_unwrap] JSONAdapter.parse patched for Gemini list-unwrap")
