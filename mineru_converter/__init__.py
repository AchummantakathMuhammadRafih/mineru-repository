"""
MinerU PDF Converter Toolkit
============================

A modular Python framework and CLI utility for high-accuracy document parsing,
Markdown generation, V1/V2 Content Lists, AST Middle JSON, visual bounding box overlays,
and structured media extraction using MinerU.
"""

# Apply third-party compatibility patches (e.g. transformers >= 4.45 / 5.x get_head_mask)
from mineru_converter.core.patches import apply_transformers_compat_patches
apply_transformers_compat_patches()

from mineru_converter.config.settings import ConversionConfig
from mineru_converter.config.constants import (
    BackendType,
    ParseMethod,
    HybridEffort,
    SUPPORTED_LANGUAGES,
    DEFAULT_OUTPUT_DIR,
)
from mineru_converter.core.converter import MinerUConverter
from mineru_converter.core.database import JobDatabase, JobStatus
from mineru_converter.core.watcher import BackgroundWatcher
from mineru_converter.models.schemas import (
    ConversionResult,
    OutputFileManifest,
    DocumentStats,
    BatchSummary,
)

__version__ = "1.0.0"
__author__ = "Muhammad Rafih"

__all__ = [
    "MinerUConverter",
    "BackgroundWatcher",
    "JobDatabase",
    "JobStatus",
    "ConversionConfig",
    "ConversionResult",
    "OutputFileManifest",
    "DocumentStats",
    "BatchSummary",
    "BackendType",
    "ParseMethod",
    "HybridEffort",
    "SUPPORTED_LANGUAGES",
    "DEFAULT_OUTPUT_DIR",
]
