"""Utility to package conversion output artifacts along with the original document into a ZIP archive."""

import io
import zipfile
from pathlib import Path
from typing import Optional


def create_job_zip(
    output_dir: Path,
    original_file_path: Optional[Path] = None,
) -> bytes:
    """
    Creates an in-memory ZIP containing all files in `output_dir` (including subfolders like images/)
    and includes the original input file.
    
    Args:
        output_dir: The directory containing MinerU output artifacts.
        original_file_path: The original input document path.
        
    Returns:
        bytes: Raw zip file content ready for download or disk write.
    """
    output_dir = Path(output_dir)
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. Include the original file if available
        if original_file_path and Path(original_file_path).exists():
            orig_p = Path(original_file_path)
            zf.write(orig_p, arcname=f"original/{orig_p.name}")

        # 2. Include all generated output artifacts recursively
        if output_dir.exists() and output_dir.is_dir():
            for p in sorted(output_dir.rglob("*")):
                if p.is_file():
                    # Preserve relative path inside the zip
                    rel_path = p.relative_to(output_dir)
                    zf.write(p, arcname=f"outputs/{rel_path}")

    buffer.seek(0)
    return buffer.getvalue()
