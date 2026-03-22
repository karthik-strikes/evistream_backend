import dspy
import math
import asyncio
import json
import re
import hashlib
import datetime
import diskcache as dc
from typing import Dict, List, Any, Tuple
from collections import defaultdict
import numpy as np
from scipy.optimize import linear_sum_assignment
from sklearn.metrics import cohen_kappa_score
from dspy_components.utility_signatures import SemanticMatcher
from core.config import EVALUATION_MODEL, EVALUATION_TEMPERATURE
from utils.lm_config import get_dspy_model
from core.field_extractor import extract_fields_from_signature
from pathlib import Path


class AsyncMedicalExtractionEvaluator:
    """Async evaluator for medical data extraction with DSPy-based semantic matching and caching."""

    def __init__(self,
                 signature_class: Any = None,
                 output_field_name: str = None,
                 field_cache_file: str = None,
                 target_file: str = None,
                 required_fields: List[str] = None,
                 semantic_fields: List[str] = None,
                 exact_fields: List[str] = None,
                 groupable_patterns: Dict[str, Dict] = None,
                 use_semantic: bool = True,
                 max_concurrent: int = 10,
                 cache_dir: str = "."):
        """
        Initialize evaluator. Can be initialized either with direct field lists OR
        with signature_class + cache config to auto-load fields.

        Args:
            signature_class: DSPy signature class (for auto-extraction)
            output_field_name: Name of the output field in signature
            field_cache_file: Path to JSON file to cache/load field configs
            target_file: Path to target JSON file (for auto-extraction sampling)
            required_fields: Explicit list (optional if using signature)
            semantic_fields: Explicit list (optional if using signature)
            exact_fields: Explicit list (optional if using signature)
            groupable_patterns: Explicit dict (optional if using signature)
            use_semantic: Whether to use semantic matching
            max_concurrent: Max concurrent semantic similarity calls
            cache_dir: Directory for disk caches
        """
        # Logic to load fields if not provided explicitly
        if required_fields is None:
            if not all([signature_class, output_field_name, field_cache_file]):
                raise ValueError(
                    "Must provide either explicit fields OR (signature_class, output_field_name, field_cache_file)")

            self.required_fields, self.semantic_fields, self.exact_fields, self.groupable_patterns = \
                self._load_or_extract_fields(
                    signature_class, output_field_name, field_cache_file, target_file)
        else:
            self.required_fields = required_fields
            self.semantic_fields = semantic_fields or []
            self.exact_fields = exact_fields or []
            self.groupable_patterns = groupable_patterns or {}

        self.use_semantic = use_semantic
        from core.config import EVALUATION_CONCURRENCY
        effective_concurrency = max_concurrent or EVALUATION_CONCURRENCY
        self.semaphore = asyncio.Semaphore(effective_concurrency)

        self.exact_fields = list(
            set(self.exact_fields) - {'Ref_ID', 'filename'})

        all_defined = set(self.semantic_fields) | set(self.exact_fields)
        required_set = set(self.required_fields)

        if all_defined != required_set:
            missing = required_set - all_defined
            extra = all_defined - required_set
            if missing or extra:
                print(f"WARNING: Field mismatch - Missing: {missing}, Extra: {extra}")

        # Persistent caches with configurable directory
        import os
        semantic_cache_path = os.path.abspath(
            os.path.join(cache_dir, ".semantic_cache"))
        matching_cache_path = os.path.abspath(
            os.path.join(cache_dir, ".evaluation_cache"))

        # Now use those variables to create the caches
        self._semantic_cache = dc.Cache(semantic_cache_path)
        self._matching_cache = dc.Cache(matching_cache_path)

        # Initialize LLM once for reuse
        self._lm = get_dspy_model(
            model_name=EVALUATION_MODEL,
            temperature=EVALUATION_TEMPERATURE
        ) if use_semantic else None

    def _load_or_extract_fields(self, signature_class, output_field_name, field_cache_file, target_file):
        """Load fields from cache or extract from signature."""
        cache_path = Path(field_cache_file)

        # Try loading from cache first
        if cache_path.exists():
            try:
                with open(cache_path, 'r') as f:
                    data = json.load(f)
                print(f"Loaded field config from {cache_path}")
                return (
                    data.get("required_fields", []),
                    data.get("semantic_fields", []),
                    data.get("exact_fields", []),
                    data.get("groupable_patterns", {})
                )
            except Exception as e:
                print(
                    f"Error loading cache {cache_path}: {e}. Regenerating...")

        # If no cache or error, extract using DSPy
        if not target_file:
            raise ValueError(
                f"Cache {field_cache_file} not found and no target_file provided for generation.")

        print(f"Generating field config from signature...")
        req, sem, exact, group = extract_fields_from_signature(
            signature_class=signature_class,
            target_file=target_file,
            output_field_name=output_field_name,
            verbose=True
        )

        # Save to cache
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, 'w') as f:
            json.dump({
                "required_fields": req,
                "semantic_fields": sem,
                "exact_fields": exact,
                "groupable_patterns": group
            }, f, indent=2)
        print(f"Saved field config to {cache_path}")

        return req, sem, exact, group

    def close(self):
        """Cleanly close the disk caches."""
        self._semantic_cache.close()
        self._matching_cache.close()

    async def get_matches_and_aligned_records(
        self,
        extracted_records: List[Dict],
        ground_truth_records: List[Dict]
    ) -> Tuple[List[Tuple[int, int, float]], Dict[Tuple[int, int], Dict]]:
        """
        Get Hungarian matches AND the aligned record pairs used for comparison.

        This method returns both the matching results and the actual aligned records
        that were compared. This is useful for:
        - Saving aligned records to CSV
        - Debugging alignment issues
        - Understanding why records matched or didn't match
        """
        if not extracted_records or not ground_truth_records:
            return [], {}

        matches, comparison_cache = await self._run_comparison_pipeline(
            extracted_records, ground_truth_records
        )

        # Return both matches and the aligned records
        return matches, comparison_cache

    async def _run_dspy_semantic_match(self, text1: str, text2: str, field_name: str) -> bool:
        try:
            with dspy.context(lm=self._lm):
                matcher = dspy.ChainOfThought(SemanticMatcher)
                result = matcher(
                    text1=text1,
                    text2=text2,
                    field_context=field_name
                )

            is_equiv = bool(result.is_equivalent)
            return is_equiv

        except Exception as e:
            print(f"SEMANTIC MATCH ERROR - Field: {field_name}, Error: {type(e).__name__}: {e}")
            return False

    async def semantic_similarity(self, text1: str, text2: str, field_name: str) -> float:
        """Calculate semantic similarity (0/1) with caching."""
        if not self.use_semantic or not text1.strip() or not text2.strip():
            return 1.0 if text1.strip() == text2.strip() else 0.0

        t1_norm = " ".join(text1.strip().lower().split())
        t2_norm = " ".join(text2.strip().lower().split())
        text_pair = tuple(sorted([t1_norm, t2_norm]))

        raw_key = f"v2_{text_pair[0]}||{text_pair[1]}||{field_name}"
        cache_key = hashlib.sha256(raw_key.encode()).hexdigest()
        if cache_key in self._semantic_cache:
            return self._semantic_cache[cache_key]

        is_equiv = await self._run_dspy_semantic_match(text1, text2, field_name)
        score = 1.0 if is_equiv else 0.0

        self._semantic_cache.set(cache_key, score, expire=86400)
        return score

    async def field_match_score(self, extracted_value: Any, ground_truth_value: Any, field_name: str) -> float:
        """
        Calculate match score between extracted and ground truth values with robust normalization.
        """

        ext_val = self.normalize_value(extracted_value, field_name)
        gt_val = self.normalize_value(ground_truth_value, field_name)

        if self.is_empty(ext_val) and self.is_empty(gt_val):
            return 1.0

        if self.is_empty(ext_val) or self.is_empty(gt_val):
            return 0.0

        if not self.use_semantic or field_name in self.exact_fields:
            return 1.0 if ext_val == gt_val else 0.0

        if field_name in self.semantic_fields:
            return await self.semantic_similarity(ext_val, gt_val, field_name)

        return 1.0 if ext_val == gt_val else 0.0

    def normalize_value(self, value: Any, field_name: str) -> str:
        """Normalize values for comparison with field-specific rules."""
        val = str(value).strip() if value is not None else ""
        val = val.lower()
        val = re.sub(r'\s+', ' ', val)
        field_name_lower = field_name.lower()

        numeric_suffixes = ('_n', '_percent', '_tendency', '_variability', '_randomized')
        if field_name_lower.endswith(numeric_suffixes):
            val = self.normalize_numeric(val)
        elif 'filename' in field_name_lower:
            val = re.sub(r'^_+', '', val)
        elif 'range' in field_name_lower:
            val = self.normalize_range(val)

        return val.rstrip('.,;')

    def normalize_range(self, value: str) -> str:
        """Normalize range values to a consistent format."""
        if not value:
            return value
        normalized = re.sub(r'\s*-\s*', ',', value)
        normalized = re.sub(r'\s*,\s*', ',', normalized)
        return normalized

    def normalize_numeric(self, value: str) -> str:
        """Convert numeric value to nearest integer."""
        if not value:
            return value
        try:
            return str(round(float(value)))
        except (ValueError, TypeError):
            return value

    def is_empty(self, value: Any) -> bool:
        """Check if a value is considered empty for evaluation purposes."""
        if value is None:
            return True
        if isinstance(value, list):
            return len(value) == 0
        if isinstance(value, (int, float)):
            return False
        if isinstance(value, str):
            empty_indicators = [
                '', 'none', 'n/a', 'na', 'not reported',
                'not available', 'nr', 'unknown', 'unclear'
            ]
            return value.strip().lower() in empty_indicators
        return not bool(value)

    def _extract_group_items(self, record: Dict, pattern: str, all_fields: List[str], max_slots: int) -> List[Dict]:
        """Extract group items from a record based on pattern."""
        prefix = pattern.split("_{i}_")[0]
        items = []
        for i in range(1, max_slots + 1):
            item = {"_original_index": i}
            has_data = False
            for field in all_fields:
                field_name = f"{prefix}_{i}_{field}"
                value = record.get(field_name, "")
                item[field] = value
                if value:
                    has_data = True
            if has_data:
                items.append(item)
        return items

    async def _calculate_group_similarity(self, ext_item: Dict, gt_item: Dict,
                                          key_fields: List[str], pattern: str) -> float:
        """Calculate similarity between two group items based on key fields."""
        scores = []
        base_prefix = pattern.split('_{i}_')[0]

        for key_field in key_fields:
            ext_val = ext_item.get(key_field, "")
            gt_val = gt_item.get(key_field, "")
            logical_field_name = f"{base_prefix}_1_{key_field}"
            score = await self.field_match_score(ext_val, gt_val, logical_field_name)
            scores.append(score)

        return sum(scores) / len(scores) if scores else 0.0

    async def _match_group_items(self, ext_items: List[Dict], gt_items: List[Dict],
                                 key_fields: List[str], pattern: str) -> List[Dict]:
        """Use Hungarian algorithm to match group items."""
        if not ext_items or not gt_items:
            return []

        n_ext = len(ext_items)
        n_gt = len(gt_items)
        similarity_matrix = np.zeros((n_ext, n_gt))

        for i, ext_item in enumerate(ext_items):
            for j, gt_item in enumerate(gt_items):
                similarity = await self._calculate_group_similarity(
                    ext_item, gt_item, key_fields, pattern
                )
                similarity_matrix[i, j] = similarity

        cost_matrix = 1.0 - similarity_matrix
        row_indices, col_indices = linear_sum_assignment(cost_matrix)

        matches = []
        for i, j in zip(row_indices, col_indices):
            if similarity_matrix[i, j] > 0.5:
                matches.append({
                    "extracted_index": ext_items[i]["_original_index"],
                    "gt_index": gt_items[j]["_original_index"],
                    "similarity": similarity_matrix[i, j]
                })
        return matches

    def _remap_record_fields(self, record: Dict, matches: List[Dict],
                             ext_items: List[Dict], pattern: str,
                             all_fields: List[str], max_slots: int) -> Dict:
        """Remap fields in record based on matches."""
        prefix = pattern.split("_{i}_")[0]
        slot_mapping = {}

        for match in matches:
            ext_slot = match["extracted_index"]
            gt_slot = match["gt_index"]
            slot_mapping[ext_slot] = gt_slot

        used_slots = set(slot_mapping.values())
        matched_ext_slots = set(match["extracted_index"] for match in matches)
        remaining_slots = [i for i in range(
            1, max_slots + 1) if i not in used_slots]
        unmatched_ext_slots = [item["_original_index"] for item in ext_items
                               if item["_original_index"] not in matched_ext_slots]

        for ext_slot, new_slot in zip(unmatched_ext_slots, remaining_slots):
            slot_mapping[ext_slot] = new_slot

        new_record = {}
        for field_name, value in record.items():
            if field_name.startswith(f"{prefix}_"):
                try:
                    parts = field_name.split("_", 2)
                    if len(parts) == 3:
                        old_slot = int(parts[1])
                        suffix = parts[2]
                        if old_slot in slot_mapping:
                            new_slot = slot_mapping[old_slot]
                            new_field_name = f"{prefix}_{new_slot}_{suffix}"
                            new_record[new_field_name] = value
                        else:
                            new_record[field_name] = value
                    else:
                        new_record[field_name] = value
                except (ValueError, IndexError):
                    new_record[field_name] = value
            else:
                new_record[field_name] = value
        return new_record

    async def align_groupable_fields(self, extracted_record: Dict, ground_truth_record: Dict) -> Tuple[Dict, Dict]:
        """Align groupable fields (like interventions) between records before comparison."""
        if not self.groupable_patterns:
            return extracted_record, ground_truth_record

        aligned_extracted = extracted_record.copy()
        aligned_gt = ground_truth_record.copy()

        for group_name, config in self.groupable_patterns.items():
            pattern = config.get("pattern", "")
            key_fields = config.get("key_matching_fields", [])
            all_fields = config.get("all_fields", [])
            max_slots = config.get("max_slots", 0)

            if not pattern or not key_fields or not all_fields or not max_slots:
                continue

            ext_items = self._extract_group_items(
                aligned_extracted, pattern, all_fields, max_slots)
            gt_items = self._extract_group_items(
                aligned_gt, pattern, all_fields, max_slots)

            if not ext_items or not gt_items:
                continue

            matches = await self._match_group_items(ext_items, gt_items, key_fields, pattern)
            if not matches:
                continue

            aligned_extracted = self._remap_record_fields(
                aligned_extracted, matches, ext_items, pattern, all_fields, max_slots
            )

        return aligned_extracted, aligned_gt

    async def _run_comparison_pipeline(self, extracted_records: List[Dict], ground_truth_records: List[Dict]):
        """
        UNIFIED PIPELINE: Align + Compare + Match

        Args:
            extracted_records: List of extracted records
            ground_truth_records: List of ground truth records

        Returns:
            Tuple of (matches, comparison_cache)
        """
        if not extracted_records or not ground_truth_records:
            return [], {}

        # Check disk cache first
        def create_stable_cache_key(records):
            sorted_records = [dict(sorted(record.items()))
                              for record in records]
            json_str = json.dumps(sorted_records, sort_keys=True, default=str)
            return hashlib.sha256(json_str.encode()).hexdigest()

        ext_hash = create_stable_cache_key(extracted_records)
        gt_hash = create_stable_cache_key(ground_truth_records)
        cache_key = f"v2_{len(extracted_records)}_{len(ground_truth_records)}_{ext_hash}_{gt_hash}"

        if cache_key in self._matching_cache:
            return self._matching_cache[cache_key]

        n_extracted = len(extracted_records)
        n_ground_truth = len(ground_truth_records)

        # Compare all pairs
        pairs_to_compare = [(i, j) for i in range(n_extracted)
                            for j in range(n_ground_truth)]

        comparison_cache = {}
        similarity_matrix = np.zeros((n_extracted, n_ground_truth))

        # Build comprehensive comparison cache with alignment integrated
        async def compute_pair_with_semaphore(i, j):
            """Compute full comparison for a single (i,j) pair."""
            async with self.semaphore:
                ext_rec = extracted_records[i]
                gt_rec = ground_truth_records[j]

                # STEP 1: Align groupable fields FIRST
                if self.groupable_patterns:
                    aligned_ext, aligned_gt = await self.align_groupable_fields(ext_rec, gt_rec)
                else:
                    aligned_ext, aligned_gt = ext_rec, gt_rec

                # STEP 2: Compare all fields ONCE
                field_scores = {}
                for field in self.required_fields:
                    ext_val = aligned_ext.get(field)
                    gt_val = aligned_gt.get(field)
                    score = await self.field_match_score(ext_val, gt_val, field)
                    field_scores[field] = score

                # STEP 3: Calculate overall similarity
                overall_sim = sum(field_scores.values()) / \
                    len(field_scores) if field_scores else 0.0

                return {
                    'aligned_extracted': aligned_ext,
                    'aligned_ground_truth': aligned_gt,
                    'field_scores': field_scores,
                    'similarity': overall_sim
                }

        # Execute all pair comparisons concurrently
        tasks = [compute_pair_with_semaphore(
            i, j) for i, j in pairs_to_compare]
        results = await asyncio.gather(*tasks)

        # Build cache and matrix
        for idx, (i, j) in enumerate(pairs_to_compare):
            comparison_cache[(i, j)] = results[idx]
            similarity_matrix[i, j] = results[idx]['similarity']

        # STEP 4: Run Hungarian matching ONCE
        cost_matrix = 1.0 - similarity_matrix
        row_idx, col_idx = linear_sum_assignment(cost_matrix)
        matches = [(i, j, similarity_matrix[i, j]) for i, j in zip(
            row_idx, col_idx) if similarity_matrix[i, j] > 0.0]

        # Cache the results with 24h TTL
        pipeline_results = (matches, comparison_cache)
        self._matching_cache.set(cache_key, pipeline_results, expire=86400)

        return pipeline_results

    def _calculate_record_metrics(self, matches: List[Tuple], extracted_records: List[Dict], ground_truth_records: List[Dict]) -> Dict:
        """Calculate precision/recall/F1 from cached matches."""
        valid_matches = [m for m in matches if m[2] >= 0.70]

        TP = len(valid_matches)
        FP = len(extracted_records) - TP
        FN = len(ground_truth_records) - TP

        precision = TP / (TP + FP) if TP + FP > 0 else 0.0
        recall = TP / (TP + FN) if TP + FN > 0 else 0.0
        f1 = 2 * precision * recall / \
            (precision + recall) if (precision + recall) > 0 else 0.0

        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "TP": TP,
            "FP": FP,
            "FN": FN
        }

    def _calculate_cohens_kappa(self, matches: List[Tuple], comparison_cache: Dict) -> float:
        """Calculate Cohen's Kappa from cached field scores."""
        if not matches:
            return 0.0

        extracted_values = []
        ground_truth_values = []

        for ext_idx, gt_idx, _ in matches:
            cache_entry = comparison_cache.get((ext_idx, gt_idx))
            if not cache_entry:
                continue

            aligned_ext = cache_entry['aligned_extracted']
            aligned_gt = cache_entry['aligned_ground_truth']

            for field in self.exact_fields:
                if field in aligned_ext and field in aligned_gt:
                    ext_val = str(aligned_ext[field]).strip().lower()
                    gt_val = str(aligned_gt[field]).strip().lower()
                    extracted_values.append(f"{field}::{ext_val}")
                    ground_truth_values.append(f"{field}::{gt_val}")

        if not extracted_values:
            return 0.0

        all_unique_values = set(extracted_values) | set(ground_truth_values)
        value_to_code = {val: idx for idx,
                         val in enumerate(sorted(all_unique_values))}

        extracted_coded = [value_to_code[val] for val in extracted_values]
        ground_truth_coded = [value_to_code[val] for val in ground_truth_values]

        kappa = cohen_kappa_score(ground_truth_coded, extracted_coded)
        return kappa

    def _calculate_field_counts(self, matches: List[Tuple], comparison_cache: Dict,
                                extracted_records: List[Dict], ground_truth_records: List[Dict]) -> Dict:
        """Calculate field-level counts from cached comparison data."""
        field_counts = defaultdict(lambda: {
            'gt_count': 0,
            'extracted_count': 0,
            'matched': 0,
            'missing': 0,
            'incorrect': 0,
            'extra': 0
        })

        for ext_idx, gt_idx, _ in matches:
            cache_entry = comparison_cache.get((ext_idx, gt_idx))
            if not cache_entry:
                continue

            aligned_ext = cache_entry['aligned_extracted']
            aligned_gt = cache_entry['aligned_ground_truth']
            field_scores = cache_entry['field_scores']

            for field in self.required_fields:
                gt_value = aligned_gt.get(field)
                extracted_value = aligned_ext.get(field)

                gt_has_value = not self.is_empty(gt_value)
                extracted_has_value = not self.is_empty(extracted_value)

                if gt_has_value:
                    field_counts[field]['gt_count'] += 1
                if extracted_has_value:
                    field_counts[field]['extracted_count'] += 1

                if gt_has_value and extracted_has_value:
                    match_score = field_scores.get(field, 0.0)
                    if match_score >= 0.70:
                        field_counts[field]['matched'] += 1
                    else:
                        field_counts[field]['incorrect'] += 1
                elif gt_has_value and not extracted_has_value:
                    field_counts[field]['missing'] += 1
                elif not gt_has_value and extracted_has_value:
                    field_counts[field]['extra'] += 1

        # Handle unmatched records
        matched_gt_indices = {gt_idx for _, gt_idx, _ in matches}
        for gt_idx, gt_rec in enumerate(ground_truth_records):
            if gt_idx not in matched_gt_indices:
                for field in self.required_fields:
                    gt_value = gt_rec.get(field)
                    if not self.is_empty(gt_value):
                        field_counts[field]['gt_count'] += 1
                        field_counts[field]['missing'] += 1

        matched_ext_indices = {ext_idx for ext_idx, _, _ in matches}
        for ext_idx, ext_rec in enumerate(extracted_records):
            if ext_idx not in matched_ext_indices:
                for field in self.required_fields:
                    extracted_value = ext_rec.get(field)
                    if not self.is_empty(extracted_value):
                        field_counts[field]['extracted_count'] += 1
                        field_counts[field]['extra'] += 1

        total_gt = sum(c['gt_count'] for c in field_counts.values())
        total_matched = sum(c['matched'] for c in field_counts.values())

        print("Total Missed Fields: ", sum(
            c['missing'] for c in field_counts.values()))
        print("Total Incorrect Fields: ", sum(
            c['incorrect'] for c in field_counts.values()))
        print("List of Missed Fields: ", [
              field for field, c in field_counts.items() if c['missing'] > 0])
        print("List of Incorrect Fields: ", [
              field for field, c in field_counts.items() if c['incorrect'] > 0])
        if total_gt > 0:
            print(
                f"Overall Field Accuracy: {(total_matched / total_gt) * 100:.2f}% ({total_matched}/{total_gt})")

        return dict(field_counts)

    def evaluate_completeness(self, extracted_records: List[Dict]) -> float:
        """Calculate completeness: % of required fields present in extracted records."""
        if not extracted_records:
            return 0.0
        total = len(self.required_fields) * len(extracted_records)
        present = sum(
            1 for r in extracted_records for f in self.required_fields if f in r)
        return present / total if total > 0 else 0.0

    async def evaluate_accuracy(self, extracted_records: List[Dict], ground_truth: List[Dict]) -> Dict:
        """
        Calculate all accuracy metrics.
        """
        if not extracted_records or not ground_truth:
            return {
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0,
                "completeness": 0.0
            }

        # Run comparison pipeline
        matches, comparison_cache = await self._run_comparison_pipeline(extracted_records, ground_truth)

        # Derive all metrics from cached results
        record_metrics = self._calculate_record_metrics(
            matches, extracted_records, ground_truth)

        try:
            kappa = self._calculate_cohens_kappa(matches, comparison_cache)
        except Exception:
            kappa = None

        result = {
            **record_metrics,
            "completeness": self.evaluate_completeness(extracted_records),
        }
        if kappa is not None:
            result["cohens_kappa"] = kappa
        return result

    async def evaluate(self, extracted_records: List[Dict], ground_truth: List[Dict] = None,
                       diagnose: bool = False) -> Dict[str, Any]:
        """Main evaluation entry point."""
        results = {
            "num_extracted": len(extracted_records),
            "completeness": self.evaluate_completeness(extracted_records),
            "semantic_enabled": self.use_semantic,
        }

        if ground_truth:
            acc = await self.evaluate_accuracy(extracted_records, ground_truth)
            results.update(acc)
            results["num_ground_truth"] = len(ground_truth)

        return results

    async def calculate_field_counts(self, extracted_records: List[Dict], ground_truth_records: List[Dict]) -> Dict:
        """Calculate field counts using cached comparison results."""
        if not extracted_records or not ground_truth_records:
            return {}

        matches, comparison_cache = await self._run_comparison_pipeline(
            extracted_records, ground_truth_records
        )

        return self._calculate_field_counts(matches, comparison_cache, extracted_records, ground_truth_records)


def medical_extraction_metric_async(example, pred, trace=None,
                                    required_fields=None,
                                    semantic_fields=None,
                                    exact_fields=None,
                                    groupable_patterns=None):
    """Wrapper function for DSPy optimizer."""
    import asyncio

    if required_fields is None or semantic_fields is None or exact_fields is None:
        raise ValueError(
            "required_fields, semantic_fields, and exact_fields must be provided")

    evaluator = AsyncMedicalExtractionEvaluator(
        required_fields=required_fields,
        semantic_fields=semantic_fields,
        exact_fields=exact_fields,
        groupable_patterns=groupable_patterns,
        use_semantic=True
    )

    try:
        if not hasattr(pred, 'extracted_records'):
            raise AttributeError(
                "pred object missing 'extracted_records' attribute")
        if not hasattr(example, 'extracted_records'):
            raise AttributeError(
                "example object missing 'extracted_records' attribute")

        extracted_records = pred.extracted_records
        ground_truth = example.extracted_records

        try:
            loop = asyncio.get_running_loop()
            try:
                import nest_asyncio
                nest_asyncio.apply()
            except ImportError:
                pass
            results = loop.run_until_complete(
                evaluator.evaluate_accuracy(extracted_records, ground_truth)
            )
        except RuntimeError:
            results = asyncio.run(evaluator.evaluate_accuracy(
                extracted_records, ground_truth))

        return results['f1']

    finally:
        evaluator.close()
