"""Docling hybrid chunking integration for MinerU pipeline."""

import json
from pathlib import Path
from typing import Any, Dict, Optional, Union
import httpx

from mineru_converter.utils.logging_utils import get_logger

logger = get_logger()


def chunk_markdown_with_docling(
    markdown_path: Union[str, Path],
    output_json_path: Optional[Union[str, Path]] = None,
    docling_endpoint: str = "http://localhost:5001/v1/chunk/hybrid/file",
    max_tokens: int = 1024,
    timeout: float = 300.0,
) -> Optional[Path]:
    """
    Send Markdown file to Docling HTTP service for hybrid chunking.
    
    Args:
        markdown_path: Path to the input markdown file (e.g. _redacted.md).
        output_json_path: Path where docling_chunks.json will be saved.
                          If None, saves as 'docling_chunks.json' in markdown_path's parent directory.
        docling_endpoint: Full URL to Docling's /v1/chunk/hybrid/file endpoint.
        max_tokens: Maximum tokens per chunk (default: 1024).
        timeout: HTTP request timeout in seconds (default: 300.0s / 5 minutes).
        
    Returns:
        Path to output docling_chunks.json file, or None if chunking failed/skipped.
    """
    in_path = Path(markdown_path).resolve()
    if not in_path.exists():
        logger.warning(f"Docling chunking skipped: Source markdown not found at {in_path}")
        return None

    if output_json_path is None:
        out_json = in_path.parent / "docling_chunks.json"
    else:
        out_json = Path(output_json_path).resolve()

    out_json.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        f"🧩 [Docling Chunker] Sending '{in_path.name}' to Docling ({docling_endpoint}) "
        f"for {max_tokens}-token hybrid chunking (timeout: {timeout:.0f}s)..."
    )

    try:
        with open(in_path, "rb") as f:
            file_bytes = f.read()

        files = {
            "files": (in_path.name, file_bytes, "text/markdown"),
        }
        data = {
            "chunking_max_tokens": max_tokens,
            "chunking_use_markdown_tables": True,
            "chunking_use_markdown_images": False,
            "chunking_merge_peers": True,
        }

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(docling_endpoint, files=files, data=data)

        if resp.status_code == 200:
            result_data = resp.json()
            num_chunks = len(result_data.get("chunks", []))
            out_json.write_text(json.dumps(result_data, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(
                f"✓ [Docling Chunker] Hybrid chunking complete: "
                f"{num_chunks} chunks written to {out_json.name}"
            )
            return out_json
        else:
            logger.warning(
                f"⚠️ [Docling Chunker] Service returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
            return None

    except httpx.ConnectError:
        logger.warning(
            f"⚠️ [Docling Chunker] Could not connect to Docling service at {docling_endpoint}. "
            "Is the Docling server running on port 5001?"
        )
        return None
    except httpx.TimeoutException:
        logger.warning(
            f"⚠️ [Docling Chunker] Request to {docling_endpoint} timed out after {timeout:.0f} seconds."
        )
        return None
    except Exception as exc:
        logger.warning(f"⚠️ [Docling Chunker] Unexpected error during chunking: {exc}")
        return None
