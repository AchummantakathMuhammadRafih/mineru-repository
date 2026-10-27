"""Constants and configuration defaults for the MinerU PDF converter."""

from enum import Enum
from pathlib import Path

# Supported input extensions
SUPPORTED_PDF_EXTENSIONS = {".pdf"}
SUPPORTED_DOC_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg", ".webp"}

# Supported Backend Types
class BackendType(str, Enum):
    PIPELINE = "pipeline"
    HYBRID_ENGINE = "hybrid-engine"
    VLM_ENGINE = "vlm-engine"
    HYBRID_HTTP_CLIENT = "hybrid-http-client"
    VLM_HTTP_CLIENT = "vlm-http-client"

# Parse Methods
class ParseMethod(str, Enum):
    AUTO = "auto"
    TXT = "txt"
    OCR = "ocr"

# Hybrid Effort levels
class HybridEffort(str, Enum):
    MEDIUM = "medium"
    HIGH = "high"

# Supported Languages for OCR
SUPPORTED_LANGUAGES = [
    "ch",           # Chinese & English
    "ch_server",    # Chinese Server OCR
    "en",           # English (mapped to ch for paddleocr or custom)
    "korean",       # Korean
    "ta",           # Tamil
    "te",           # Telugu
    "ka",           # Kannada
    "th",           # Thai
    "el",           # Greek
    "arabic",       # Arabic
    "east_slavic",  # East Slavic
    "cyrillic",     # Cyrillic
    "devanagari",   # Devanagari / Hindi
]

# Output Artifact Keys
ARTIFACT_MARKDOWN = "markdown"
ARTIFACT_CONTENT_LIST_V1 = "content_list_v1"
ARTIFACT_CONTENT_LIST_V2 = "content_list_v2"
ARTIFACT_MIDDLE_JSON = "middle_json"
ARTIFACT_MODEL_JSON = "model_json"
ARTIFACT_LAYOUT_PDF = "layout_pdf"
ARTIFACT_SPAN_PDF = "span_pdf"
ARTIFACT_ORIGIN_PDF = "origin_pdf"
ARTIFACT_IMAGES_DIR = "images_dir"
ARTIFACT_MANIFEST = "manifest"
ARTIFACT_CLEAN_MARKDOWN = "clean_markdown"
ARTIFACT_REDACTED_MARKDOWN = "redacted_markdown"

# Default filenames suffixes
SUFFIX_MARKDOWN = ".md"
SUFFIX_CLEAN_MARKDOWN = "_clean.md"
SUFFIX_REDACTED_MARKDOWN = "_redacted.md"
SUFFIX_CONTENT_LIST_V1 = "_content_list.json"
SUFFIX_CONTENT_LIST_V2 = "_content_list_v2.json"
SUFFIX_CORRECTED_CONTENT_LIST_V2 = "_content_list_v2_corrected.json"
SUFFIX_MIDDLE_JSON = "_middle.json"
SUFFIX_MODEL_JSON = "_model.json"
SUFFIX_LAYOUT_PDF = "_layout.pdf"
SUFFIX_SPAN_PDF = "_span.pdf"
SUFFIX_ORIGIN_PDF = "_origin.pdf"
IMAGES_DIR_NAME = "images"
MANIFEST_FILE_NAME = "conversion_manifest.json"

DEFAULT_OUTPUT_DIR = Path("./output/mineru")
