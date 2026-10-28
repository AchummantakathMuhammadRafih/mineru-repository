"""
Hierarchy Corrector and Markdown Generator using content_list_v2.json and PyMuPDF.

Features:
1. Extracts bboxes from "type": "title" elements in content_list_v2.json.
2. Finds the dominant font size for each title from the original PDF in that bbox area.
3. Arranges unique font sizes in descending order to assign hierarchical levels (H1, H2, H3, ...).
4. Corrects the "level" in a duplicate/copy of content_list_v2.json (preserving the original v2 intact).
5. Generates cleaned Markdown:
   - Formats titles based on corrected levels (#, ##, ###, ...).
   - Keeps paragraphs, tables, and equations.
   - Filters out unwanted noise: page_header, page_footer, page_number, images, footnotes (if not needed).
"""

from collections import Counter
import copy
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

from mineru_converter.utils.logging_utils import get_logger

logger = get_logger()


def _safe_open(file_path: Union[str, Path], mode: str = "r", encoding: str = "utf-8"):
    """Open file safely handling Windows long path limits (>260 chars)."""
    path_str = str(Path(file_path).resolve())
    if sys.platform == "win32" and not path_str.startswith("\\\\?\\"):
        path_str = "\\\\?\\" + path_str
    return open(path_str, mode, encoding=encoding)


def extract_dominant_font_size(
    page: Any,
    bbox_1000: List[Union[int, float]],
    padding_pt: float = 1.0,
) -> Optional[float]:
    """
    Extract the dominant font size within the specified bounding box from a PyMuPDF page.
    bbox_1000 is given in 0-1000 normalized coordinates: [x0, y0, x1, y1].
    """
    if page is None or not bbox_1000 or len(bbox_1000) != 4:
        return None

    rect = page.rect
    x0 = max(0.0, (bbox_1000[0] * rect.width / 1000.0) - padding_pt)
    y0 = max(0.0, (bbox_1000[1] * rect.height / 1000.0) - padding_pt)
    x1 = min(rect.width, (bbox_1000[2] * rect.width / 1000.0) + padding_pt)
    y1 = min(rect.height, (bbox_1000[3] * rect.height / 1000.0) + padding_pt)

    clip_rect = fitz.Rect(x0, y0, x1, y1)
    text_data = page.get_text("dict", clip=clip_rect)

    sizes_weighted: List[float] = []
    for block in text_data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_text = span.get("text", "").strip()
                span_size = span.get("size")
                if span_text and span_size is not None and span_size > 0:
                    # Weight size by character length of the text span
                    sizes_weighted.extend([round(float(span_size), 1)] * len(span_text))

    if not sizes_weighted:
        return None

    # Return most common (dominant) font size
    dominant_size = Counter(sizes_weighted).most_common(1)[0][0]
    return dominant_size


def cluster_font_sizes(sizes_desc: List[float], tolerance: float = 0.5) -> Dict[float, int]:
    """
    Cluster font sizes that are almost identical (e.g. 14.1 and 14.0)
    and map them to heading levels (1, 2, 3, ... capped at 6).
    """
    size_to_level: Dict[float, int] = {}
    current_level = 1

    unique_sizes = sorted(set(sizes_desc), reverse=True)
    if not unique_sizes:
        return {}

    # Simple greedy clustering
    clusters: List[List[float]] = []
    for s in unique_sizes:
        added = False
        for c in clusters:
            if abs(c[0] - s) <= tolerance:
                c.append(s)
                added = True
                break
        if not added:
            clusters.append([s])

    # Assign levels in descending order of cluster representative
    for cluster in clusters:
        assigned_level = min(current_level, 6)
        for s in cluster:
            size_to_level[s] = assigned_level
        current_level += 1

    return size_to_level


def correct_title_hierarchy(
    content_list_v2: List[List[Dict[str, Any]]],
    pdf_path: Union[str, Path],
) -> Tuple[List[List[Dict[str, Any]]], Dict[str, Any]]:
    """
    1. Clone content_list_v2 so original is never modified.
    2. Extract dominant font size for each "type": "title" block using original PDF.
    3. Arrange font sizes descending and compute proper hierarchy levels.
    4. Update "level" in the duplicated structure.
    
    Returns:
        (corrected_v2, summary_stats)
    """
    if fitz is None:
        raise ImportError("PyMuPDF (fitz) is required for font size extraction.")

    corrected_v2 = copy.deepcopy(content_list_v2)
    doc = fitz.open(str(pdf_path))

    title_entries: List[Dict[str, Any]] = []

    # First pass: collect dominant font sizes for all titles
    for page_idx, page_blocks in enumerate(corrected_v2):
        page = doc[page_idx] if page_idx < len(doc) else None
        for block_idx, block in enumerate(page_blocks):
            if block.get("type") == "title":
                bbox = block.get("bbox", [])
                dom_size = extract_dominant_font_size(page, bbox)
                
                # Title text for reporting
                content_obj = block.get("content", {})
                title_spans = content_obj.get("title_content", [])
                text_content = "".join(
                    s.get("content", "") for s in title_spans if isinstance(s, dict)
                )

                title_entries.append({
                    "page_idx": page_idx,
                    "block_idx": block_idx,
                    "text": text_content,
                    "orig_level": content_obj.get("level"),
                    "dom_size": dom_size,
                    "block_ref": block,
                })

    # Find unique dominant sizes in descending order
    valid_sizes = [t["dom_size"] for t in title_entries if t["dom_size"] is not None]
    size_to_level = cluster_font_sizes(valid_sizes)

    # Fallback level if dominant font size could not be extracted
    default_level = max(size_to_level.values()) + 1 if size_to_level else 1

    # Second pass: correct levels in the duplicated v2 list
    corrected_count = 0
    for entry in title_entries:
        dom_size = entry["dom_size"]
        if dom_size is not None and dom_size in size_to_level:
            new_level = size_to_level[dom_size]
        else:
            new_level = entry["orig_level"] or default_level

        entry["new_level"] = new_level
        if new_level != entry["orig_level"]:
            corrected_count += 1

        entry["block_ref"]["content"]["level"] = new_level

    doc.close()

    summary_stats = {
        "total_titles": len(title_entries),
        "corrected_count": corrected_count,
        "size_to_level_mapping": {str(k): v for k, v in size_to_level.items()},
        "title_details": [
            {
                "page": e["page_idx"] + 1,
                "text": e["text"],
                "dom_size": e["dom_size"],
                "old_level": e["orig_level"],
                "new_level": e["new_level"],
            }
            for e in title_entries
        ],
    }

    return corrected_v2, summary_stats


def normalize_heading_levels(
    content_list_v2: List[List[Dict[str, Any]]],
) -> Tuple[List[List[Dict[str, Any]]], Dict[str, Any]]:
    """
    Sequential order pass for heuristic hierarchy correction:
    Scans headings in document reading order and eliminates level skips.
    
    If heading levels jump (e.g. H1 followed by H3, H6), they are compressed
    in order without gaps:
        # he        (H1)
        ### g       (H3 -> H2)
        ###### h    (H6 -> H3)
    
    Siblings and parent ascents are preserved correctly:
    - If current_level <= prev_level: it climbs to an ancestor/sibling level,
      mapped to the active stack's corresponding level.
    - If current_level > prev_level: it can descend at most 1 level deeper
      (new_level = prev_assigned_level + 1).
      
    Returns:
        (updated_content_list_v2, summary_stats)
    """
    heading_refs: List[Dict[str, Any]] = []

    for page_idx, page in enumerate(content_list_v2):
        for block_idx, block in enumerate(page):
            if block.get("type") == "title":
                content_obj = block.get("content", {})
                title_spans = content_obj.get("title_content", [])
                text_content = "".join(
                    s.get("content", "") for s in title_spans if isinstance(s, dict)
                ).strip()
                heading_refs.append({
                    "page_idx": page_idx,
                    "block_idx": block_idx,
                    "text": text_content,
                    "raw_level": max(1, min(6, int(content_obj.get("level", 1)))),
                    "content_obj": content_obj,
                })

    if not heading_refs:
        return content_list_v2, {"normalized_count": 0, "adjustments": []}

    adjustments: List[Dict[str, Any]] = []

    # Track ancestor hierarchy chain: list of (orig_raw_level, assigned_level)
    chain: List[Tuple[int, int]] = []

    for entry in heading_refs:
        raw_lvl = entry["raw_level"]

        if not chain:
            # First heading in document: must start at H1 (or top of hierarchy)
            assigned_lvl = 1
            chain.append((raw_lvl, assigned_lvl))
        else:
            prev_raw, prev_assigned = chain[-1]

            if raw_lvl > prev_raw:
                # Descending to a child heading: cannot skip levels, exactly 1 level deeper
                assigned_lvl = min(6, prev_assigned + 1)
                chain.append((raw_lvl, assigned_lvl))
            elif raw_lvl == prev_raw:
                # Sibling at the same original level
                assigned_lvl = prev_assigned
            else:
                # Ascending to a parent/ancestor level (raw_lvl < prev_raw)
                # Pop chain until finding an ancestor with <= raw_lvl
                while chain and chain[-1][0] > raw_lvl:
                    chain.pop()

                if chain and chain[-1][0] == raw_lvl:
                    # Found an exact matching ancestor level
                    assigned_lvl = chain[-1][1]
                elif chain:
                    # Between two levels on the stack: nest 1 level below that ancestor
                    assigned_lvl = min(6, chain[-1][1] + 1)
                    chain.append((raw_lvl, assigned_lvl))
                else:
                    # Popped completely: top level
                    assigned_lvl = 1
                    chain.append((raw_lvl, assigned_lvl))

        if assigned_lvl != raw_lvl:
            adjustments.append({
                "page": entry["page_idx"] + 1,
                "text": entry["text"][:40],
                "old_level": raw_lvl,
                "new_level": assigned_lvl,
            })
            entry["content_obj"]["level"] = assigned_lvl

    if adjustments:
        logger.info(
            f"✓ [Level Normalizer] Corrected {len(adjustments)} heading level skip(s) in reading order."
        )

    summary_stats = {
        "normalized_count": len(adjustments),
        "adjustments": adjustments,
    }

    return content_list_v2, summary_stats


def correct_title_hierarchy_llm(
    content_list_v2: List[List[Dict[str, Any]]],
    openai_api_key: Optional[str] = None,
    openai_model: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    azure_endpoint: Optional[str] = None,
    azure_api_key: Optional[str] = None,
    azure_deployment: Optional[str] = None,
    azure_api_version: str = "2024-02-15-preview",
    temperature: float = 0.0,
    timeout: Optional[float] = None,
    fallback_pdf_path: Optional[Union[str, Path]] = None,
) -> Tuple[List[List[Dict[str, Any]]], Dict[str, Any]]:
    """
    Parallel LLM Flow using OpenAI or Azure OpenAI:
    1. Clone content_list_v2 so original is never modified.
    2. Extract only the 'type': 'title' elements from content_list_v2, assigning each a unique sequential id.
    3. Send the complete list of headings to OpenAI / Azure OpenAI with a structured JSON schema.
    4. Parse returned JSON mapping id -> corrected level (1..6).
    5. Update 'level' in the duplicated structure.
    6. If LLM credentials are missing or the API call fails, gracefully falls back
       to font-size heuristics (if PDF is available) or original levels.

    Returns:
        (corrected_v2, summary_stats)
    """
    corrected_v2 = copy.deepcopy(content_list_v2)
    title_entries: List[Dict[str, Any]] = []

    for page_idx, page_blocks in enumerate(corrected_v2):
        for block_idx, block in enumerate(page_blocks):
            if block.get("type") == "title":
                content_obj = block.get("content", {})
                title_spans = content_obj.get("title_content", [])
                text_content = "".join(
                    s.get("content", "") for s in title_spans if isinstance(s, dict)
                ).strip()

                title_entries.append({
                    "id": len(title_entries),
                    "page_idx": page_idx,
                    "block_idx": block_idx,
                    "text": text_content,
                    "orig_level": content_obj.get("level"),
                    "block_ref": block,
                })

    if not title_entries:
        logger.info("🤖 [LLM Hierarchy] Document contains 0 title headings; skipping LLM call.")
        return corrected_v2, {
            "method": "llm_semantic",
            "total_titles": 0,
            "corrected_count": 0,
            "title_details": [],
        }

    # Resolve provider:
    # If openai_api_key is explicitly passed, use direct OpenAI.
    # If azure_endpoint / azure_api_key is explicitly passed, use Azure OpenAI.
    # Otherwise check environment variables.
    explicit_direct = bool(openai_api_key)
    explicit_azure = bool(azure_endpoint or azure_api_key or azure_deployment)

    az_endpoint = (azure_endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT") or "").strip().rstrip("/")
    az_key = (azure_api_key or os.environ.get("AZURE_OPENAI_API_KEY") or "").strip()
    az_deployment = (
        azure_deployment
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_MODEL")
        or "gpt-4o-mini"
    ).strip()
    az_version = (azure_api_version or os.environ.get("AZURE_OPENAI_API_VERSION") or "2024-02-15-preview").strip()

    oai_key = (openai_api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
    oai_model = (openai_model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
    oai_base = (openai_base_url or os.environ.get("OPENAI_BASE_URL") or "").strip() or None

    if explicit_direct:
        is_direct_openai = True
        is_azure = False
    elif explicit_azure:
        is_azure = bool(az_endpoint and az_key)
        is_direct_openai = False
    else:
        # Check env vars
        if oai_key and not (az_endpoint and az_key):
            is_direct_openai = True
            is_azure = False
        else:
            is_azure = bool(az_endpoint and az_key)
            is_direct_openai = bool(oai_key and not is_azure)

    if not is_azure and not is_direct_openai:
        logger.warning(
            "⚠️ [LLM Hierarchy] No OpenAI or Azure OpenAI credentials found (neither OPENAI_API_KEY nor AZURE_OPENAI_API_KEY/ENDPOINT are set). "
            f"Attempting fallback to font-size heuristics (fallback_pdf={'available' if fallback_pdf_path and Path(fallback_pdf_path).exists() else 'none'})..."
        )
        if fallback_pdf_path and Path(fallback_pdf_path).exists():
            corr_v2, summary = correct_title_hierarchy(content_list_v2, fallback_pdf_path)
            logger.info(
                f"✓ [LLM Hierarchy Fallback] Heuristics applied successfully: "
                f"{summary.get('corrected_count', 0)}/{summary.get('total_titles', 0)} headings corrected."
            )
            return corr_v2, summary
        logger.warning("⚠️ [LLM Hierarchy] Fallback PDF not available; returning unmodified heading levels.")
        return corrected_v2, {
            "method": "llm_semantic (fallback: no credentials)",
            "total_titles": len(title_entries),
            "corrected_count": 0,
            "title_details": [
                {
                    "id": e["id"],
                    "page": e["page_idx"] + 1,
                    "text": e["text"],
                    "old_level": e["orig_level"],
                    "new_level": e["orig_level"],
                }
                for e in title_entries
            ],
        }

    headings_input = [
        {
            "id": e["id"],
            "page": e["page_idx"] + 1,
            "text": e["text"],
            "current_level": e["orig_level"],
        }
        for e in title_entries
    ]

    provider_name = "azure_openai" if is_azure else "openai"
    model_name = az_deployment if is_azure else oai_model

    # Resolve timeout: explicit argument -> LLM_TIMEOUT env var -> default 300.0s (5 minutes)
    resolved_timeout = timeout
    if resolved_timeout is None:
        try:
            resolved_timeout = float(os.environ.get("LLM_TIMEOUT", "300.0"))
        except (ValueError, TypeError):
            resolved_timeout = 300.0

    if is_azure:
        logger.info(
            f"🤖 [LLM Hierarchy] Initiating Azure OpenAI call | deployment='{az_deployment}' "
            f"| endpoint='{az_endpoint}' | api_version='{az_version}' "
            f"| total_headings={len(title_entries)} | temperature={temperature} | timeout={resolved_timeout}s"
        )
    else:
        logger.info(
            f"🤖 [LLM Hierarchy] Initiating OpenAI call | model='{oai_model}' "
            f"| base_url='{oai_base or 'default'}' "
            f"| total_headings={len(title_entries)} | temperature={temperature} | timeout={resolved_timeout}s"
        )

    system_prompt = (
        "You are an expert document outline structure and markdown hierarchy corrector.\n"
        "You will receive a sequential JSON array of headings extracted from a document, each with 'id', 'page', 'text', and 'current_level'.\n"
        "Your task is to assign an optimal Markdown heading level (integer from 1 to 6) to each heading based on:\n"
        "1. Document semantic outline and numbering scheme:\n"
        "   - Top-level document title or major part: Level 1 (#)\n"
        "   - Major numbered sections (e.g. '1. Introduction', 'Chapter 2'): Level 1 or Level 2\n"
        "   - Sub-sections (e.g. '1.1 Background'): Level 2 or Level 3\n"
        "   - Sub-sub-sections (e.g. '1.1.1 Details'): Level 3 or Level 4\n"
        "   - Special callouts, sidebars, notes, appendix subheaders: appropriate relative child level\n"
        "2. Structural consistency: maintain logical parent-child nesting without skipping levels arbitrarily.\n\n"
        "CRITICAL RULES:\n"
        "- You MUST respond with a valid JSON object matching this schema:\n"
        '  {"headings": [{"id": <int>, "level": <int 1-6>}, ...]}\n'
        "- Include an entry for every input heading id.\n"
        "- The 'id' in output must exactly match the 'id' in input."
    )

    t0 = time.time()
    try:
        if is_azure:
            from openai import AzureOpenAI
            client = AzureOpenAI(
                azure_endpoint=az_endpoint,
                api_key=az_key,
                api_version=az_version,
                timeout=resolved_timeout,
            )
        else:
            from openai import OpenAI
            client = OpenAI(
                api_key=oai_key,
                base_url=oai_base,
                timeout=resolved_timeout,
            )

        logger.info(f"🚀 [LLM Hierarchy] Dispatching request with {len(headings_input)} headings to {provider_name} ('{model_name}')...")
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(headings_input, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
        )
        duration = time.time() - t0

        usage_info = ""
        usage = getattr(response, "usage", None)
        if usage:
            p_tok = getattr(usage, "prompt_tokens", None)
            c_tok = getattr(usage, "completion_tokens", None)
            t_tok = getattr(usage, "total_tokens", None)
            u_parts = []
            if p_tok is not None:
                u_parts.append(f"prompt={p_tok}")
            if c_tok is not None:
                u_parts.append(f"completion={c_tok}")
            if t_tok is not None:
                u_parts.append(f"total={t_tok}")
            if u_parts:
                usage_info = f" | tokens: {', '.join(u_parts)}"

        logger.info(
            f"📥 [LLM Hierarchy] Received response from '{model_name}' in {duration:.2f}s{usage_info}"
        )

        raw_content = response.choices[0].message.content or "{}"
        parsed = json.loads(raw_content)

        id_to_level: Dict[int, int] = {}
        if isinstance(parsed, dict):
            headings_list = parsed.get("headings") or parsed.get("titles") or parsed.get("items")
            if isinstance(headings_list, list):
                for item in headings_list:
                    if isinstance(item, dict) and "id" in item and "level" in item:
                        try:
                            id_to_level[int(item["id"])] = max(1, min(6, int(item["level"])))
                        except (ValueError, TypeError):
                            pass
            elif isinstance(headings_list, dict):
                for k, v in headings_list.items():
                    try:
                        id_to_level[int(k)] = max(1, min(6, int(v)))
                    except (ValueError, TypeError):
                        pass
            else:
                for k, v in parsed.items():
                    try:
                        id_to_level[int(k)] = max(1, min(6, int(v if not isinstance(v, dict) else v.get("level", 1))))
                    except (ValueError, TypeError):
                        pass
        elif isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and "id" in item and "level" in item:
                    try:
                        id_to_level[int(item["id"])] = max(1, min(6, int(item["level"])))
                    except (ValueError, TypeError):
                        pass

        corrected_count = 0
        for entry in title_entries:
            assigned_level = id_to_level.get(entry["id"])
            new_level = assigned_level if assigned_level is not None else entry["orig_level"] or 1
            entry["new_level"] = new_level
            if new_level != entry["orig_level"]:
                corrected_count += 1
            entry["block_ref"]["content"]["level"] = new_level

        logger.info(
            f"✓ [LLM Hierarchy] Applied mappings: {corrected_count}/{len(title_entries)} heading levels adjusted."
        )
        if corrected_count > 0:
            sample_changes = [
                f"'{e['text'][:25]}' (H{e['orig_level']} -> H{e['new_level']})"
                for e in title_entries if e["new_level"] != e["orig_level"]
            ]
            sample_msg = "; ".join(sample_changes[:4])
            if len(sample_changes) > 4:
                sample_msg += f"; ... and {len(sample_changes) - 4} more"
            logger.info(f"   ↳ Sample adjustments: {sample_msg}")
        else:
            logger.info("   ↳ All headings retained their original levels as verified optimal.")

        summary_stats = {
            "method": f"{provider_name}_semantic",
            "provider": provider_name,
            "model": model_name,
            "total_titles": len(title_entries),
            "corrected_count": corrected_count,
            "duration_seconds": round(duration, 3),
            "title_details": [
                {
                    "id": e["id"],
                    "page": e["page_idx"] + 1,
                    "text": e["text"],
                    "old_level": e["orig_level"],
                    "new_level": e["new_level"],
                }
                for e in title_entries
            ],
        }
        return corrected_v2, summary_stats

    except Exception as llm_err:
        duration = time.time() - t0
        logger.error(
            f"❌ [LLM Hierarchy] {provider_name.capitalize()} call failed after {duration:.2f}s: {llm_err}. "
            "Attempting fallback to font-size heuristics if PDF is available...",
            exc_info=True,
        )
        if fallback_pdf_path and Path(fallback_pdf_path).exists():
            corr_v2, summary = correct_title_hierarchy(content_list_v2, fallback_pdf_path)
            logger.info(
                f"✓ [LLM Hierarchy Fallback] Heuristics applied successfully after error: "
                f"{summary.get('corrected_count', 0)}/{summary.get('total_titles', 0)} headings corrected."
            )
            return corr_v2, summary

        logger.warning("⚠️ [LLM Hierarchy] Fallback PDF not available; returning unmodified heading levels.")
        return corrected_v2, {
            "method": f"{provider_name}_semantic (fallback: error {llm_err})",
            "provider": provider_name,
            "total_titles": len(title_entries),
            "corrected_count": 0,
            "title_details": [
                {
                    "id": e["id"],
                    "page": e["page_idx"] + 1,
                    "text": e["text"],
                    "old_level": e["orig_level"],
                    "new_level": e["orig_level"],
                }
                for e in title_entries
            ],
        }


def merge_consecutive_headings(
    content_list_v2: List[List[Dict[str, Any]]],
    separator: str = ": ",
) -> Tuple[List[List[Dict[str, Any]]], Dict[str, Any]]:
    """
    Scans content_list_v2 in document reading order to detect consecutive headings
    without intervening body text or content.

    When consecutive headings are found (e.g. 'Root Cause #1' followed by 'DELAYED TRACE PRESENTATION'),
    they are merged into a single combined heading:
        - Text is joined: 'Root Cause #1: DELAYED TRACE PRESENTATION'
          (If the first heading already ends in punctuation like ':' or '-', space is used).
        - Heading level takes the HIGHEST level (numerically lowest: min(levels)) among the merged titles.
        - Subsequent merged heading blocks in the run are converted into empty title blocks so they
          produce no duplicate output and no subtree cascade errors.

    Returns:
        (updated_content_list_v2, summary_stats)
    """
    flattened_blocks: List[Tuple[int, int, Dict[str, Any]]] = []
    for page_idx, page in enumerate(content_list_v2):
        for block_idx, block in enumerate(page):
            flattened_blocks.append((page_idx, block_idx, block))

    merges: List[Dict[str, Any]] = []
    curr_idx = 0
    total_blocks = len(flattened_blocks)

    while curr_idx < total_blocks:
        page_idx, block_idx, block = flattened_blocks[curr_idx]
        block_type = block.get("type", "")

        if block_type == "title":
            content_obj = block.get("content", {})
            title_spans = content_obj.get("title_content", [])
            primary_text = "".join(
                s.get("content", "") for s in title_spans if isinstance(s, dict)
            ).strip()

            if primary_text:
                consecutive_titles: List[Tuple[int, int, Dict[str, Any], str, int]] = [
                    (page_idx, block_idx, block, primary_text, int(content_obj.get("level", 1)))
                ]
                scan_idx = curr_idx + 1

                while scan_idx < total_blocks:
                    next_page_idx, next_block_idx, next_block = flattened_blocks[scan_idx]
                    next_type = next_block.get("type", "")
                    next_content = next_block.get("content", {})

                    # Skip non-content noise
                    if next_type in ("page_header", "page_footer", "page_number"):
                        scan_idx += 1
                        continue

                    # Substantive body content: paragraph, table, formula, or image
                    if next_type in ("paragraph", "table", "formula", "image"):
                        if next_type == "paragraph":
                            p_spans = next_content.get("paragraph_content", [])
                            p_text = "".join(
                                s.get("content", "") for s in p_spans if isinstance(s, dict)
                            ).strip()
                            if not p_text:
                                scan_idx += 1
                                continue
                        # Substantive content encountered; ends the consecutive run
                        break

                    # Found another consecutive heading
                    if next_type == "title":
                        next_title_spans = next_content.get("title_content", [])
                        next_text = "".join(
                            s.get("content", "") for s in next_title_spans if isinstance(s, dict)
                        ).strip()

                        if not next_text:
                            scan_idx += 1
                            continue

                        consecutive_titles.append((
                            next_page_idx,
                            next_block_idx,
                            next_block,
                            next_text,
                            int(next_content.get("level", 1)),
                        ))
                        scan_idx += 1
                        continue

                    # Any other unrecognized block type ends the run
                    break

                # If 2 or more consecutive headings exist, merge them
                if len(consecutive_titles) > 1:
                    # Level must be the HIGHEST level (numerically lowest: min(levels))
                    highest_level = min(t[4] for t in consecutive_titles)

                    # Join texts intelligently
                    combined_text_parts: List[str] = []
                    for idx, (_, _, _, t_text, _) in enumerate(consecutive_titles):
                        if idx == 0:
                            combined_text_parts.append(t_text)
                        else:
                            prev_t = combined_text_parts[-1].rstrip()
                            if prev_t.endswith((":", "-", "—", "/", "|")):
                                combined_text_parts.append(f" {t_text}")
                            else:
                                combined_text_parts.append(f"{separator}{t_text}")

                    merged_title_text = "".join(combined_text_parts).strip()

                    # Update the primary (first) block with merged text and highest level
                    primary_block = consecutive_titles[0][2]
                    primary_block["content"]["level"] = highest_level
                    primary_block["content"]["title_content"] = [
                        {"type": "text", "content": merged_title_text}
                    ]

                    # Clear out subsequent merged title blocks so they don't produce duplicate output
                    for _, _, sub_block, _, _ in consecutive_titles[1:]:
                        sub_block["content"]["title_content"] = []
                        sub_block["content"]["_merged_into_parent"] = True

                    merges.append({
                        "page": consecutive_titles[0][0] + 1,
                        "original_headings": [t[3] for t in consecutive_titles],
                        "original_levels": [t[4] for t in consecutive_titles],
                        "merged_heading": merged_title_text,
                        "assigned_level": highest_level,
                    })

                    logger.info(
                        f"🔗 [Consecutive Merge] Combined {len(consecutive_titles)} headings into: "
                        f"'{merged_title_text[:50]}' (assigned highest level: H{highest_level})"
                    )

                    # Advance curr_idx past the scanned cluster
                    curr_idx = scan_idx
                    continue

        curr_idx += 1

    summary_stats = {
        "merged_headings_count": len(merges),
        "merge_details": merges,
    }

    if merges:
        logger.info(f"✓ [Consecutive Merge] Combined {len(merges)} consecutive heading group(s).")

    return content_list_v2, summary_stats


def demote_consecutive_headings(
    content_list_v2: List[List[Dict[str, Any]]],
) -> Tuple[List[List[Dict[str, Any]]], Dict[str, Any]]:
    """Backward-compatibility alias pointing to merge_consecutive_headings."""
    return merge_consecutive_headings(content_list_v2)


def convert_v2_to_markdown(
    content_list_v2: List[List[Dict[str, Any]]],
    include_footnotes: bool = False,
    filter_images: bool = True,
    filter_headers_footers: bool = True,
) -> str:
    """
    Renders clean Markdown from content_list_v2:
    - Formats titles using Markdown hashes matching their corrected level.
    - Preserves paragraphs, formatting, tables, formulas.
    - Filters out images (if filter_images=True).
    - Filters out page_header, page_footer, page_number (if filter_headers_footers=True).
    - Optionally includes footnotes.
    """
    md_blocks: List[str] = []

    for page_idx, page in enumerate(content_list_v2):
        for block in page:
            block_type = block.get("type", "")
            content = block.get("content", {})

            # Filter out headers, footers, page numbers
            if filter_headers_footers and block_type in (
                "page_header",
                "page_footer",
                "page_number",
            ):
                continue

            # Filter out images
            if filter_images and block_type == "image":
                continue

            # Footnotes filter
            if not include_footnotes and block_type == "page_footnote":
                continue

            if block_type == "title":
                level = max(1, min(6, content.get("level", 1)))
                title_spans = content.get("title_content", [])
                text = "".join(
                    s.get("content", "") for s in title_spans if isinstance(s, dict)
                ).strip()
                if text:
                    prefix = "#" * level
                    md_blocks.append(f"{prefix} {text}")

            elif block_type == "paragraph":
                para_spans = content.get("paragraph_content", [])
                text = "".join(
                    s.get("content", "") for s in para_spans if isinstance(s, dict)
                ).strip()
                if text:
                    md_blocks.append(text)

            elif block_type == "page_footnote":
                fn_spans = content.get("page_footnote_content", [])
                text = "".join(
                    s.get("content", "") for s in fn_spans if isinstance(s, dict)
                ).strip()
                if text:
                    md_blocks.append(f"> {text}")

            elif block_type == "table":
                # Handle table markdown/html representation if present
                table_body = content.get("table_content") or content.get("html") or content.get("text")
                if table_body:
                    md_blocks.append(str(table_body).strip())

            elif block_type == "formula":
                # Handle math formulas if present
                formula_text = content.get("formula_content") or content.get("text")
                if formula_text:
                    md_blocks.append(f"$${formula_text}$$")

    # Combine blocks with double newlines
    result_md = "\n\n".join(md_blocks)
    return result_md + "\n" if result_md else ""


def process_content_list_v2_to_clean_markdown(
    v2_json_path: Union[str, Path],
    pdf_path: Optional[Union[str, Path]] = None,
    output_v2_path: Optional[Union[str, Path]] = None,
    output_md_path: Optional[Union[str, Path]] = None,
    filter_images: bool = True,
    include_footnotes: bool = False,
    use_llm: bool = False,
    openai_api_key: Optional[str] = None,
    openai_model: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    azure_endpoint: Optional[str] = None,
    azure_api_key: Optional[str] = None,
    azure_deployment: Optional[str] = None,
    azure_api_version: str = "2024-02-15-preview",
    temperature: float = 0.0,
    timeout: Optional[float] = None,
) -> Tuple[Path, Path, Dict[str, Any]]:
    """
    End-to-end pipeline:
    1. Reads original content_list_v2.json.
    2. Corrects hierarchy into a duplicate JSON file:
       - If use_llm=True: uses OpenAI / Azure OpenAI semantic outline analysis (heuristics disabled).
       - If use_llm=False: uses dominant font-size heuristics via PyMuPDF.
    3. Produces filtered, clean Markdown.
    """
    v2_p = Path(v2_json_path).resolve()
    pdf_p = Path(pdf_path).resolve() if pdf_path else None

    if not v2_p.exists():
        raise FileNotFoundError(f"V2 JSON file not found: {v2_p}")
    if not use_llm and (pdf_p is None or not pdf_p.exists()):
        raise FileNotFoundError(f"PDF file not found for font heuristics: {pdf_p}")

    with _safe_open(v2_p, "r", encoding="utf-8") as f:
        v2_data = json.load(f)

    # 1-3. Correct hierarchy into duplicated structure
    if use_llm:
        corrected_v2, summary = correct_title_hierarchy_llm(
            content_list_v2=v2_data,
            openai_api_key=openai_api_key,
            openai_model=openai_model,
            openai_base_url=openai_base_url,
            azure_endpoint=azure_endpoint,
            azure_api_key=azure_api_key,
            azure_deployment=azure_deployment,
            azure_api_version=azure_api_version,
            temperature=temperature,
            timeout=timeout,
            fallback_pdf_path=pdf_p,
        )
    else:
        corrected_v2, summary = correct_title_hierarchy(v2_data, pdf_p)

        # Sequential scan to normalize heading skips (e.g. H1 -> H3 -> H6 becomes H1 -> H2 -> H3)
        corrected_v2, norm_summary = normalize_heading_levels(corrected_v2)
        summary["level_normalization_count"] = norm_summary.get("normalized_count", 0)
        summary["level_normalization_details"] = norm_summary.get("adjustments", [])

    # Post-scan: Merge consecutive headings without intervening body paragraphs or content
    corrected_v2, merge_summary = merge_consecutive_headings(corrected_v2)
    summary["merged_consecutive_headings"] = merge_summary.get("merged_headings_count", 0)
    summary["merge_details"] = merge_summary.get("merge_details", [])

    # Save duplicated corrected v2
    if output_v2_path is None:
        output_v2_path = v2_p.parent / f"{v2_p.stem}_corrected.json"
    else:
        output_v2_path = Path(output_v2_path).resolve()

    with _safe_open(output_v2_path, "w", encoding="utf-8") as f:
        json.dump(corrected_v2, f, indent=4, ensure_ascii=False)

    # 4. Generate clean markdown
    clean_md = convert_v2_to_markdown(
        corrected_v2,
        include_footnotes=include_footnotes,
        filter_images=filter_images,
        filter_headers_footers=True,
    )

    if output_md_path is None:
        # e.g., <doc_stem>_clean.md
        stem_name = v2_p.stem.replace("_content_list_v2", "").replace("_v2list", "")
        output_md_path = v2_p.parent / f"{stem_name}_clean.md"
    else:
        output_md_path = Path(output_md_path).resolve()

    with _safe_open(output_md_path, "w", encoding="utf-8") as f:
        f.write(clean_md)

    logger.info(
        f"Corrected V2 saved to {output_v2_path.name} (strategy={summary.get('method', 'heuristics')}), "
        f"Clean Markdown saved to {output_md_path.name}"
    )
    return output_v2_path, output_md_path, summary
