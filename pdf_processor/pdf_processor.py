"""
PDF processor using the marker PDF parser with unique file naming.
"""

from .base import BaseProcessor
import os
import json
import hashlib
import logging
import sys
import time
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from datetime import datetime
import base64
import requests
from diskcache import Cache
from dotenv import load_dotenv
from requests.exceptions import RequestException

# Load environment variables from .env file
load_dotenv()

# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()

# API Configuration
DATALAB_API_KEY = os.getenv("DATALAB_API_KEY")
DATALAB_API_BASE_URL = os.getenv(
    "DATALAB_API_BASE_URL", "https://www.datalab.to/api/v1")

# Directory Configuration
CACHE_DIR = Path(os.getenv("CACHE_DIR", PROJECT_ROOT / "cache"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", PROJECT_ROOT /
                  "output" / "extracted_pdfs"))
LOG_DIR = Path(os.getenv("LOG_DIR", PROJECT_ROOT / "logs"))
ERROR_LOG = Path(os.getenv("ERROR_LOG", LOG_DIR / "error_log_parser.txt"))

# Cost Configuration
COST_TRACKING_FILE = Path(
    os.getenv("COST_TRACKING_FILE", PROJECT_ROOT / "logs" / "cost_tracking.json"))
MAX_COST_THRESHOLD = float(os.getenv("MAX_COST_THRESHOLD", "500.0"))
COST_PER_1000_PAGES = float(os.getenv("COST_PER_1000_PAGES", "3.0"))

# Processing Configuration
DEFAULT_MAX_WORKERS = int(os.getenv("DEFAULT_MAX_WORKERS", "10"))
DEFAULT_TIMEOUT = int(os.getenv("DEFAULT_TIMEOUT", "60"))
DEFAULT_MAX_RETRIES = int(os.getenv("DEFAULT_MAX_RETRIES", "5"))
DEFAULT_MAX_POLLS = int(os.getenv("DEFAULT_MAX_POLLS", "300"))


class CostExceededException(Exception):
    pass


class DataLabAPIError(Exception):
    pass


class PDFParserConfig:
    def __init__(self):
        self.api_key = DATALAB_API_KEY
        self.cache_dir = str(CACHE_DIR)
        self.timeout = DEFAULT_TIMEOUT
        self.max_retries = DEFAULT_MAX_RETRIES
        self.max_polls = DEFAULT_MAX_POLLS
        self.max_workers = DEFAULT_MAX_WORKERS
        self.error_log_path = str(ERROR_LOG)
        self.max_cost_threshold = MAX_COST_THRESHOLD
        if not self.api_key:
            raise ValueError(
                "DATALAB_API_KEY environment variable is required")


class CostTracker:
    def __init__(self, cost_file: str = None, cost_per_1000_pages: float = None):
        self.cost_file = cost_file or str(COST_TRACKING_FILE)
        self.cost_per_1000_pages = cost_per_1000_pages or COST_PER_1000_PAGES
        self.max_cost_threshold = MAX_COST_THRESHOLD
        self.logger = logging.getLogger(__name__)
        self.cost_data = self._load_cost_data()
        self.session_pages = 0
        self.session_cost = 0.0
        self.logger.info(
            f"Cost tracker initialized. Previous total: ${self.cost_data['total_cost']:.2f}"
        )

    def _load_cost_data(self) -> Dict:
        if os.path.exists(self.cost_file):
            try:
                with open(self.cost_file, 'r') as f:
                    data = json.load(f)
                if not all(key in data for key in ['total_pages', 'total_cost', 'sessions']):
                    raise ValueError("Invalid cost data structure")
                return data
            except Exception as e:
                self.logger.warning(
                    f"Error loading cost data, starting fresh: {e}")
        return {'total_pages': 0, 'total_cost': 0.0, 'sessions': []}

    def _save_cost_data(self):
        try:
            os.makedirs(os.path.dirname(self.cost_file) if os.path.dirname(
                self.cost_file) else '.', exist_ok=True)
            with open(self.cost_file, 'w') as f:
                json.dump(self.cost_data, f, indent=2)
        except Exception as e:
            self.logger.error(f"Error saving cost data: {e}")

    def calculate_cost(self, page_count: int) -> float:
        if page_count <= 0:
            return 0.0
        return (page_count / 1000.0) * self.cost_per_1000_pages

    def add_pages(self, page_count: int, doc_id: str = None) -> float:
        if page_count <= 0:
            return 0.0
        page_cost = self.calculate_cost(page_count)
        projected_total = self.cost_data['total_cost'] + \
            self.session_cost + page_cost
        if projected_total > self.max_cost_threshold:
            raise CostExceededException(
                f"Adding {page_count} pages (${page_cost:.2f}) would exceed cost threshold of ${self.max_cost_threshold}. "
                f"Current total: ${self.cost_data['total_cost'] + self.session_cost:.2f}, "
                f"Projected total: ${projected_total:.2f}"
            )
        self.session_pages += page_count
        self.session_cost += page_cost
        doc_info = f" for {doc_id}" if doc_id else ""
        self.logger.info(
            f"Added {page_count} pages{doc_info} (${page_cost:.2f}). "
            f"Session total: ${self.session_cost:.2f}, "
            f"Overall total: ${self.cost_data['total_cost'] + self.session_cost:.2f}"
        )
        return page_cost

    def finalize_session(self, session_name: str = None) -> Dict:
        if self.session_pages == 0:
            return {
                'session_pages': 0,
                'session_cost': 0.0,
                'total_pages': self.cost_data['total_pages'],
                'total_cost': self.cost_data['total_cost']
            }
        session_record = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'name': session_name or f"Session_{len(self.cost_data['sessions']) + 1}",
            'pages': self.session_pages,
            'cost': self.session_cost
        }
        self.cost_data['total_pages'] += self.session_pages
        self.cost_data['total_cost'] += self.session_cost
        self.cost_data['sessions'].append(session_record)
        self._save_cost_data()
        session_summary = {
            'session_pages': self.session_pages,
            'session_cost': self.session_cost,
            'total_pages': self.cost_data['total_pages'],
            'total_cost': self.cost_data['total_cost']
        }
        self.session_pages = 0
        self.session_cost = 0.0
        return session_summary

    def get_current_totals(self) -> Dict:
        return {
            'session_pages': self.session_pages,
            'session_cost': self.session_cost,
            'total_pages': self.cost_data['total_pages'],
            'total_cost': self.cost_data['total_cost'],
            'running_total_pages': self.cost_data['total_pages'] + self.session_pages,
            'running_total_cost': self.cost_data['total_cost'] + self.session_cost,
            'remaining_budget': self.max_cost_threshold - (self.cost_data['total_cost'] + self.session_cost)
        }

    def get_cost_summary(self) -> Dict:
        totals = self.get_current_totals()
        return {
            'current_session': {'pages': totals['session_pages'], 'cost': totals['session_cost']},
            'historical_total': {'pages': totals['total_pages'], 'cost': totals['total_cost']},
            'running_total': {'pages': totals['running_total_pages'], 'cost': totals['running_total_cost']},
            'budget_info': {
                'threshold': self.max_cost_threshold,
                'remaining': totals['remaining_budget'],
                'percentage_used': (totals['running_total_cost'] / self.max_cost_threshold) * 100
            },
            'recent_sessions': self.cost_data['sessions'][-5:]
        }

    def check_threshold(self) -> bool:
        current_total = self.cost_data['total_cost'] + self.session_cost
        return current_total >= self.max_cost_threshold

    def get_remaining_budget(self) -> float:
        current_total = self.cost_data['total_cost'] + self.session_cost
        return max(0, self.max_cost_threshold - current_total)


class CacheManager:
    def __init__(self, cache_dir: str = None):
        self.cache = Cache(cache_dir or str(CACHE_DIR))

    def make_key(self, endpoint: str, file_path: str) -> str:
        with open(file_path, "rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()
        return f"{endpoint}_md::{file_hash}"

    def get(self, key: str) -> Optional[Dict]:
        return self.cache.get(key)

    def set(self, key: str, data: Dict) -> None:
        self.cache.set(key, data)

    def __contains__(self, key: str) -> bool:
        return key in self.cache


class DataLabAPIClient:
    def __init__(self, config: PDFParserConfig, cache_manager: CacheManager):
        self.config = config
        self.cache_manager = cache_manager
        self.logger = logging.getLogger(__name__)

    def _make_api_request(self, endpoint: str, file_path: str) -> Tuple[str, Dict]:
        url = f"{DATALAB_API_BASE_URL}/{endpoint}"
        with open(file_path, "rb") as file:
            form_data = {"file": (os.path.basename(
                file_path), file, "application/pdf")}
            if endpoint == "marker":
                form_data["output_format"] = (None, "markdown")
                form_data["paginate"] = (None, "true")  # Add page separators
                # Keep headers and footers in output
                additional_config = {
                    "keep_pageheader_in_output": True,
                    "keep_pagefooter_in_output": True
                }
                form_data["additional_config"] = (None, json.dumps(additional_config))
            headers = {"X-Api-Key": self.config.api_key}
            for attempt in range(self.config.max_retries):
                try:
                    response = requests.post(
                        url, files=form_data, headers=headers, timeout=self.config.timeout)
                    response.raise_for_status()
                    break
                except RequestException as e:
                    if attempt == self.config.max_retries - 1:
                        raise DataLabAPIError(
                            f"API call failed after {self.config.max_retries} attempts: {e}") from e
                    time.sleep(2 ** attempt)
        try:
            response_data = response.json()
            check_url = response_data["request_check_url"]
        except (KeyError, json.JSONDecodeError) as e:
            raise DataLabAPIError(
                f"Invalid response from Datalab API: {e}") from e
        return check_url, headers

    def _poll_results(self, check_url: str, headers: Dict, cache_key: str) -> Dict:
        for _ in range(self.config.max_polls):
            time.sleep(1)
            try:
                response = requests.get(
                    check_url, headers=headers, timeout=self.config.timeout)
                response.raise_for_status()
                data = response.json()
            except (RequestException, json.JSONDecodeError) as e:
                raise DataLabAPIError(f"Error polling results: {e}") from e
            status = data.get("status")
            if status == "complete":
                self.cache_manager.set(cache_key, data)
                return data
            elif status not in {"processing", "pending"}:
                raise DataLabAPIError(f"Unexpected API status: {status}")
        raise TimeoutError(
            f"API request timed out after {self.config.max_polls} polls")

    def call_api(self, endpoint: str, file_path: str) -> Dict:
        cache_key = self.cache_manager.make_key(endpoint, file_path)
        cached_result = self.cache_manager.get(cache_key)
        if cached_result:
            return cached_result
        check_url, headers = self._make_api_request(endpoint, file_path)
        return self._poll_results(check_url, headers, cache_key)


MARKER_PARSER_AVAILABLE = True


logger = logging.getLogger(__name__)


class PDFProcessor(BaseProcessor):
    """PDF processor using the existing marker PDF parser."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the PDF processor."""
        super().__init__(config)

        # Extract configuration
        self.extract_images = config.get(
            "extract_images", False) if config else False
        self.cache_dir = config.get(
            "cache_dir", "cache") if config else "cache"
        self.output_dir = config.get(
            "output_dir", "output") if config else "output"

        # Initialize marker parser components
        self._initialize_marker_parser()

        logger.info("PDFProcessor initialized with marker parser")

    def _validate_config(self) -> None:
        """Validate PDF processor configuration."""
        pass  # All validation is now handled by the embedded classes

    def _initialize_marker_parser(self):
        """Initialize the marker PDF parser components."""
        try:
            # Create configuration
            self.parser_config = PDFParserConfig()

            # Initialize components
            self.cache_manager = CacheManager(self.cache_dir)
            self.api_client = DataLabAPIClient(
                self.parser_config, self.cache_manager)
            self.cost_tracker = CostTracker()

            # Create output directory
            os.makedirs(self.output_dir, exist_ok=True)

        except Exception as e:
            logger.error(f"Error initializing marker parser: {e}")
            raise

    def process(self, content: str, force_reprocess: bool = False) -> Dict[str, Any]:
        """
        Process PDF content using the marker parser.
        First checks for existing results to avoid re-running Marker API.

        Args:
            content: Path to the PDF file
            force_reprocess: If True, skip cache and re-process the PDF

        Returns:
            Dict: Processed PDF data
        """
        try:
            if not os.path.exists(content):
                raise FileNotFoundError(f"PDF file not found: {content}")

            logger.debug(f"Processing PDF: {content}")

            # Check if we already have processed results (unless force reprocess is requested)
            if not force_reprocess:
                existing_result = self._check_existing_result(content)
                if existing_result:
                    logger.info(
                        f"Using existing PDF processing result for: {content}")
                    return existing_result
            else:
                logger.info(f"Force reprocessing PDF: {content}")

            # Generate unique filename for output
            unique_filename = self._generate_unique_filename(content)

            # Parse PDF using marker parser (only if not cached)
            result = self._parse_pdf_with_marker(content, unique_filename)

            logger.debug(f"PDF processing completed: {content}")
            return result

        except Exception as e:
            logger.error(f"Error processing PDF {content}: {str(e)}")
            raise

    def _check_existing_result(self, pdf_path: str) -> Optional[Dict[str, Any]]:
        """
        Check if we already have a processed result for this PDF.

        Returns existing result if found, None otherwise.
        """
        try:
            with open(pdf_path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()[:16]
            expected_filename = f"{file_hash}_md"

            # Check if output directory with this name exists
            output_path = os.path.join(self.output_dir, expected_filename)
            result_file = os.path.join(
                output_path, f"{expected_filename}.json")

            if os.path.exists(result_file):
                logger.debug(f"Found existing result: {result_file}")

                # Load and validate the existing result
                with open(result_file, 'r', encoding='utf-8') as f:
                    existing_result = json.load(f)

                # Verify the result has the required structure
                # Note: pdf_path is intentionally not checked here — the hash-based
                # folder already guarantees content identity, so a renamed/moved PDF
                # with the same bytes should still hit the cache.
                if (existing_result.get("status") == "success" and
                        "marker" in existing_result):
                    return existing_result
                else:
                    logger.warning(
                        f"Existing result file invalid or corrupted: {result_file}")

            return None

        except Exception as e:
            logger.warning(f"Error checking for existing result: {e}")
            return None

    def _generate_unique_filename(self, pdf_path: str) -> str:
        """Generate filename with _md suffix for the output, keyed on content hash."""
        with open(pdf_path, "rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()[:16]
        filename = f"{file_hash}_md"
        return filename

    def _parse_pdf_with_marker(self, pdf_path: str, unique_filename: str) -> Dict[str, Any]:
        """Parse PDF using the marker parser."""
        try:
            # Call the marker API
            marker_results = self.api_client.call_api("marker", pdf_path)

            # Create result structure
            result = {
                "id": hashlib.md5(pdf_path.encode()).hexdigest(),
                "pdf_path": pdf_path,
                "unique_filename": unique_filename,
                "marker": marker_results,
                "status": "success",
                "processing_timestamp": datetime.now().isoformat()
            }

            # Save result with unique filename
            self._save_result_with_unique_name(result, unique_filename)

            # Save images if requested and available
            if self.extract_images and marker_results.get("images"):
                self._save_images(marker_results["images"], unique_filename)

            return result

        except Exception as e:
            logger.error(f"Error parsing PDF with marker: {e}")
            return {
                "id": hashlib.md5(pdf_path.encode()).hexdigest(),
                "pdf_path": pdf_path,
                "unique_filename": unique_filename,
                "marker": {},
                "status": f"error: {str(e)}",
                "processing_timestamp": datetime.now().isoformat()
            }

    def _save_result_with_unique_name(self, result: Dict[str, Any], unique_filename: str):
        """Save result as filename_md.json."""
        try:
            # Create output directory structure
            output_path = os.path.join(self.output_dir, unique_filename)
            os.makedirs(output_path, exist_ok=True)

            # Save as filename_md.json
            result_file = os.path.join(output_path, f"{unique_filename}.json")

            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            logger.debug(f"Saved extraction result to: {result_file}")

        except Exception as e:
            logger.error(f"Error saving result: {e}")
            raise

    def _save_images(self, images_dict: Dict[str, str], unique_filename: str):
        """Save base64-encoded images with unique naming."""
        try:
            # Create images directory
            images_dir = os.path.join(
                self.output_dir, unique_filename, "images")
            os.makedirs(images_dir, exist_ok=True)

            for key, b64_data in images_dict.items():
                if not b64_data or not str(b64_data).strip():
                    continue

                try:
                    # Sanitize filename
                    safe_key = key.strip("/").replace("/", "_")
                    # add extension
                    image_filename = f"{unique_filename}_{safe_key}.jpeg"
                    image_path = os.path.join(images_dir, image_filename)

                    # Convert base64 string → bytes
                    if isinstance(b64_data, str):
                        img_bytes = base64.b64decode(b64_data)
                    else:
                        img_bytes = b64_data  # assume already bytes

                    # Save image
                    with open(image_path, "wb") as f:
                        f.write(img_bytes)

                    logger.debug(f"Saved image: {image_path}")

                except Exception as e:
                    logger.error(f"Error saving image {key}: {e}")

        except Exception as e:
            logger.error(f"Error saving images: {e}")

    def get_capabilities(self) -> List[str]:
        """Get list of processing capabilities."""
        capabilities = ["pdf_parsing", "markdown_extraction"]

        if self.extract_images:
            capabilities.append("image_extraction")

        return capabilities

    def get_cost_info(self) -> Dict[str, Any]:
        """Get cost information from the cost tracker."""
        if hasattr(self, 'cost_tracker'):
            return self.cost_tracker.get_cost_summary()
        return {"error": "Cost tracker not available"}

    def load_existing_result(self, pdf_path: str) -> Optional[Dict[str, Any]]:
        """
        Load existing PDF processing result without re-processing.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            Existing result if found, None otherwise
        """
        return self._check_existing_result(pdf_path)

    def get_processing_status(self, pdf_path: str) -> Dict[str, Any]:
        """Get processing status for a specific PDF."""
        try:
            existing_result = self._check_existing_result(pdf_path)

            if existing_result:
                with open(pdf_path, "rb") as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()[:16]
                expected_filename = f"{file_hash}_md"
                result_file = os.path.join(
                    self.output_dir, expected_filename, f"{expected_filename}.json")

                return {
                    "status": "processed",
                    "result_file": result_file,
                    "unique_filename": expected_filename,
                    "data": existing_result
                }

            return {"status": "not_processed"}

        except Exception as e:
            logger.error(f"Error getting processing status: {e}")
            return {"status": "error", "error": str(e)}

    def list_cached_results(self) -> List[Dict[str, Any]]:
        """
        List all cached PDF processing results.

        Returns:
            List of cached result summaries
        """
        cached_results = []

        try:
            if not os.path.exists(self.output_dir):
                return cached_results

            for item in os.listdir(self.output_dir):
                item_path = os.path.join(self.output_dir, item)
                if os.path.isdir(item_path) and item.endswith('_md'):
                    result_file = os.path.join(item_path, f"{item}.json")

                    if os.path.exists(result_file):
                        try:
                            with open(result_file, 'r', encoding='utf-8') as f:
                                result_data = json.load(f)

                            # Extract summary information
                            summary = {
                                "unique_filename": item,
                                "pdf_path": result_data.get("pdf_path", ""),
                                "status": result_data.get("status", "unknown"),
                                "processing_timestamp": result_data.get("processing_timestamp", ""),
                                "result_file": result_file,
                                "has_markdown": bool(result_data.get("marker", {}).get("markdown")),
                                "has_images": bool(result_data.get("marker", {}).get("images")),
                                "file_size": os.path.getsize(result_file)
                            }

                            cached_results.append(summary)

                        except Exception as e:
                            logger.warning(
                                f"Error reading cached result {result_file}: {e}")

            # Sort by processing timestamp (newest first)
            cached_results.sort(key=lambda x: x.get(
                "processing_timestamp", ""), reverse=True)

        except Exception as e:
            logger.error(f"Error listing cached results: {e}")

        return cached_results
