"""
Simple wrapper for PDFProcessor to use in Streamlit MVP.
Provides a clean interface for uploading and processing PDFs.
"""

import os
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional
import streamlit as st

from .pdf_processor import PDFProcessor


class StreamlitPDFProcessor:
    """
    Wrapper for PDFProcessor that works well with Streamlit.
    Handles file uploads, temporary storage, and result presentation.
    """

    def __init__(self, output_dir: str = None, cache_dir: str = "cache"):
        """
        Initialize the processor for Streamlit.

        Args:
            output_dir: Directory to store extracted markdown
            cache_dir: Directory for caching API results
        """
        self.output_dir = output_dir or os.environ.get(
            "NOTEBOOK_DIR",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "notebooks")
        )
        self.cache_dir = cache_dir

        # Create directories
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(cache_dir, exist_ok=True)

        # Initialize processor with config
        config = {
            "extract_images": False,  # Set to True if needed
            "cache_dir": cache_dir,
            "output_dir": output_dir
        }

        self.processor = PDFProcessor(config)

    def process_uploaded_file(self, uploaded_file, force_reprocess: bool = False) -> Dict[str, Any]:
        """
        Process an uploaded PDF file from Streamlit.

        Args:
            uploaded_file: Streamlit UploadedFile object
            force_reprocess: If True, skip cache and reprocess

        Returns:
            Dict with processing results
        """
        # Save uploaded file to temporary location
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_path = tmp_file.name

        try:
            # Process the PDF
            result = self.processor.process(
                tmp_path, force_reprocess=force_reprocess)

            # Add original filename to result
            result['original_filename'] = uploaded_file.name

            return result

        finally:
            # Clean up temporary file
            try:
                os.unlink(tmp_path)
            except:
                pass

    def process_pdf_path(self, pdf_path: str, force_reprocess: bool = False) -> Dict[str, Any]:
        """
        Process a PDF from a file path.

        Args:
            pdf_path: Path to PDF file
            force_reprocess: If True, skip cache and reprocess

        Returns:
            Dict with processing results
        """
        return self.processor.process(pdf_path, force_reprocess=force_reprocess)

    def get_markdown_content(self, result: Dict[str, Any]) -> Optional[str]:
        """
        Extract markdown content from processing result.

        Args:
            result: Processing result from process_uploaded_file or process_pdf_path

        Returns:
            Markdown content or None if not available
        """
        if result.get('status') != 'success':
            return None

        marker_data = result.get('marker', {})
        return marker_data.get('markdown', '')

    def get_cost_summary(self) -> Dict[str, Any]:
        """
        Get cost tracking information.

        Returns:
            Dict with cost information
        """
        return self.processor.get_cost_info()

    def list_processed_pdfs(self) -> list:
        """
        List all previously processed PDFs.

        Returns:
            List of processed PDF summaries
        """
        return self.processor.list_cached_results()

    def check_if_processed(self, pdf_path: str) -> Optional[Dict[str, Any]]:
        """
        Check if a PDF has already been processed.

        Args:
            pdf_path: Path to PDF file

        Returns:
            Existing result if found, None otherwise
        """
        return self.processor.load_existing_result(pdf_path)


# Streamlit-specific helper functions

def display_processing_result(result: Dict[str, Any]):
    """Display processing result in Streamlit UI."""
    if result.get('status') == 'success':
        st.success(
            f"✅ Successfully processed: {result.get('original_filename', 'PDF')}")

        # Show markdown preview
        with st.expander("📄 Markdown Preview (first 500 characters)"):
            markdown = result.get('marker', {}).get('markdown', '')
            st.code(markdown[:500] + "..." if len(markdown)
                    > 500 else markdown)

        # Show metadata
        with st.expander("ℹ️ Processing Metadata"):
            st.json({
                'unique_filename': result.get('unique_filename'),
                'processing_timestamp': result.get('processing_timestamp'),
                'pdf_path': result.get('pdf_path')
            })
    else:
        st.error(
            f"❌ Processing failed: {result.get('status', 'Unknown error')}")


def display_cost_info(cost_info: Dict[str, Any]):
    """Display cost tracking information in Streamlit UI."""
    st.subheader("💰 Cost Tracking")

    budget_info = cost_info.get('budget_info', {})
    running_total = cost_info.get('running_total', {})

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Total Cost",
            f"${running_total.get('cost', 0):.2f}"
        )

    with col2:
        st.metric(
            "Pages Processed",
            running_total.get('pages', 0)
        )

    with col3:
        st.metric(
            "Budget Remaining",
            f"${budget_info.get('remaining', 0):.2f}"
        )

    # Progress bar
    percentage_used = budget_info.get('percentage_used', 0)
    st.progress(min(percentage_used / 100, 1.0))
    st.caption(
        f"Budget: {percentage_used:.1f}% used of ${budget_info.get('threshold', 0):.2f}")
