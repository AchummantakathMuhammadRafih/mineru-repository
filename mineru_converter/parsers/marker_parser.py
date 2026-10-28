"""Marker PDF Parser integration for MinerU pipeline."""

from pathlib import Path
from typing import Optional, Tuple
import os
import shutil
import time

from mineru_converter.utils.logging_utils import get_logger

logger = get_logger()


def is_marker_available() -> bool:
    """Check if marker-pdf or its CLI/library is installed and importable."""
    try:
        import marker  # noqa: F401
        return True
    except ImportError:
        # Also check if marker command-line tool exists in PATH
        return shutil.which("marker_single") is not None or shutil.which("marker") is not None


def convert_pdf_with_marker(
    pdf_path: Path,
    output_dir: Path,
    max_pages: Optional[int] = None,
    langs: Optional[str] = None,
) -> Tuple[Path, Optional[Path]]:
    """
    Parse a PDF using marker-pdf into Markdown.
    
    Returns:
        (markdown_path, images_dir_or_none)
    """
    pdf_path = Path(pdf_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    doc_stem = pdf_path.stem

    # Attempt 1: Direct Python API if marker package is importable
    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.output import text_from_rendered

        logger.info(f"🚀 [Marker Parser] Using Python API to parse {pdf_path.name}...")
        converter = PdfConverter(
            artifact_dict=create_model_dict(),
        )
        rendered = converter(str(pdf_path))
        text, _, images = text_from_rendered(rendered)

        md_path = output_dir / f"{doc_stem}.md"
        md_path.write_text(text, encoding="utf-8")

        images_dir = output_dir / "images"
        if images:
            images_dir.mkdir(parents=True, exist_ok=True)
            for img_name, img_data in images.items():
                img_file = images_dir / img_name
                try:
                    img_data.save(str(img_file))
                except Exception:
                    pass

        return md_path, images_dir if images else None

    except ImportError:
        pass
    except Exception as api_err:
        logger.warning(f"Marker Python API failed: {api_err}. Trying CLI fallback...")

    # Attempt 2: CLI fallback (marker_single or marker)
    marker_cmd = shutil.which("marker_single") or shutil.which("marker")
    if marker_cmd:
        import subprocess

        logger.info(f"🚀 [Marker Parser] Using CLI command '{marker_cmd}' for {pdf_path.name}...")
        cmd = [marker_cmd, str(pdf_path), "--output_dir", str(output_dir)]
        if langs:
            cmd.extend(["--langs", langs])

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Marker CLI failed with exit code {res.returncode}: {res.stderr}")

        # Locate output markdown
        possible_md = [
            output_dir / f"{doc_stem}.md",
            output_dir / doc_stem / f"{doc_stem}.md",
        ]
        for p in possible_md:
            if p.exists():
                return p, output_dir / "images" if (output_dir / "images").exists() else None

    raise ImportError(
        "marker-pdf is not installed or available. "
        "Please install it using: pip install marker-pdf"
    )
