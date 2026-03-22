import asyncio
import json
import aiofiles
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

from core.config import DEFAULT_OUTPUT_DIR, DEFAULT_CSV_DIR, DEFAULT_JSON_DIR
from utils.supabase_client import get_supabase_client


class AsyncMedicalFileHandler:
    """Async file handler for medical data extraction pipeline."""

    def __init__(self, default_output_dir: str = DEFAULT_OUTPUT_DIR, default_csv_dir: str = DEFAULT_CSV_DIR,
                 default_json_dir: str = DEFAULT_JSON_DIR,
                 csv_filename: str = "patient_population_evaluation_results.csv",
                 json_filename: str = "patient_population_evaluation_results.json",
                 schema_name: str = "patient_population"):
        self.default_output_dir = default_output_dir
        self.default_csv_dir = default_csv_dir
        self.default_json_dir = default_json_dir
        self.csv_filename = csv_filename
        self.json_filename = json_filename
        self.schema_name = schema_name
        self.supabase_client = get_supabase_client()

    def _generate_output_filename(self, source_file_path: str) -> str:
        """Generate output filename from source filename."""
        source_path = Path(source_file_path)
        source_name = source_path.stem
        suffix = f"_{self.schema_name}" if self.schema_name else ""

        if source_name.endswith('_md'):
            output_name = source_name[:-3] + suffix
        else:
            output_name = source_name + suffix

        return output_name + '.json'

    async def save_extracted_results(self, extracted_records: List[Dict],
                                     source_file_path: str,
                                     output_dir: str = None,
                                     override: bool = False) -> str:
        """Save extracted results to JSON file asynchronously."""
        try:
            output_filename = self._generate_output_filename(source_file_path)

            if output_dir is None:
                if self.default_output_dir:
                    output_dir = Path(self.default_output_dir)
                else:
                    output_dir = Path(source_file_path).parent
            else:
                output_dir = Path(output_dir)

            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / output_filename

            if output_path.exists() and not override:
                return None

            save_data = {
                "metadata": {
                    "source_file": str(source_file_path),
                    "extraction_timestamp": datetime.now().isoformat(),
                    "total_records": len(extracted_records),
                    "pipeline_version": "DSPy_Async_1.0"
                },
                "extracted_records": extracted_records
            }

            async with aiofiles.open(output_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(save_data, indent=2, ensure_ascii=False))

            print(
                f"Successfully saved {len(extracted_records)} records to: {output_path}")

            # Optionally save to Supabase
            if self.supabase_client and self.supabase_client.is_available():
                metadata = {
                    "output_file": str(output_path),
                    "pipeline_version": "DSPy_Async_1.0"
                }
                await self.supabase_client.save_extracted_records(
                    extracted_records=extracted_records,
                    source_file=source_file_path,
                    schema_name=self.schema_name,
                    metadata=metadata
                )

            return str(output_path)

        except Exception as e:
            print(f"Error saving results: {e}")
            return None

    async def save_evaluation_to_csv(self, baseline_results: List[Dict], ground_truth: List[Dict],
                                     source_file: str, matches: List[tuple], csv_dir: str = None,
                                     override: bool = False):
        """Save evaluation results to CSV asynchronously using streaming append."""
        csv_dir = csv_dir or self.default_csv_dir
        Path(csv_dir).mkdir(parents=True, exist_ok=True)
        csv_path = Path(csv_dir) / self.csv_filename

        # Prepare data rows
        rows = []
        matched_gt_indices = set()
        matched_ext_indices = set()
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        for ext_idx, gt_idx, score in matches:
            if score >= 0.5:
                matched_gt_indices.add(gt_idx)
                matched_ext_indices.add(ext_idx)

                # Ground truth row (TP - correctly found)
                gt_row = ground_truth[gt_idx].copy()
                gt_row.update({
                    'data_type': 'ground_truth',
                    'source_file': source_file,
                    'match_score': score,
                    'pair_id': f"{source_file}_{gt_idx}",
                    'classification': 'TP',
                    'timestamp': timestamp
                })
                rows.append(gt_row)

                # Extracted row (TP - correct extraction)
                ext_row = baseline_results[ext_idx].copy()
                ext_row.update({
                    'data_type': 'extracted',
                    'source_file': source_file,
                    'match_score': score,
                    'pair_id': f"{source_file}_{gt_idx}",
                    'classification': 'TP',
                    'timestamp': timestamp
                })
                rows.append(ext_row)

        # Add unmatched ground truth (FN)
        for gt_idx, gt_record in enumerate(ground_truth):
            if gt_idx not in matched_gt_indices:
                row = gt_record.copy()
                row.update({
                    'data_type': 'ground_truth',
                    'source_file': source_file,
                    'match_score': 0.0,
                    'pair_id': f"{source_file}_{gt_idx}_missing",
                    'classification': 'FN',
                    'timestamp': timestamp
                })
                rows.append(row)

        # Add unmatched extractions (FP)
        for ext_idx, ext_record in enumerate(baseline_results):
            if ext_idx not in matched_ext_indices:
                row = ext_record.copy()
                row.update({
                    'data_type': 'extracted',
                    'source_file': source_file,
                    'match_score': 0.0,
                    'pair_id': f"{source_file}_fp_{ext_idx}",
                    'classification': 'FP',
                    'timestamp': timestamp
                })
                rows.append(row)

        # Save to CSV asynchronously
        new_df = pd.DataFrame(rows)

        if new_df.empty:
            return str(csv_path)

        if override and csv_path.exists():
            # Rare path: drop existing rows for this source_file, then rewrite once
            async with aiofiles.open(csv_path, 'r', encoding='utf-8') as f:
                existing_content = await f.read()
            existing_df = pd.read_csv(pd.io.common.StringIO(existing_content))
            filtered_df = existing_df[existing_df['source_file']
                                      != source_file]
            final_df = pd.concat([filtered_df, new_df], ignore_index=True)
            csv_content = final_df.to_csv(index=False)
            async with aiofiles.open(csv_path, 'w', encoding='utf-8') as f:
                await f.write(csv_content)
        else:
            csv_exists = csv_path.exists()
            csv_content = new_df.to_csv(index=False, header=not csv_exists)
            async with aiofiles.open(csv_path, 'a', encoding='utf-8') as f:
                await f.write(csv_content)

        # Optionally save evaluation details to Supabase
        if self.supabase_client and self.supabase_client.is_available():
            await self.supabase_client.save_evaluation_details(
                baseline_results=baseline_results,
                ground_truth=ground_truth,
                matches=matches,
                source_file=source_file,
                schema_name=self.schema_name
            )

        return str(csv_path)

    async def save_evaluation_to_json(self, evaluation_results: Dict, source_file: str, json_path: str = None):
        """Save evaluation results to JSON file asynchronously."""
        if json_path:
            json_path_obj = Path(json_path)
        else:
            json_path_obj = Path(self.default_json_dir) / self.json_filename
        json_path = str(json_path_obj)

        new_entry = {
            "source_file": source_file,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            **{k: v for k, v in evaluation_results.items() if k != 'field_accuracies'}
        }

        # Ensure directory exists
        json_path_obj.parent.mkdir(parents=True, exist_ok=True)

        # Load existing data or create empty list
        data = []
        if json_path_obj.exists():
            async with aiofiles.open(json_path, 'r') as f:
                content = await f.read()
                try:
                    data = json.loads(content) if content.strip() else []
                except json.JSONDecodeError:
                    data = []

        # Check if source_file already exists and replace/append
        existing_index = next((i for i, entry in enumerate(
            data) if entry.get('source_file') == source_file), None)

        if existing_index is not None:
            data[existing_index] = new_entry
        else:
            data.append(new_entry)
            print(f"Added new results for {source_file}")

        # Save asynchronously
        async with aiofiles.open(json_path, 'w') as f:
            await f.write(json.dumps(data, indent=2))

        # Optionally save to Supabase
        if self.supabase_client and self.supabase_client.is_available():
            await self.supabase_client.save_evaluation_metrics(
                evaluation_results=evaluation_results,
                source_file=source_file,
                schema_name=self.schema_name
            )

        return json_path

    async def run_and_save(self, pipeline, markdown_content: str, source_file_path: str,
                           output_dir: str = None, override: bool = False):
        """Run pipeline and save results asynchronously."""
        prediction = await pipeline.forward(markdown_content)
        extracted_records = prediction if isinstance(
            prediction, list) else prediction.extracted_records

        result_path = await self.save_extracted_results(
            extracted_records, source_file_path, output_dir, override
        )
        return result_path
