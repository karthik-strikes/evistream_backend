"""
Base processor class for PDF processing.
"""

from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class BaseProcessor:
    """Base class for all processors."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the base processor.

        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        self._validate_config()
        logger.debug(f"{self.__class__.__name__} initialized")

    def _validate_config(self) -> None:
        """
        Validate processor configuration.
        Should be overridden by subclasses.
        """
        pass

    def process(self, content: str, **kwargs) -> Dict[str, Any]:
        """
        Process the content.
        Must be implemented by subclasses.

        Args:
            content: Content to process
            **kwargs: Additional arguments

        Returns:
            Dict: Processing results
        """
        raise NotImplementedError("Subclasses must implement process()")

    def get_capabilities(self) -> list:
        """
        Get list of processing capabilities.
        Should be overridden by subclasses.

        Returns:
            List of capability strings
        """
        return []














