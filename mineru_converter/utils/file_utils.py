"""File system utilities for discovery, hashing, formatting, and path manipulation."""

import hashlib
import os
import re
from pathlib import Path
from typing import List, Union

from mineru_converter.config.constants import SUPPORTED_DOC_EXTENSIONS, SUPPORTED_PDF_EXTENSIONS


def find_input_files(
    target: Union[str, Path],
    recursive: bool = True,
    only_pdf: bool = False
) -> List[Path]:
    """
    Discover all matching document files from a file path or directory.
    
    Args:
        target: File or directory path.
        recursive: Whether to search subdirectories recursively.
        only_pdf: If True, only return PDF files. Otherwise returns all supported doc formats.
        
    Returns:
        List of Path objects sorted alphabetically.
    """
    path = Path(target).resolve()
    valid_exts = SUPPORTED_PDF_EXTENSIONS if only_pdf else SUPPORTED_DOC_EXTENSIONS

    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")

    if path.is_file():
        if path.suffix.lower() not in valid_exts:
            raise ValueError(
                f"Unsupported file format '{path.suffix}'. Expected one of: {sorted(list(valid_exts))}"
            )
        return [path]

    if path.is_dir():
        files: List[Path] = []
        pattern = "**/*" if recursive else "*"
        for item in path.glob(pattern):
            if item.is_file() and item.suffix.lower() in valid_exts:
                files.append(item.resolve())
        return sorted(files)

    return []


def compute_file_sha256(file_path: Union[str, Path], chunk_size: int = 65536) -> str:
    """Compute SHA256 checksum of a file."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            sha.update(chunk)
    return sha.hexdigest()


def format_file_size(size_bytes: int) -> str:
    """Format bytes into human-readable representation."""
    if size_bytes < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_idx = 0
    val = float(size_bytes)
    while val >= 1024.0 and unit_idx < len(units) - 1:
        val /= 1024.0
        unit_idx += 1
    return f"{val:.2f} {units[unit_idx]}" if unit_idx > 0 else f"{int(val)} B"


def sanitize_filename(filename: str) -> str:
    """Sanitize string to be safe for directory / filenames across Windows and Linux."""
    # Replace invalid chars with single underscore and collapse
    sanitized = re.sub(r'[\\/*?:"<>|]+', "_", filename)
    sanitized = re.sub(r"[\s_]+", "_", sanitized).strip("._")
    return sanitized or "document"


def ensure_directory(path: Union[str, Path]) -> Path:
    """Ensure directory exists and return Path object."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
