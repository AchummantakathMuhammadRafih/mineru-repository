"""Logging configuration and Rich console helpers."""

import logging
import sys
from typing import Optional
from rich.console import Console
from rich.logging import RichHandler

console = Console(color_system="auto", stderr=False)
err_console = Console(color_system="auto", stderr=True)

LOGGER_NAME = "mineru_converter"


def setup_logger(
    verbose: bool = False,
    quiet: bool = False,
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    Configure and return application logger with Rich formatting.
    
    Args:
        verbose: If True, set level to DEBUG.
        quiet: If True, suppress INFO and only show WARNING/ERROR.
        log_file: Optional path to write plain text log file.
    """
    logger = logging.getLogger(LOGGER_NAME)
    
    # Avoid duplicate handlers on re-init
    if logger.handlers:
        logger.handlers.clear()

    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    logger.setLevel(level)

    # Rich console handler
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_level=True,
        show_path=verbose,
        markup=True,
        rich_tracebacks=True,
        tracebacks_show_locals=verbose,
    )
    rich_handler.setLevel(level)
    logger.addHandler(rich_handler)

    # Optional file logger
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    """Retrieve current package logger."""
    return logging.getLogger(LOGGER_NAME)
