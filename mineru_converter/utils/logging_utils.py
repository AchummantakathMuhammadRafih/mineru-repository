"""Logging configuration and Rich console helpers."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional, Union

from rich.console import Console
from rich.logging import RichHandler

console = Console(color_system="auto", stderr=False)
err_console = Console(color_system="auto", stderr=True)

LOGGER_NAME = "mineru_converter"


def add_file_handler_to_loggers(
    log_file: Union[str, Path],
    logger_names: Optional[List[str]] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    level: int = logging.INFO,
) -> RotatingFileHandler:
    """
    Attach a thread-safe rotating file handler to the specified loggers
    (defaults to mineru_converter, uvicorn, uvicorn.access, and uvicorn.error).
    """
    log_path = Path(log_file).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    names = logger_names or [LOGGER_NAME, "uvicorn", "uvicorn.error"]
    for name in names:
        l = logging.getLogger(name)
        # Avoid duplicate handlers for this exact file
        already_has = any(
            isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")).resolve() == log_path
            for h in l.handlers
        )
        if not already_has:
            l.addHandler(file_handler)

    # Explicitly silence uvicorn.access so HTTP request access logs do not pollute server.log
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.setLevel(logging.WARNING)

    return file_handler


def setup_logger(
    verbose: bool = False,
    quiet: bool = False,
    log_file: Optional[Union[str, Path]] = None,
) -> logging.Logger:
    """
    Configure and return application logger with Rich formatting and optional rotating file logging.
    
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
        add_file_handler_to_loggers(log_file, logger_names=[LOGGER_NAME], level=logging.DEBUG if verbose else logging.INFO)

    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    """Retrieve current package logger."""
    return logging.getLogger(LOGGER_NAME)

