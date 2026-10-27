"""Settings and configuration schema for the MinerU PDF conversion pipeline."""

from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


from mineru_converter.models.enums import BackendType, ParseMethod, HybridEffort


class ConversionConfig(BaseModel):
    """Configuration options for converting a PDF via MinerU."""

    output_dir: Path = Field(
        default=Path("./output/mineru"),
        description="Target root directory where output artifacts will be saved."
    )
    backend: BackendType = Field(
        default=BackendType.PIPELINE,
        description="Backend engine for parsing: pipeline, hybrid-engine, vlm-engine, etc."
    )
    method: ParseMethod = Field(
        default=ParseMethod.AUTO,
        description="Parsing method: auto, txt (fast text-only), ocr (image/scanned PDF OCR)."
    )
    lang: str = Field(
        default="ch",
        description="Primary document language code for OCR (e.g. 'ch', 'en', 'korean', 'arabic', 'devanagari')."
    )
    effort: HybridEffort = Field(
        default=HybridEffort.MEDIUM,
        description="Effort level for hybrid backend: medium (faster) or high (detailed image/chart analysis)."
    )
    
    # Feature flags for extraction
    formula_enable: bool = Field(
        default=True,
        description="Enable LaTeX formula recognition and extraction."
    )
    table_enable: bool = Field(
        default=True,
        description="Enable table structure extraction (HTML/Markdown/LaTeX)."
    )
    image_analysis: bool = Field(
        default=True,
        description="Enable image and chart analysis for VLM and Hybrid backends."
    )
    
    # Output artifact generation flags
    dump_images: bool = Field(
        default=True,
        description="Save extracted images to disk under images/ folder."
    )
    capture_header: bool = Field(
        default=True,
        description="Elevate first header to top level (#) if needed using high-quality markdown parser."
    )
    dump_md: bool = Field(
        default=True,
        description="Produce Markdown (.md) document."
    )
    dump_content_list: bool = Field(
        default=True,
        description="Produce both V1 (_content_list.json) and V2 (_content_list_v2.json) structured lists."
    )
    generate_clean_markdown: bool = Field(
        default=True,
        description="Run dominant font-size hierarchy correction on V2 list to generate duplicate corrected V2 JSON and clean, filtered Markdown."
    )
    redact_pii: bool = Field(
        default=True,
        description="Redact PII from clean markdown using Microsoft Presidio (masked with stars)."
    )
    pii_entities: Optional[List[str]] = Field(
        default=None,
        description="List of Presidio entity types to redact. If None, redacts standard PII entities."
    )
    pii_score_threshold: Optional[float] = Field(
        default=0.4,
        description="Confidence score threshold for PII entity detection."
    )

    dump_middle_json: bool = Field(
        default=True,
        description="Produce MinerU intermediate representation AST (_middle.json)."
    )
    dump_model_output: bool = Field(
        default=True,
        description="Produce raw model inference output (_model.json)."
    )
    draw_layout_bbox: bool = Field(
        default=True,
        description="Produce annotated layout PDF with bounding boxes (_layout.pdf)."
    )
    draw_span_bbox: bool = Field(
        default=True,
        description="Produce annotated span PDF with inline token/span boxes (_span.pdf)."
    )
    dump_orig_pdf: bool = Field(
        default=True,
        description="Produce normalized original PDF slice (_origin.pdf)."
    )
    
    # Page filtering (0-indexed internally)
    start_page: int = Field(
        default=0,
        ge=0,
        description="Start page index (0-indexed, inclusive)."
    )
    end_page: Optional[int] = Field(
        default=None,
        description="End page index (0-indexed, inclusive). None processes to document end."
    )
    
    # Remote inference server
    server_url: Optional[str] = Field(
        default=None,
        description="Server endpoint URL when using remote http client backends."
    )
    
    client_side_output_generation: bool = Field(
        default=False,
        description="Generate markdown and content lists locally from remote server middle json."
    )

    # Post-processing and packaging
    generate_manifest: bool = Field(
        default=True,
        description="Generate conversion_manifest.json with artifact filepaths, checksums, and metadata."
    )
    generate_summary: bool = Field(
        default=True,
        description="Generate text/json summary of document blocks, image count, formula count, etc."
    )


    @field_validator("lang", mode="before")
    @classmethod
    def normalize_language(cls, v: str) -> str:
        """Map common language synonyms."""
        val = str(v).strip().lower()
        if val in ("en", "english"):
            return "ch"  # MinerU default OCR handles English via 'ch' model efficiently
        return val

    @field_validator("output_dir", mode="before")
    @classmethod
    def ensure_path(cls, v) -> Path:
        return Path(v) if not isinstance(v, Path) else v

    class Config:
        arbitrary_types_allowed = True
        use_enum_values = True
