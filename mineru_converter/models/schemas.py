"""Pydantic schemas and dataclasses for conversion requests, results, manifests, and stats."""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class OutputFileManifest(BaseModel):
    """Tracks all generated output artifacts for a converted document."""

    document_name: str
    output_directory: Path
    
    # Core MinerU output files
    markdown_path: Optional[Path] = Field(None, description="Path to .md Markdown document")
    content_list_v1_path: Optional[Path] = Field(None, description="Path to _content_list.json (V1)")
    content_list_v2_path: Optional[Path] = Field(None, description="Path to _content_list_v2.json (V2)")
    middle_json_path: Optional[Path] = Field(None, description="Path to _middle.json AST representation")
    model_json_path: Optional[Path] = Field(None, description="Path to _model.json model output")
    
    # Visual validation artifacts
    layout_pdf_path: Optional[Path] = Field(None, description="Path to _layout.pdf with block boxes")
    span_pdf_path: Optional[Path] = Field(None, description="Path to _span.pdf with inline token boxes")
    origin_pdf_path: Optional[Path] = Field(None, description="Path to _origin.pdf normalized PDF")
    
    # Extracted media directory & files
    images_dir: Optional[Path] = Field(None, description="Directory containing extracted images/tables")
    image_files: List[Path] = Field(default_factory=list, description="List of individual image file paths")
    
    # Clean / Corrected / Redacted Markdown artifacts
    clean_markdown_path: Optional[Path] = Field(None, description="Path to _clean.md Markdown document")
    redacted_markdown_path: Optional[Path] = Field(None, description="Path to _redacted.md Markdown document")
    corrected_content_list_v2_path: Optional[Path] = Field(None, description="Path to _content_list_v2_corrected.json")

    # Meta / summary artifacts
    manifest_path: Optional[Path] = Field(None, description="Path to generated conversion_manifest.json")
    summary_path: Optional[Path] = Field(None, description="Path to generated summary.json")

    def all_files(self) -> List[Path]:
        """Return list of all existing generated files."""
        files = []
        for p in [
            self.markdown_path,
            self.clean_markdown_path,
            self.redacted_markdown_path,
            self.content_list_v1_path,
            self.content_list_v2_path,
            self.corrected_content_list_v2_path,
            self.middle_json_path,
            self.model_json_path,
            self.layout_pdf_path,
            self.span_pdf_path,
            self.origin_pdf_path,
            self.manifest_path,
            self.summary_path,
        ]:
            if p and p.exists():
                files.append(p)
        files.extend([img for img in self.image_files if img.exists()])
        return files

    def as_dict(self) -> Dict[str, Any]:
        """Serialize paths to strings for JSON serialization."""
        return {
            "document_name": self.document_name,
            "output_directory": str(self.output_directory),
            "markdown": str(self.markdown_path) if self.markdown_path else None,
            "clean_markdown": str(self.clean_markdown_path) if self.clean_markdown_path else None,
            "redacted_markdown": str(self.redacted_markdown_path) if self.redacted_markdown_path else None,
            "content_list_v1": str(self.content_list_v1_path) if self.content_list_v1_path else None,
            "content_list_v2": str(self.content_list_v2_path) if self.content_list_v2_path else None,
            "corrected_content_list_v2": str(self.corrected_content_list_v2_path) if self.corrected_content_list_v2_path else None,
            "middle_json": str(self.middle_json_path) if self.middle_json_path else None,
            "model_json": str(self.model_json_path) if self.model_json_path else None,
            "layout_pdf": str(self.layout_pdf_path) if self.layout_pdf_path else None,
            "span_pdf": str(self.span_pdf_path) if self.span_pdf_path else None,
            "origin_pdf": str(self.origin_pdf_path) if self.origin_pdf_path else None,
            "images_dir": str(self.images_dir) if self.images_dir else None,
            "image_count": len(self.image_files),
            "image_files": [str(p) for p in self.image_files],
            "manifest_file": str(self.manifest_path) if self.manifest_path else None,
            "summary_file": str(self.summary_path) if self.summary_path else None,
        }


    class Config:
        arbitrary_types_allowed = True


class DocumentStats(BaseModel):
    """Statistics and metric counts extracted from the parsed document."""

    total_document_pages: int = 0
    processed_pages: int = 0
    character_count: int = 0
    word_count: int = 0
    paragraph_count: int = 0
    heading_count: int = 0
    table_count: int = 0
    formula_count: int = 0
    image_count: int = 0
    input_file_size_bytes: int = 0
    output_total_size_bytes: int = 0
    conversion_duration_seconds: float = 0.0


class ConversionResult(BaseModel):
    """Result of a document conversion."""

    success: bool
    input_path: Path
    document_name: str
    manifest: OutputFileManifest
    stats: Optional[DocumentStats] = None
    error_message: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    duration_seconds: float = 0.0

    class Config:
        arbitrary_types_allowed = True


class BatchSummary(BaseModel):
    """Summary of a batch conversion run."""

    total_documents: int
    succeeded_count: int
    failed_count: int
    results: List[ConversionResult]
    total_duration_seconds: float
    output_directory: Path

    @property
    def success_rate(self) -> float:
        if self.total_documents == 0:
            return 0.0
        return (self.succeeded_count / self.total_documents) * 100.0

    class Config:
        arbitrary_types_allowed = True
