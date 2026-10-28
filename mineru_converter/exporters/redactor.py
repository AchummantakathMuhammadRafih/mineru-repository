"""
PII Redactor for Clean Markdown using Microsoft Presidio.

Features:
1. Analyzes markdown text for Personally Identifiable Information (PII) using Microsoft Presidio AnalyzerEngine.
2. Masks detected PII entities with stars (*) using Presidio AnonymizerEngine.
3. Preserves markdown formatting, headings, structure, and character length.
4. Generates a new redacted markdown file (_redacted.md).
"""

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from presidio_analyzer import AnalyzerEngine, RecognizerResult
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig
    PRESIDIO_AVAILABLE = True
except ImportError:
    PRESIDIO_AVAILABLE = False
    AnalyzerEngine = None
    RecognizerResult = None
    AnonymizerEngine = None
    OperatorConfig = None

from mineru_converter.utils.logging_utils import get_logger

logger = get_logger()

# Module-level cached instances to avoid reloading spaCy models on every call
_ANALYZER_INSTANCE = None
_ANONYMIZER_INSTANCE = None

# Default standard PII entities targeted for redaction
DEFAULT_PII_ENTITIES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "LOCATION",
    "IP_ADDRESS",
    "CREDIT_CARD",
    "CRYPTO",
    "IBAN_CODE",
    "US_SSN",
    "US_PASSPORT",
    "US_DRIVER_LICENSE",
    "US_BANK_NUMBER",
    "US_ITIN",
    "UK_NHS",
    "MEDICAL_LICENSE",
]


def get_presidio_engines() -> Tuple[Any, Any]:
    """
    Lazy load and return cached instances of Presidio Analyzer and Anonymizer engines.
    
    Raises:
        ImportError: If presidio-analyzer or presidio-anonymizer is not installed.
    """
    global _ANALYZER_INSTANCE, _ANONYMIZER_INSTANCE
    if not PRESIDIO_AVAILABLE:
        raise ImportError(
            "Microsoft Presidio is required for PII redaction. "
            "Please ensure 'presidio-analyzer' and 'presidio-anonymizer' are installed."
        )
    if _ANALYZER_INSTANCE is None:
        logger.info("Initializing Microsoft Presidio Analyzer & Anonymizer engines...")
        _ANALYZER_INSTANCE = AnalyzerEngine()
        _ANONYMIZER_INSTANCE = AnonymizerEngine()
    return _ANALYZER_INSTANCE, _ANONYMIZER_INSTANCE


def redact_markdown_text(
    markdown_text: str,
    entities: Optional[List[str]] = None,
    score_threshold: Optional[float] = 0.4,
    language: str = "en",
    masking_char: str = "*",
) -> Tuple[str, Dict[str, Any]]:
    """
    Redact PII in markdown text by masking each character of detected entities with stars (*).
    
    Args:
        markdown_text: The source markdown string.
        entities: List of Presidio entity types to redact. If None, uses DEFAULT_PII_ENTITIES.
                  Pass ["*"] or ["ALL"] to detect all Presidio-supported entities.
        score_threshold: Minimum confidence score for PII detection (0.0 to 1.0).
        language: Language code for NLP analysis (defaults to 'en').
        masking_char: Character used for masking (defaults to '*').
        
    Returns:
        Tuple of (redacted_markdown_text, summary_dict)
    """
    if not markdown_text or not markdown_text.strip():
        return markdown_text, {
            "total_redactions": 0,
            "entities_detected": {},
            "redacted_items": [],
        }

    analyzer, anonymizer = get_presidio_engines()

    # Determine supported language (fallback to English if requested language is not loaded)
    supported_langs = analyzer.nlp_engine.get_supported_languages()
    nlp_lang = language if language in supported_langs else "en"

    # Resolve target entities
    target_entities = entities
    if target_entities is None:
        target_entities = DEFAULT_PII_ENTITIES
    elif any(e in ("*", "ALL", "all") for e in target_entities):
        target_entities = None  # None tells Presidio to check all registered entities

    # Analyze text for PII entities
    results = analyzer.analyze(
        text=markdown_text,
        entities=target_entities,
        language=nlp_lang,
        score_threshold=score_threshold,
    )
    if results is None:
        results = []

    # Supplement: Detect person names in download watermarks e.g. "Downloaded by \[Name\] at" or "Downloaded by Name at"
    # Escaped brackets (\[...\]) often cause spaCy NER tokenizers to miss person entities
    if target_entities is None or "PERSON" in target_entities:
        watermark_pattern = re.compile(
            r'Downloaded\s+by\s*\\?\[?(?P<name>[A-Za-z\s\.\'-]+?)\\?\]?\s+at',
            re.IGNORECASE
        )
        for match in watermark_pattern.finditer(markdown_text):
            name_str = match.group("name").strip()
            # Only consider valid name strings (at least 2 letters, reasonable length)
            if len(name_str) >= 2 and any(c.isalpha() for c in name_str):
                start_idx, end_idx = match.start("name"), match.end("name")
                # Avoid overlapping duplicate results
                if not any(r.start <= start_idx and r.end >= end_idx for r in results):
                    results.append(
                        RecognizerResult(
                            entity_type="PERSON",
                            start=start_idx,
                            end=end_idx,
                            score=0.95,
                        )
                    )

    if not results:
        return markdown_text, {
            "total_redactions": 0,
            "entities_detected": {},
            "redacted_items": [],
        }

    # Configure anonymizer to mask detected PII with stars (*)
    # chars_to_mask is set very high so the entire entity span is masked
    operators = {
        "DEFAULT": OperatorConfig(
            "mask",
            {
                "type": "mask",
                "masking_char": masking_char,
                "chars_to_mask": 999999,
                "from_end": False,
            },
        )
    }

    anonymized_result = anonymizer.anonymize(
        text=markdown_text,
        analyzer_results=results,
        operators=operators,
    )

    # Compile summary statistics
    redacted_items = [
        {
            "start": item.start,
            "end": item.end,
            "entity_type": item.entity_type,
            "masked_length": len(item.text) if item.text else (item.end - item.start),
            "operator": item.operator,
        }
        for item in anonymized_result.items
    ]

    entity_counts = dict(Counter(item.entity_type for item in anonymized_result.items))

    summary = {
        "total_redactions": len(redacted_items),
        "entities_detected": entity_counts,
        "redacted_items": redacted_items,
    }

    return anonymized_result.text, summary


def redact_markdown_file(
    input_clean_md_path: Union[str, Path],
    output_redacted_md_path: Optional[Union[str, Path]] = None,
    entities: Optional[List[str]] = None,
    score_threshold: Optional[float] = 0.4,
    language: str = "en",
    masking_char: str = "*",
) -> Tuple[Path, Dict[str, Any]]:
    """
    Read cleaned markdown, redact PII with Presidio masked with stars,
    and save the output to a new markdown file.
    
    Args:
        input_clean_md_path: Path to the clean markdown file.
        output_redacted_md_path: Path for the redacted markdown file. If None,
                                 saves to <doc_stem>_redacted.md in the same directory.
        entities: Optional list of entities to redact.
        score_threshold: Minimum confidence score.
        language: Language for NLP analysis.
        masking_char: Character used for masking (default: '*').
        
    Returns:
        Tuple of (output_redacted_md_path, summary_dict)
    """
    in_path = Path(input_clean_md_path).resolve()
    if not in_path.exists():
        raise FileNotFoundError(f"Source markdown file not found: {in_path}")

    # Determine destination path
    if output_redacted_md_path is None:
        stem = in_path.stem
        # If stem ends with _clean, replace with _redacted
        if stem.endswith("_clean"):
            redacted_stem = stem[:-6] + "_redacted"
        else:
            redacted_stem = stem + "_redacted"
        out_path = in_path.parent / f"{redacted_stem}.md"
    else:
        out_path = Path(output_redacted_md_path).resolve()

    with open(in_path, "r", encoding="utf-8") as f:
        clean_text = f.read()

    redacted_text, summary = redact_markdown_text(
        markdown_text=clean_text,
        entities=entities,
        score_threshold=score_threshold,
        language=language,
        masking_char=masking_char,
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(redacted_text)

    logger.info(
        f"Redacted markdown saved to {out_path.name} "
        f"({summary['total_redactions']} PII entities masked with '{masking_char}')"
    )
    return out_path, summary
