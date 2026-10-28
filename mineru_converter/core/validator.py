"""PDF file validation, page count inspection, and corruption checks."""

from pathlib import Path
from typing import Optional, Tuple
import pypdf

from mineru_converter.utils.logging_utils import get_logger

logger = get_logger()


class PDFValidationError(Exception):
    """Raised when PDF file is invalid, encrypted, or cannot be parsed."""
    pass


def validate_pdf_document(
    pdf_path: Path,
    start_page: int = 0,
    end_page: Optional[int] = None
) -> Tuple[int, int, int]:
    """
    Validate PDF file integrity, check encryption, and resolve effective page range.
    
    Args:
        pdf_path: Path to PDF file.
        start_page: Desired 0-indexed start page.
        end_page: Desired 0-indexed end page (inclusive), or None for all.
        
    Returns:
        Tuple of (total_pages, effective_start_page, effective_end_page).
        
    Raises:
        PDFValidationError: If file does not exist, is empty, is encrypted with password, or has invalid page bounds.
    """
    if not pdf_path.exists():
        raise PDFValidationError(f"PDF file does not exist: {pdf_path}")

    if pdf_path.stat().st_size == 0:
        raise PDFValidationError(f"PDF file is empty (0 bytes): {pdf_path}")

    # Non-PDF documents (docx, pptx, xlsx, html)
    ext = pdf_path.suffix.lower()
    if ext != ".pdf":
        return 1, 0, 0

    try:
        reader = pypdf.PdfReader(str(pdf_path))
        if reader.is_encrypted:
            try:
                # Try decrypting with empty password
                reader.decrypt("")
            except Exception:
                raise PDFValidationError(
                    f"PDF document is password protected and cannot be opened: {pdf_path.name}"
                )

        total_pages = len(reader.pages)
        if total_pages == 0:
            raise PDFValidationError(f"PDF contains 0 pages: {pdf_path.name}")

    except PDFValidationError:
        raise
    except Exception as exc:
        raise PDFValidationError(f"Failed to read PDF structure for '{pdf_path.name}': {exc}") from exc

    # Validate page range
    if start_page < 0 or start_page >= total_pages:
        raise PDFValidationError(
            f"Invalid start_page {start_page}. Document '{pdf_path.name}' only has {total_pages} pages (0 to {total_pages - 1})."
        )

    if end_page is not None:
        if end_page < start_page:
            raise PDFValidationError(
                f"end_page ({end_page}) cannot be less than start_page ({start_page})."
            )
        effective_end = min(end_page, total_pages - 1)
    else:
        effective_end = total_pages - 1

    return total_pages, start_page, effective_end
