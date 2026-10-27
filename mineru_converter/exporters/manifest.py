"""Manifest builder and output artifact tracker."""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from mineru_converter.config.constants import (
    MANIFEST_FILE_NAME,
    SUFFIX_CONTENT_LIST_V1,
    SUFFIX_CONTENT_LIST_V2,
    SUFFIX_LAYOUT_PDF,
    SUFFIX_MARKDOWN,
    SUFFIX_MIDDLE_JSON,
    SUFFIX_MODEL_JSON,
    SUFFIX_ORIGIN_PDF,
    SUFFIX_SPAN_PDF,
    IMAGES_DIR_NAME,
)
from mineru_converter.models.schemas import DocumentStats, OutputFileManifest
from mineru_converter.utils.file_utils import compute_file_sha256, format_file_size, ensure_directory
from mineru_converter.utils.logging_utils import get_logger

logger = get_logger()


def build_output_manifest(
    output_directory: Path,
    doc_stem: str
) -> OutputFileManifest:
    """
    Locate and catalog all generated MinerU files inside the conversion directory.
    
    Args:
        output_directory: The directory where MinerU placed output artifacts.
        doc_stem: The base document name (without extension).
        
    Returns:
        Populated OutputFileManifest instance.
    """
    manifest = OutputFileManifest(
        document_name=doc_stem,
        output_directory=output_directory
    )

    # Resolve Markdown
    md_file = output_directory / f"{doc_stem}{SUFFIX_MARKDOWN}"
    if md_file.exists():
        manifest.markdown_path = md_file

    # Resolve Content List V1
    cl1_file = output_directory / f"{doc_stem}{SUFFIX_CONTENT_LIST_V1}"
    if cl1_file.exists():
        manifest.content_list_v1_path = cl1_file

    # Resolve Content List V2
    cl2_file = output_directory / f"{doc_stem}{SUFFIX_CONTENT_LIST_V2}"
    if cl2_file.exists():
        manifest.content_list_v2_path = cl2_file

    # Resolve Corrected Content List V2
    cl2_corr_file = output_directory / f"{doc_stem}_content_list_v2_corrected.json"
    if cl2_corr_file.exists():
        manifest.corrected_content_list_v2_path = cl2_corr_file

    # Resolve Clean Markdown
    clean_md_file = output_directory / f"{doc_stem}_clean.md"
    if clean_md_file.exists():
        manifest.clean_markdown_path = clean_md_file

    # Resolve Redacted Markdown
    redacted_md_file = output_directory / f"{doc_stem}_redacted.md"
    if redacted_md_file.exists():
        manifest.redacted_markdown_path = redacted_md_file


    # Resolve Middle JSON
    mid_file = output_directory / f"{doc_stem}{SUFFIX_MIDDLE_JSON}"
    if mid_file.exists():
        manifest.middle_json_path = mid_file

    # Resolve Model JSON
    model_file = output_directory / f"{doc_stem}{SUFFIX_MODEL_JSON}"
    if model_file.exists():
        manifest.model_json_path = model_file

    # Resolve Layout PDF
    layout_file = output_directory / f"{doc_stem}{SUFFIX_LAYOUT_PDF}"
    if layout_file.exists():
        manifest.layout_pdf_path = layout_file

    # Resolve Span PDF
    span_file = output_directory / f"{doc_stem}{SUFFIX_SPAN_PDF}"
    if span_file.exists():
        manifest.span_pdf_path = span_file

    # Resolve Origin PDF
    origin_file = output_directory / f"{doc_stem}{SUFFIX_ORIGIN_PDF}"
    if origin_file.exists():
        manifest.origin_pdf_path = origin_file

    # Resolve Manifest & Summary files
    manifest_file = output_directory / MANIFEST_FILE_NAME
    if manifest_file.exists():
        manifest.manifest_path = manifest_file

    summary_file = output_directory / "conversion_summary.json"
    if summary_file.exists():
        manifest.summary_path = summary_file

    # Resolve Images Directory
    images_dir = output_directory / IMAGES_DIR_NAME
    if images_dir.exists() and images_dir.is_dir():
        manifest.images_dir = images_dir
        manifest.image_files = sorted(
            [p for p in images_dir.iterdir() if p.is_file()]
        )

    return manifest


def calculate_document_stats(
    manifest: OutputFileManifest,
    input_file: Path,
    total_pages: int = 0,
    processed_pages: int = 0,
    duration_seconds: float = 0.0
) -> DocumentStats:
    """Calculate detailed document statistics from generated artifacts."""
    # Attempt to load from existing manifest file if zero
    if manifest.manifest_path and manifest.manifest_path.exists():
        try:
            with open(manifest.manifest_path, "r", encoding="utf-8") as mf:
                m_data = json.load(mf)
            conv_info = m_data.get("conversion", {})
            if total_pages == 0:
                total_pages = conv_info.get("pages_total", 0)
            if processed_pages == 0:
                processed_pages = conv_info.get("pages_processed", 0)
            if duration_seconds == 0.0:
                duration_seconds = conv_info.get("duration_seconds", 0.0)
        except Exception:
            pass

    stats = DocumentStats(
        total_document_pages=total_pages,
        processed_pages=processed_pages,
        conversion_duration_seconds=duration_seconds,
        input_file_size_bytes=input_file.stat().st_size if input_file.exists() else 0,
    )

    # Compute total output size
    total_out_size = 0
    for file_path in manifest.all_files():
        try:
            total_out_size += file_path.stat().st_size
        except Exception:
            pass
    stats.output_total_size_bytes = total_out_size
    stats.image_count = len(manifest.image_files)

    # Inspect Markdown content for text stats
    if manifest.markdown_path and manifest.markdown_path.exists():
        try:
            content = manifest.markdown_path.read_text(encoding="utf-8", errors="replace")
            stats.character_count = len(content)
            stats.word_count = len(content.split())
            stats.heading_count = sum(1 for line in content.splitlines() if line.strip().startswith("#"))
        except Exception as exc:
            logger.debug(f"Error analyzing markdown file stats: {exc}")

    # Inspect Middle JSON for structural counts (tables, formulas, blocks)
    if manifest.middle_json_path and manifest.middle_json_path.exists():
        try:
            with open(manifest.middle_json_path, "r", encoding="utf-8") as f:
                middle_data = json.load(f)

            pdf_info = middle_data.get("pdf_info", [])
            table_count = 0
            formula_count = 0
            para_count = 0

            for page in pdf_info:
                # Count blocks in page
                blocks = page.get("blocks", []) if isinstance(page, dict) else []
                for b in blocks:
                    b_type = b.get("type", "")
                    if b_type == "table":
                        table_count += 1
                    elif b_type in ("inline_equation", "interline_equation", "equation"):
                        formula_count += 1
                    elif b_type in ("text", "para", "paragraph"):
                        para_count += 1

                # Count tables if stored in tables list
                tables = page.get("tables", []) if isinstance(page, dict) else []
                table_count += len(tables)

            stats.table_count = max(table_count, stats.table_count)
            stats.formula_count = max(formula_count, stats.formula_count)
            stats.paragraph_count = max(para_count, stats.paragraph_count)
        except Exception as exc:
            logger.debug(f"Error analyzing middle.json stats: {exc}")

    # Fallback to content_list_v2 if middle_json didn't yield table/formula count
    if stats.table_count == 0 and manifest.content_list_v2_path and manifest.content_list_v2_path.exists():
        try:
            with open(manifest.content_list_v2_path, "r", encoding="utf-8") as f:
                cl_data = json.load(f)
            if isinstance(cl_data, list):
                stats.table_count = sum(1 for item in cl_data if item.get("type") == "table")
                stats.formula_count = sum(1 for item in cl_data if item.get("type") in ("equation", "formula"))
        except Exception:
            pass

    return stats


def write_manifest_and_summary(
    manifest: OutputFileManifest,
    stats: DocumentStats,
    input_file: Path,
    backend: str,
    method: str
) -> Tuple[Path, Path]:
    """
    Write conversion_manifest.json and summary.json to output directory.
    
    Returns:
        Tuple of (manifest_path, summary_path).
    """
    out_dir = manifest.output_directory
    ensure_directory(out_dir)

    manifest_path = out_dir / MANIFEST_FILE_NAME
    summary_path = out_dir / "conversion_summary.json"

    # Build file checksums and sizes
    files_info = {}
    for p in manifest.all_files():
        try:
            rel = str(p.relative_to(out_dir))
            files_info[rel] = {
                "size_bytes": p.stat().st_size,
                "size_human": format_file_size(p.stat().st_size),
                "sha256": compute_file_sha256(p),
            }
        except Exception:
            pass

    manifest_data = {
        "schema_version": "1.0.0",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "document": {
            "name": manifest.document_name,
            "input_file": str(input_file.resolve()),
            "input_size_bytes": stats.input_file_size_bytes,
            "input_size_human": format_file_size(stats.input_file_size_bytes),
        },
        "conversion": {
            "backend": backend,
            "method": method,
            "duration_seconds": round(stats.conversion_duration_seconds, 2),
            "pages_total": stats.total_document_pages,
            "pages_processed": stats.processed_pages,
        },
        "artifacts": manifest.as_dict(),
        "files_checksums": files_info,
    }

    manifest_path.write_text(
        json.dumps(manifest_data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    manifest.manifest_path = manifest_path

    # Summary payload
    summary_data = {
        "document_name": manifest.document_name,
        "status": "success",
        "duration_seconds": round(stats.conversion_duration_seconds, 2),
        "pages": {
            "total": stats.total_document_pages,
            "processed": stats.processed_pages,
        },
        "extracted_content": {
            "characters": stats.character_count,
            "words": stats.word_count,
            "headings": stats.heading_count,
            "paragraphs": stats.paragraph_count,
            "tables": stats.table_count,
            "formulas": stats.formula_count,
            "images": stats.image_count,
        },
        "storage": {
            "total_output_size_bytes": stats.output_total_size_bytes,
            "total_output_size_human": format_file_size(stats.output_total_size_bytes),
        },
        "primary_files": {
            "markdown": str(manifest.markdown_path.resolve()) if manifest.markdown_path else None,
            "v1_content_list": str(manifest.content_list_v1_path.resolve()) if manifest.content_list_v1_path else None,
            "v2_content_list": str(manifest.content_list_v2_path.resolve()) if manifest.content_list_v2_path else None,
            "middle_json": str(manifest.middle_json_path.resolve()) if manifest.middle_json_path else None,
        }
    }

    summary_path.write_text(
        json.dumps(summary_data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    manifest.summary_path = summary_path

    return manifest_path, summary_path
