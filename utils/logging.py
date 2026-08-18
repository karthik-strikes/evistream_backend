import logging
import pandas as pd
import json
import hashlib
from pathlib import Path
from datetime import datetime
import dspy

from core.config import DEFAULT_HISTORY_CSV, PROJECT_ROOT

_logger = logging.getLogger(__name__)


def _strip_nul(value):
    """Recursively remove NUL bytes (\\u0000) that PostgreSQL text/jsonb cannot store.

    PDF text extraction frequently leaves stray NUL/control bytes in the content,
    which flow into the LLM prompt and then into this audit insert, causing
    postgrest error 22P05 "unsupported Unicode escape sequence".
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    if isinstance(value, dict):
        return {k: _strip_nul(v) for k, v in value.items()}
    return value


# Global variables to track processed calls
_processed_hashes = set()
# Default history CSV path, built relative to the project root
_csv_path = DEFAULT_HISTORY_CSV
# Set to True to log full prompts (increases CSV size)
_include_full_prompts = False


def set_log_file(csv_path: str, include_full_prompts: bool = False):
    """Set the CSV file path for logging.

    Args:
        csv_path: Path to the CSV file for logging
        include_full_prompts: If True, log full system and user prompts (increases file size)
    """
    global _csv_path, _processed_hashes, _include_full_prompts
    _csv_path = csv_path
    _include_full_prompts = include_full_prompts

    # OPTIMIZATION: Do NOT load the entire CSV into memory.
    # We will only track hashes for the CURRENT session to avoid duplicates within a run.
    # If a run is restarted, we might log duplicates, but that's better than O(N) startup time.
    _processed_hashes = set()

    if not Path(csv_path).exists():
        print(f"New log file will be created: {csv_path}")
        if include_full_prompts:
            print("  ⚠️  Full prompts will be logged (large file size)")


def log_history(clear_memory: bool = True, save_to_supabase: bool = True, source_file: str = None, schema_name: str = None, run_papers=None):
    """Log current DSPy history to CSV and optionally to Supabase.

    Args:
        clear_memory: If True, clears the LM history after logging to free RAM.
        save_to_supabase: If True, also saves to Supabase (in addition to CSV).
        source_file: Optional source file path for context linking.
        schema_name: Optional schema name for context linking.
        run_papers: Optional list of the run's papers (`run_batch`'s own
            argument: `{"doc_id", "markdown_content", "path"}`). Passing it lets
            each row record which paper it was about — the only moment that is
            knowable, since the markdown lives in S3 and this list is gone
            afterwards. See utils/llm_call_labels.py.
    """
    global _processed_hashes, _csv_path

    # Get LM from dspy settings
    try:
        lm = dspy.settings.lm
    except:
        print("Error: No LM found in dspy.settings")
        return 0

    if not hasattr(lm, 'history') or not lm.history:
        # print("No history found in language model")
        return 0

    new_records = []
    new_call_data = []  # For Supabase

    for call_data in lm.history:
        # Generate unique hash
        hash_content = {
            'messages': call_data.get('messages', []),
            'timestamp': call_data.get('timestamp', ''),
            'uuid': call_data.get('uuid', ''),
        }
        call_hash = hashlib.md5(json.dumps(
            hash_content, sort_keys=True, default=str).encode()).hexdigest()

        # Skip if already processed IN THIS SESSION
        if call_hash in _processed_hashes:
            continue

        # Extract call info
        messages = call_data.get('messages', [])
        system_msg = next((m.get('content', '')
                          for m in messages if m.get('role') == 'system'), '')
        user_msg = next((m.get('content', '')
                        for m in messages if m.get('role') == 'user'), '')

        # Extract response
        response_obj = call_data.get('response', {})
        assistant_response = ""
        if hasattr(response_obj, 'choices') and response_obj.choices:
            assistant_response = response_obj.choices[0].message.content

        # Extract usage
        usage = call_data.get('usage', {})
        if isinstance(usage, dict):
            prompt_tokens = usage.get('prompt_tokens', 0)
            completion_tokens = usage.get('completion_tokens', 0)
            total_tokens = usage.get('total_tokens', 0)
        else:
            prompt_tokens = completion_tokens = total_tokens = 0

        record = {
            'call_hash': call_hash,
            'timestamp': call_data.get('timestamp', datetime.now().isoformat()),
            'uuid': call_data.get('uuid', ''),
            'model': call_data.get('model', ''),
            'cost': call_data.get('cost', 0.0),
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'total_tokens': total_tokens,
            'system_msg_length': len(system_msg),
            'user_msg_preview': user_msg[:200] if user_msg else '',
            'response_preview': assistant_response[:200] if assistant_response else '',
            'cache_hit': getattr(response_obj, 'cache_hit', False) if response_obj else False,
            'logged_at': datetime.now().isoformat()
        }

        # Add full prompts if enabled
        if _include_full_prompts:
            record['full_system_prompt'] = system_msg
            record['full_user_prompt'] = user_msg
            record['full_response'] = assistant_response

        new_records.append(record)
        new_call_data.append(call_data)  # Keep original for Supabase
        _processed_hashes.add(call_hash)

    if not new_records:
        if clear_memory:
            lm.history.clear()
        return 0

    # Save to CSV (Append mode) - ALWAYS save as backup
    new_df = pd.DataFrame(new_records)

    if Path(_csv_path).exists():
        new_df.to_csv(_csv_path, mode='a', header=False, index=False)
    else:
        Path(_csv_path).parent.mkdir(parents=True, exist_ok=True)
        new_df.to_csv(_csv_path, index=False)

    # Optionally save to Supabase
    if save_to_supabase:
        try:
            from utils.supabase_client import get_supabase_client

            client = get_supabase_client()
            if client and client.is_available():
                # Save each call to Supabase synchronously (simpler, no async issues)
                saved_count = 0
                # Stamp every row with the current run so per-run cost/token
                # attribution is exact (see utils/run_context.py). None outside
                # a stamped worker context (e.g. eval scripts) — that's fine.
                from utils.run_context import (
                    get_current_extraction_id,
                    get_current_job_id,
                )
                _run_job_id = get_current_job_id()
                _run_extraction_id = get_current_extraction_id()

                # Derive what each call was FOR — paper, pipeline step, retry,
                # duration — and store it alongside what it cost. Three of those
                # labels need the whole run at once (see llm_call_labels), which
                # is why this happens here rather than per row. Never fatal: an
                # unlabeled cost row is still a cost row.
                try:
                    from utils.llm_call_labels import label_run_calls
                    _labels = label_run_calls(new_call_data, run_papers)
                except Exception:
                    _logger.warning(
                        "Could not label LLM calls for this run", exc_info=True)
                    _labels = []

                for _idx, call_data in enumerate(new_call_data):
                    try:
                        # Use synchronous save method

                        # Extract messages — strip NUL bytes Postgres can't store (PDF text artifacts)
                        messages = _strip_nul(call_data.get('messages', []))
                        system_msg = next(
                            (m.get('content', '') for m in messages if m.get('role') == 'system'), '')
                        user_msg = next(
                            (m.get('content', '') for m in messages if m.get('role') == 'user'), '')

                        # Extract response
                        response_obj = call_data.get('response', {})
                        assistant_response = ""
                        if hasattr(response_obj, 'choices') and response_obj.choices:
                            assistant_response = _strip_nul(response_obj.choices[0].message.content or "")

                        # Extract usage
                        usage = call_data.get('usage', {})
                        if isinstance(usage, dict):
                            prompt_tokens = usage.get('prompt_tokens', 0)
                            completion_tokens = usage.get(
                                'completion_tokens', 0)
                            total_tokens = usage.get('total_tokens', 0)
                            cache_creation_input_tokens = usage.get(
                                'cache_creation_input_tokens', 0) or 0
                            cache_read_input_tokens = usage.get(
                                'cache_read_input_tokens', 0) or 0
                            if not cache_read_input_tokens:
                                # OpenAI/Gemini report cache reads under
                                # prompt_tokens_details.cached_tokens instead of
                                # the Anthropic-shaped top-level field.
                                _details = usage.get('prompt_tokens_details') or {}
                                if isinstance(_details, dict):
                                    cache_read_input_tokens = _details.get(
                                        'cached_tokens', 0) or 0
                        else:
                            prompt_tokens = completion_tokens = total_tokens = 0
                            cache_creation_input_tokens = cache_read_input_tokens = 0

                        # Generate unique hash
                        hash_content = {
                            'messages': messages,
                            'timestamp': call_data.get('timestamp', ''),
                            'uuid': call_data.get('uuid', ''),
                        }
                        call_hash = hashlib.md5(json.dumps(
                            hash_content, sort_keys=True, default=str).encode()).hexdigest()

                        # Prepare data for insertion
                        data = {
                            "call_hash": call_hash,
                            "call_uuid": call_data.get('uuid', ''),
                            "call_timestamp": call_data.get('timestamp'),
                            "model": call_data.get('model', ''),
                            "cost": call_data.get('cost', 0.0),
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": total_tokens,
                            "cache_creation_input_tokens": cache_creation_input_tokens,
                            "cache_read_input_tokens": cache_read_input_tokens,
                            "cache_hit": getattr(response_obj, 'cache_hit', False) if response_obj else False,
                            "messages": messages,
                            "system_prompt": system_msg,
                            "user_prompt": user_msg,
                            "assistant_response": assistant_response,
                            "source_file": source_file,
                            "schema_name": schema_name,
                            "job_id": _run_job_id,
                            # NOT extraction_id: that column's FK points at
                            # `extraction_results(id)`, not `extractions(id)`, so
                            # writing the run's id raises 23503 and loses the whole
                            # row. That mismatch is why it is NULL in all 24,682
                            # rows. `job_id` is the exact run key; /usage maps it to
                            # an extraction via `jobs.input_data->>extraction_id`.
                            "metadata": (
                                {**_labels[_idx], "extraction_id": _run_extraction_id}
                                if _idx < len(_labels) and _run_extraction_id
                                else (_labels[_idx] if _idx < len(_labels) else {})
                            ),
                        }

                        # Insert into 'llm_history' table (use upsert to handle duplicates)
                        result = client.client.table("llm_history").upsert(
                            data, on_conflict="call_hash").execute()
                        if result.data:
                            saved_count += 1
                    except Exception:
                        _logger.warning("Failed to persist LLM call to Supabase", exc_info=True)

                if saved_count > 0:
                    print(
                        f"✓ Saved {saved_count} LLM history records to Supabase")
        except Exception:
            _logger.warning("Failed to persist LLM history to Supabase", exc_info=True)

    # CRITICAL OPTIMIZATION: Clear memory after logging
    if clear_memory:
        lm.history.clear()

    return len(new_records)


def log_all_lm_histories(source_file: str = None, schema_name: str = None, run_papers=None) -> int:
    """Flush LM history from every model in the ModelRouter cache.

    evistream uses `dspy.context(lm=...)` per coroutine instead of
    `dspy.configure()`, so `dspy.settings.lm.history` only sees a subset
    of calls. The authoritative history lives on each cached LM in
    `ModelRouter._lm_cache`. This walks them all.
    """
    try:
        from utils.circuit_breaker import ModelRouter
        router = ModelRouter.get_instance()
        lms = list(router._lm_cache.values())
    except Exception:
        _logger.warning("ModelRouter unavailable; falling back to dspy.settings.lm", exc_info=True)
        return log_history(source_file=source_file, schema_name=schema_name, run_papers=run_papers)

    total = 0
    for lm in lms:
        if not getattr(lm, "history", None):
            continue
        try:
            with dspy.context(lm=lm):
                total += log_history(
                    clear_memory=True,
                    save_to_supabase=True,
                    source_file=source_file,
                    schema_name=schema_name,
                    run_papers=run_papers,
                )
        except Exception:
            _logger.warning(f"Failed to flush history for LM {getattr(lm, 'model', '?')}", exc_info=True)
    return total


def show_stats():
    """Show statistics from the logged history."""
    global _csv_path

    if not Path(_csv_path).exists():
        print("No history file found")
        return

    try:
        df = pd.read_csv(_csv_path)

        print(f"\nDSPy History Stats from {_csv_path}:")
        print("=" * 50)
        print(f"Total calls: {len(df)}")

        if 'model' in df.columns:
            print(f"Unique models: {df['model'].nunique()}")
            print("Model breakdown:")
            for model, count in df['model'].value_counts().head().items():
                print(f"  {model}: {count} calls")

        if 'cost' in df.columns:
            # If prompt_tokens == 0, set cost = 0 for those rows
            df.loc[df['prompt_tokens'] == 0, 'cost'] = 0

            # Recompute totals
            total_cost = df['cost'].sum()
            avg_cost = df['cost'].mean()

            print(f"Total cost: ${total_cost:.4f}")
            print(f"Average cost per call: ${avg_cost:.4f}")

        if 'total_tokens' in df.columns:
            total_tokens = df['total_tokens'].sum()
            avg_tokens = df['total_tokens'].mean()
            print(f"Total tokens: {total_tokens:,}")
            print(f"Average tokens per call: {avg_tokens:.1f}")

        if 'cache_hit' in df.columns:
            cache_rate = df['cache_hit'].mean() * 100
            print(f"Cache hit rate: {cache_rate:.1f}%")

        if 'timestamp' in df.columns:
            print(
                f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")

    except Exception as e:
        print(f"Error reading history: {e}")


def view_recent(n=5):
    """View the most recent n logged calls."""
    global _csv_path

    if not Path(_csv_path).exists():
        print("No history file found")
        return

    try:
        df = pd.read_csv(_csv_path)
        recent = df.tail(n)

        print(f"\nMost Recent {n} DSPy Calls:")
        print("=" * 60)

        for _, row in recent.iterrows():
            print(f"Time: {row['timestamp']}")
            print(f"Model: {row['model']}")
            print(f"Tokens: {row['total_tokens']} | Cost: ${row['cost']:.4f}")
            print(f"User: {row['user_msg_preview'][:100]}...")
            print(f"Response: {row['response_preview'][:100]}...")
            print("-" * 40)

    except Exception as e:
        print(f"Error reading history: {e}")


def clear_cache():
    """Clear the processed hashes cache (will reprocess all history next time)."""
    global _processed_hashes
    _processed_hashes.clear()
    print("Cleared processed hashes cache")


def export_full_history(output_file: str = "full_dspy_history.json"):
    """Export complete DSPy history with full messages to JSON."""
    try:
        lm = dspy.settings.lm
        if hasattr(lm, 'history') and lm.history:
            with open(output_file, 'w') as f:
                json.dump(lm.history, f, indent=2, default=str)
            print(f"Exported full history to {output_file}")
        else:
            print("No history found to export")
    except Exception as e:
        print(f"Error exporting history: {e}")


def log_execution_time(start_time: float, end_time: float, mode: str, source: str, target: str, schema: str):
    """Log execution time to CSV."""
    duration = end_time - start_time
    csv_path = PROJECT_ROOT / "outputs" / "logs" / "execution_times.csv"

    record = {
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "duration_seconds": round(duration, 2),
        "source": source,
        "target": target,
        "schema": schema
    }

    df = pd.DataFrame([record])

    if Path(csv_path).exists():
        df.to_csv(csv_path, mode='a', header=False, index=False)
    else:
        # Ensure parent directory exists before first write
        Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(csv_path, index=False)

    print(f"\nExecution time logged: {duration:.2f}s")
