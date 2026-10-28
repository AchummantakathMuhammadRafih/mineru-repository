"""Markdown-level hierarchy corrector and cleaner for Marker outputs."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re

import markdown_it
from mineru_converter.utils.logging_utils import get_logger

logger = get_logger()


def correct_markdown_heading_hierarchy(
    raw_markdown: str,
    collapse_skips: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    """
    Scans a raw Markdown document (like that produced by Marker), inspects all headings,
    and applies hierarchy normalization:
    1. Ensures the first heading is elevated to H1 if not already.
    2. Compresses level gaps (e.g. H1 followed immediately by H3/H6 without H2)
       so headings descend at most 1 level deeper at a time.
    3. Respects consecutive heading merges if two adjacent identical-level or inverted headings exist.

    Returns:
        (clean_markdown_text, summary_stats)
    """
    if not raw_markdown or not raw_markdown.strip():
        return raw_markdown, {"headings_count": 0, "adjusted_count": 0}

    parser = markdown_it.MarkdownIt()
    tokens = parser.parse(raw_markdown)

    heading_tokens = []
    for token in tokens:
        if token.type == "heading_open":
            level = int(token.tag[1])
            start_line = token.map[0]
            heading_tokens.append({"level": level, "start_line": start_line})

    if not heading_tokens:
        return raw_markdown, {"headings_count": 0, "adjusted_count": 0}

    lines = raw_markdown.splitlines(keepends=True)
    adjustments = []

    # Sequential pass: track hierarchy stack to normalize skips
    # chain of (orig_level, new_level)
    chain: List[Tuple[int, int]] = []

    for idx, h in enumerate(heading_tokens):
        orig_level = h["level"]
        line_idx = h["start_line"]
        line_str = lines[line_idx]

        if idx == 0:
            # First heading is always H1
            new_level = 1
            chain = [(orig_level, 1)]
        else:
            prev_orig, prev_new = chain[-1]
            if orig_level > prev_orig:
                # Descending into a child subsection: descend at most 1 level
                new_level = min(6, prev_new + 1)
                chain.append((orig_level, new_level))
            elif orig_level == prev_orig:
                # Sibling
                new_level = prev_new
            else:
                # Ascending back to ancestor
                while chain and chain[-1][0] > orig_level:
                    chain.pop()
                if chain and chain[-1][0] == orig_level:
                    new_level = chain[-1][1]
                elif chain:
                    new_level = min(6, chain[-1][1] + 1)
                    chain.append((orig_level, new_level))
                else:
                    new_level = 1
                    chain = [(orig_level, 1)]

        if new_level != orig_level:
            m = re.match(r"^(\s*)(#{1,6})(\s+.*)$", line_str)
            if m:
                new_hashes = "#" * new_level
                lines[line_idx] = f"{m.group(1)}{new_hashes}{m.group(3)}"
                adjustments.append({
                    "line": line_idx + 1,
                    "old_level": orig_level,
                    "new_level": new_level,
                    "text": line_str.strip()[:60]
                })

    normalized_md = "".join(lines)

    # Post-scan: Merge consecutive headings without intervening body content
    clean_md, merge_summary = merge_consecutive_markdown_headings(normalized_md)

    summary = {
        "headings_count": len(heading_tokens),
        "adjusted_count": len(adjustments),
        "adjustments": adjustments,
        "merged_headings_count": merge_summary.get("merged_count", 0),
        "merge_details": merge_summary.get("merges", []),
    }
    return clean_md, summary


def merge_consecutive_markdown_headings(
    markdown_text: str,
    separator: str = ": ",
) -> Tuple[str, Dict[str, Any]]:
    """
    Post-scan pass on normalized Markdown:
    Scans for consecutive heading lines that have no intervening body text/paragraphs.
    Only merges if the headings are at the same level (e.g. H2 followed by H2)
    or inverted (e.g. H3 followed by H2). Preserves natural parent -> child (H2 -> H3).
    When merged:
    - Text is combined with separator (e.g. '# Chapter 9: Test environments')
    - Level is set to the highest level (numerically lowest: min(levels))
    """
    lines = markdown_text.splitlines(keepends=True)
    heading_pattern = re.compile(r"^(\s*)(#{1,6})\s+(.*?)\s*$")

    # Find line indices of all headings and intervening non-empty non-whitespace lines
    parsed_lines = []
    for idx, line in enumerate(lines):
        m = heading_pattern.match(line)
        if m:
            level = len(m.group(2))
            text = m.group(3).strip()
            parsed_lines.append((idx, "heading", level, text, m.group(1)))
        elif line.strip():
            parsed_lines.append((idx, "body", 0, line.strip(), ""))

    merges = []
    lines_to_delete = set()
    lines_to_replace = {}

    curr_idx = 0
    total_parsed = len(parsed_lines)

    while curr_idx < total_parsed:
        item = parsed_lines[curr_idx]
        if item[1] == "heading":
            consecutive = [item]
            scan_idx = curr_idx + 1

            while scan_idx < total_parsed:
                next_item = parsed_lines[scan_idx]
                if next_item[1] != "heading":
                    break

                prev_level = consecutive[-1][2]
                next_level = next_item[2]

                # Natural parent -> child hierarchy (e.g. H2 followed by H3):
                # Do NOT combine; keep as separate headings.
                if prev_level < next_level:
                    break

                consecutive.append(next_item)
                scan_idx += 1

            if len(consecutive) > 1:
                highest_level = min(h[2] for h in consecutive)
                combined_parts = []
                for c_idx, h in enumerate(consecutive):
                    t_text = h[3]
                    if c_idx == 0:
                        combined_parts.append(t_text)
                    else:
                        prev_t = combined_parts[-1].rstrip()
                        if prev_t.endswith((":", "-", "—", "/", "|")):
                            combined_parts.append(f" {t_text}")
                        else:
                            combined_parts.append(f"{separator}{t_text}")

                merged_text = "".join(combined_parts).strip()
                primary_line_idx = consecutive[0][0]
                leading_ws = consecutive[0][4]
                new_hashes = "#" * highest_level
                newline_char = "\n" if lines[primary_line_idx].endswith("\n") else ""
                lines_to_replace[primary_line_idx] = f"{leading_ws}{new_hashes} {merged_text}{newline_char}"

                for h in consecutive[1:]:
                    lines_to_delete.add(h[0])

                merges.append({
                    "primary_line": primary_line_idx + 1,
                    "original_headings": [h[3] for h in consecutive],
                    "original_levels": [h[2] for h in consecutive],
                    "merged_heading": merged_text,
                    "assigned_level": highest_level,
                })

                logger.info(
                    f"🔗 [Markdown Merge] Combined {len(consecutive)} headings into: "
                    f"'{merged_text[:50]}' (assigned level: H{highest_level})"
                )

                curr_idx = scan_idx
                continue

        curr_idx += 1

    if not lines_to_delete and not lines_to_replace:
        return markdown_text, {"merged_count": 0, "merges": []}

    output_lines = []
    for idx, line in enumerate(lines):
        if idx in lines_to_delete:
            continue
        if idx in lines_to_replace:
            output_lines.append(lines_to_replace[idx])
        else:
            output_lines.append(line)

    return "".join(output_lines), {"merged_count": len(merges), "merges": merges}
