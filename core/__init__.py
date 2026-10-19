"""
eviStreams Core Module

Production-ready core module for DSPy code generation and extraction.
Provides structured logging, exception handling, and configuration management.
"""

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logging(
    level: str = "INFO",
    log_dir: Optional[Path] = None,
    log_file: str = "core.log",
    format_string: Optional[str] = None
) -> None:
    """
    Setup structured logging for core module.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files (if None, only console logging)
        log_file: Name of log file
        format_string: Custom format string (uses default if None)

    Example:
        >>> from core import setup_logging
        >>> setup_logging(level="DEBUG", log_dir=Path("logs"))
    """
    # Default format with timestamp, logger name, level, and message
    if format_string is None:
        format_string = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Create formatter
    formatter = logging.Formatter(format_string)

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Clear existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Console handler (always enabled)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper()))
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (optional)
    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / log_file

        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setLevel(getattr(logging, level.upper()))
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        root_logger.info(f"Logging initialized: level={level}, file={log_path}")
    else:
        root_logger.info(f"Logging initialized: level={level}, console only")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a module.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance

    Example:
        >>> from core import get_logger
        >>> logger = get_logger(__name__)
        >>> logger.info("Hello from core module")
    """
    return logging.getLogger(name)


# Initialize with default settings (can be reconfigured by applications)
setup_logging(level="INFO")


__all__ = [
    "setup_logging",
    "get_logger",
]
