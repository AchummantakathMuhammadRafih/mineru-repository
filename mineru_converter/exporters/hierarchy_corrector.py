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


def correct_title_hierarchy_llm(
    content_list_v2: List[List[Dict[str, Any]]],
    azure_endpoint: Optional[str] = None,
    azure_api_key: Optional[str] = None,
    azure_deployment: Optional[str] = None,
    azure_api_version: str = "2024-02-15-preview",
    temperature: float = 0.0,
    timeout: float = 60.0,
    fallback_pdf_path: Optional[Union[str, Path]] = None,
) -> Tuple[List[List[Dict[str, Any]]], Dict[str, Any]]:
    """
    Parallel LLM Flow using Azure OpenAI:
    1. Clone content_list_v2 so original is never modified.
    2. Extract only the 'type': 'title' elements from content_list_v2, assigning each a unique sequential id.
    3. Send the complete list of headings to Azure OpenAI with a structured JSON schema.
    4. Parse returned JSON mapping id -> corrected level (1..6).
    5. Update 'level' in the duplicated structure.
    6. If Azure OpenAI credentials are missing or the API call fails, gracefully falls back
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
            "method": "azure_openai_semantic",
            "total_titles": 0,
            "corrected_count": 0,
            "title_details": [],
        }

    # Resolve Azure OpenAI credentials from arguments or environment variables
    endpoint = (azure_endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT") or "").strip().rstrip("/")
    api_key = (azure_api_key or os.environ.get("AZURE_OPENAI_API_KEY") or "").strip()
    deployment = (
        azure_deployment
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_MODEL")
        or "gpt-4o-mini"
    ).strip()
    api_version = (azure_api_version or os.environ.get("AZURE_OPENAI_API_VERSION") or "2024-02-15-preview").strip()

    if not endpoint or not api_key:
        logger.warning(
            "⚠️ [LLM Hierarchy] Azure OpenAI credentials missing (endpoint or api_key not set). "
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
        # Fallback to unchanged levels
        return corrected_v2, {
            "method": "azure_openai_semantic (fallback: no credentials)",
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

    logger.info(
        f"🤖 [LLM Hierarchy] Initiating Azure OpenAI call | deployment='{deployment}' "
        f"| endpoint='{endpoint}' | api_version='{api_version}' "
        f"| total_headings={len(title_entries)} | temperature={temperature} | timeout={timeout}s"
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
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout,
        )

        logger.info(f"🚀 [LLM Hierarchy] Dispatching request with {len(headings_input)} headings to '{deployment}'...")
        response = client.chat.completions.create(
            model=deployment,
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
            f"📥 [LLM Hierarchy] Received response from '{deployment}' in {duration:.2f}s{usage_info}"
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
            "method": "azure_openai_semantic",
            "model": deployment,
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
            f"❌ [LLM Hierarchy] Azure OpenAI call failed after {duration:.2f}s: {llm_err}. "
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
            "method": f"azure_openai_semantic (fallback: error {llm_err})",
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
    azure_endpoint: Optional[str] = None,
    azure_api_key: Optional[str] = None,
    azure_deployment: Optional[str] = None,
    azure_api_version: str = "2024-02-15-preview",
    temperature: float = 0.0,
    timeout: float = 60.0,
) -> Tuple[Path, Path, Dict[str, Any]]:
    """
    End-to-end pipeline:
    1. Reads original content_list_v2.json.
    2. Corrects hierarchy into a duplicate JSON file:
       - If use_llm=True: uses Azure OpenAI semantic outline analysis (heuristics disabled).
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
