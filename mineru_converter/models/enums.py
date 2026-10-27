"""Enumerations used across the MinerU PDF converter."""

from enum import Enum


class BackendType(str, Enum):
    """Available processing backends for MinerU."""
    PIPELINE = "pipeline"
    HYBRID_ENGINE = "hybrid-engine"
    VLM_ENGINE = "vlm-engine"
    HYBRID_HTTP_CLIENT = "hybrid-http-client"
    VLM_HTTP_CLIENT = "vlm-http-client"


class ParseMethod(str, Enum):
    """PDF parsing mode."""
    AUTO = "auto"
    TXT = "txt"
    OCR = "ocr"


class HybridEffort(str, Enum):
    """Effort level for hybrid backend."""
    MEDIUM = "medium"
    HIGH = "high"


class MakeModeEnum(str, Enum):
    """Markdown rendering mode."""
    MM_MD = "mm_md"
    STANDARD_MD = "standard_md"
    RAW_MD = "raw_md"
