"""
Supabase client wrapper for eviStreams data storage.
Provides async methods to store extracted results and evaluation metrics.
"""

import os
from typing import Dict, List, Optional, Any
from datetime import datetime
from supabase import create_client, Client
from core.config import SUPABASE_URL, SUPABASE_KEY


class SupabaseClient:
    """Async-compatible Supabase client wrapper for eviStreams."""

    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        """
        Initialize Supabase client.

        Args:
            url: Supabase project URL (defaults to SUPABASE_URL from config)
            key: Supabase anon/service key (defaults to SUPABASE_KEY from config)
        """
        self.url = url or SUPABASE_URL
        self.key = key or SUPABASE_KEY
        self.client: Optional[Client] = None

        if self.url and self.key:
            try:
                self.client = create_client(self.url, self.key)
                print(f"✓ Supabase client initialized: {self.url}")
            except Exception as e:
                print(f"⚠️ Failed to initialize Supabase client: {e}")
                self.client = None
        else:
            print(
                "⚠️ Supabase credentials not configured. Set SUPABASE_URL and SUPABASE_KEY in config.py")

    def is_available(self) -> bool:
        """Check if Supabase client is available."""
        return self.client is not None

    async def save_extracted_records(
        self,
        extracted_records: List[Dict],
        source_file: str,
        schema_name: str,
        metadata: Optional[Dict] = None
    ) -> Optional[str]:
        """
        Save extracted records to Supabase.

        Args:
            extracted_records: List of extracted record dictionaries
            source_file: Path to source file
            schema_name: Name of the schema used (e.g., 'patient_population')
            metadata: Optional metadata dictionary

        Returns:
            UUID of inserted record or None if failed
        """
        if not self.is_available():
            return None

        try:
            # Prepare data for insertion
            data = {
                "source_file": source_file,
                "schema_name": schema_name,
                "extracted_records": extracted_records,
                "total_records": len(extracted_records),
                "extraction_timestamp": datetime.now().isoformat(),
                "pipeline_version": "DSPy_Async_1.0",
                "metadata": metadata or {}
            }

            # Insert into 'extracted_results' table
            result = self.client.table(
                "extracted_results").insert(data).execute()

            if result.data:
                record_id = result.data[0].get("id")
                print(
                    f"✓ Saved {len(extracted_records)} records to Supabase (ID: {record_id})")
                return record_id
            return None

        except Exception as e:
            print(f"❌ Error saving extracted records to Supabase: {e}")
            return None

    async def save_evaluation_metrics(
        self,
        evaluation_results: Dict,
        source_file: str,
        schema_name: str,
        extracted_record_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Save evaluation metrics to Supabase.

        Args:
            evaluation_results: Dictionary containing evaluation metrics
            source_file: Path to source file
            schema_name: Name of the schema used
            extracted_record_id: Optional ID of related extracted_results record

        Returns:
            UUID of inserted record or None if failed
        """
        if not self.is_available():
            return None

        try:
            # Prepare evaluation data
            data = {
                "source_file": source_file,
                "schema_name": schema_name,
                "extracted_record_id": extracted_record_id,
                "precision": evaluation_results.get("precision", 0.0),
                "recall": evaluation_results.get("recall", 0.0),
                "f1": evaluation_results.get("f1", 0.0),
                "completeness": evaluation_results.get("completeness", 0.0),
                "cohens_kappa": evaluation_results.get("cohens_kappa", 0.0),
                "num_extracted": evaluation_results.get("num_extracted", 0),
                "num_ground_truth": evaluation_results.get("num_ground_truth", 0),
                "tp": evaluation_results.get("TP", 0),
                "fp": evaluation_results.get("FP", 0),
                "fn": evaluation_results.get("FN", 0),
                "evaluation_timestamp": datetime.now().isoformat(),
                "semantic_enabled": evaluation_results.get("semantic_enabled", False)
            }

            # Insert into 'evaluation_metrics' table
            result = self.client.table(
                "evaluation_metrics").insert(data).execute()

            if result.data:
                record_id = result.data[0].get("id")
                print(
                    f"✓ Saved evaluation metrics to Supabase (ID: {record_id})")
                return record_id
            return None

        except Exception as e:
            print(f"❌ Error saving evaluation metrics to Supabase: {e}")
            return None

    async def save_evaluation_details(
        self,
        baseline_results: List[Dict],
        ground_truth: List[Dict],
        matches: List[tuple],
        source_file: str,
        schema_name: str,
        evaluation_metric_id: Optional[str] = None
    ) -> int:
        """
        Save detailed evaluation data (TP/FP/FN records) to Supabase.

        Args:
            baseline_results: Extracted records
            ground_truth: Ground truth records
            matches: List of (ext_idx, gt_idx, score) tuples
            source_file: Path to source file
            schema_name: Name of the schema used
            evaluation_metric_id: Optional ID of related evaluation_metrics record

        Returns:
            Number of records inserted
        """
        if not self.is_available():
            return 0

        try:
            rows = []
            matched_gt_indices = set()
            matched_ext_indices = set()
            timestamp = datetime.now().isoformat()

            # Process matched pairs (TP)
            for ext_idx, gt_idx, score in matches:
                if score >= 0.5:
                    matched_gt_indices.add(gt_idx)
                    matched_ext_indices.add(ext_idx)

                    # Ground truth row (TP)
                    gt_record = ground_truth[gt_idx].copy()
                    gt_row = {
                        "data_type": "ground_truth",
                        "source_file": source_file,
                        "schema_name": schema_name,
                        "match_score": float(score),
                        "pair_id": f"{source_file}_{gt_idx}",
                        "classification": "TP",
                        "evaluation_metric_id": evaluation_metric_id,
                        "timestamp": timestamp,
                        "record_data": gt_record
                    }
                    rows.append(gt_row)

                    # Extracted row (TP)
                    ext_record = baseline_results[ext_idx].copy()
                    ext_row = {
                        "data_type": "extracted",
                        "source_file": source_file,
                        "schema_name": schema_name,
                        "match_score": float(score),
                        "pair_id": f"{source_file}_{gt_idx}",
                        "classification": "TP",
                        "evaluation_metric_id": evaluation_metric_id,
                        "timestamp": timestamp,
                        "record_data": ext_record
                    }
                    rows.append(ext_row)

            # Add unmatched ground truth (FN)
            for gt_idx, gt_record in enumerate(ground_truth):
                if gt_idx not in matched_gt_indices:
                    row = {
                        "data_type": "ground_truth",
                        "source_file": source_file,
                        "schema_name": schema_name,
                        "match_score": 0.0,
                        "pair_id": f"{source_file}_{gt_idx}_missing",
                        "classification": "FN",
                        "evaluation_metric_id": evaluation_metric_id,
                        "timestamp": timestamp,
                        "record_data": gt_record.copy()
                    }
                    rows.append(row)

            # Add unmatched extractions (FP)
            for ext_idx, ext_record in enumerate(baseline_results):
                if ext_idx not in matched_ext_indices:
                    row = {
                        "data_type": "extracted",
                        "source_file": source_file,
                        "schema_name": schema_name,
                        "match_score": 0.0,
                        "pair_id": f"{source_file}_fp_{ext_idx}",
                        "classification": "FP",
                        "evaluation_metric_id": evaluation_metric_id,
                        "timestamp": timestamp,
                        "record_data": ext_record.copy()
                    }
                    rows.append(row)

            if rows:
                # Batch insert into 'evaluation_details' table
                result = self.client.table(
                    "evaluation_details").insert(rows).execute()
                count = len(result.data) if result.data else 0
                print(f"✓ Saved {count} evaluation detail records to Supabase")
                return count
            return 0

        except Exception as e:
            print(f"❌ Error saving evaluation details to Supabase: {e}")
            return 0

    def get_extracted_results(
        self,
        schema_name: Optional[str] = None,
        source_file: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        Query extracted results from Supabase.

        Args:
            schema_name: Filter by schema name
            source_file: Filter by source file
            limit: Maximum number of results

        Returns:
            List of extracted result records
        """
        if not self.is_available():
            return []

        try:
            query = self.client.table("extracted_results").select("*")

            if schema_name:
                query = query.eq("schema_name", schema_name)
            if source_file:
                query = query.eq("source_file", source_file)

            query = query.order("extraction_timestamp", desc=True).limit(limit)
            result = query.execute()

            return result.data if result.data else []

        except Exception as e:
            print(f"❌ Error querying extracted results: {e}")
            return []

    def get_evaluation_metrics(
        self,
        schema_name: Optional[str] = None,
        source_file: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        Query evaluation metrics from Supabase.

        Args:
            schema_name: Filter by schema name
            source_file: Filter by source file
            limit: Maximum number of results

        Returns:
            List of evaluation metric records
        """
        if not self.is_available():
            return []

        try:
            query = self.client.table("evaluation_metrics").select("*")

            if schema_name:
                query = query.eq("schema_name", schema_name)
            if source_file:
                query = query.eq("source_file", source_file)

            query = query.order("evaluation_timestamp", desc=True).limit(limit)
            result = query.execute()

            return result.data if result.data else []

        except Exception as e:
            print(f"❌ Error querying evaluation metrics: {e}")
            return []

    async def save_llm_history(
        self,
        call_data: Dict,
        source_file: Optional[str] = None,
        schema_name: Optional[str] = None,
        extraction_id: Optional[str] = None,
        evaluation_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Save LLM call history to Supabase.

        Args:
            call_data: Dictionary containing LLM call information from DSPy history
            source_file: Optional path to source file being processed
            schema_name: Optional schema name being used
            extraction_id: Optional ID of related extraction
            evaluation_id: Optional ID of related evaluation

        Returns:
            UUID of inserted record or None if failed
        """
        if not self.is_available():
            return None

        try:
            import hashlib
            import json

            # Extract messages
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
                "cache_hit": getattr(response_obj, 'cache_hit', False) if response_obj else False,
                "messages": messages,
                "system_prompt": system_msg,
                "user_prompt": user_msg,
                "assistant_response": assistant_response,
                "source_file": source_file,
                "schema_name": schema_name,
                "extraction_id": extraction_id,
                "evaluation_id": evaluation_id,
                "metadata": {}
            }

            # Insert into 'llm_history' table (use upsert to handle duplicates)
            result = self.client.table("llm_history").upsert(
                data, on_conflict="call_hash").execute()

            if result.data:
                record_id = result.data[0].get("id")
                return record_id
            return None

        except Exception as e:
            # Silently fail to avoid disrupting the main pipeline
            # print(f"⚠️ Error saving LLM history to Supabase: {e}")
            return None

    async def save_workflow_state(
        self,
        thread_id: str,
        workflow_state: Dict[str, Any],
        metadata: Optional[Dict] = None
    ) -> Optional[str]:
        """
        Save workflow state to Supabase for human review resume.

        Args:
            thread_id: Unique thread ID for the workflow
            workflow_state: Complete workflow state dict
            metadata: Optional metadata about the workflow

        Returns:
            UUID of inserted/updated record or None if failed
        """
        if not self.is_available():
            return None

        try:
            data = {
                "thread_id": thread_id,
                "workflow_state": workflow_state,
                "saved_at": datetime.now().isoformat(),
                "metadata": metadata or {}
            }

            # Upsert to handle updates to same thread_id
            result = self.client.table("workflow_states").upsert(
                data, on_conflict="thread_id").execute()

            if result.data:
                record_id = result.data[0].get("id")
                print(
                    f"✓ Saved workflow state to Supabase (thread: {thread_id})")
                return record_id
            return None

        except Exception as e:
            print(f"❌ Error saving workflow state to Supabase: {e}")
            return None

    def get_workflow_state(
        self,
        thread_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve workflow state from Supabase.

        Args:
            thread_id: Unique thread ID for the workflow

        Returns:
            Workflow state dict or None if not found
        """
        if not self.is_available():
            return None

        try:
            result = self.client.table("workflow_states").select(
                "*").eq("thread_id", thread_id).execute()

            if result.data and len(result.data) > 0:
                print(
                    f"✓ Retrieved workflow state from Supabase (thread: {thread_id})")
                return result.data[0].get("workflow_state")
            else:
                print(f"⚠️ No workflow state found for thread: {thread_id}")
                return None

        except Exception as e:
            print(f"❌ Error retrieving workflow state from Supabase: {e}")
            return None


# Global Supabase client instance
_supabase_client: Optional[SupabaseClient] = None


def get_supabase_client() -> Optional[SupabaseClient]:
    """Get or create the global Supabase client instance."""
    global _supabase_client

    if _supabase_client is None:
        _supabase_client = SupabaseClient()
    return _supabase_client
