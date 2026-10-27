"""Postprocessing utilities for extracted Markdown and MinerU artifacts."""

from pathlib import Path
from typing import Optional, Tuple
import re
import shutil
import markdown_it

from mineru_converter.models.schemas import OutputFileManifest
from mineru_converter.utils.logging_utils import get_logger

logger = get_logger()


def normalize_markdown_content(markdown_text: str) -> Tuple[str, bool, str]:
    """
    Parse Markdown content with markdown-it-py, inspect all headers, and ensure
    the first header is elevated to the top heading level (# / H1).
    
    If the first header is already the highest level (H1) and no subsequent header
    conflicts with it, it is left untouched. Otherwise, the first header is
    promoted to H1 and any subsequent H1 headers are demoted to H2.
    
    Returns:
        Tuple of (updated_markdown_text, was_modified, reason_message)
    """
    if not markdown_text or not markdown_text.strip():
        return markdown_text, False, "Empty markdown content"

    parser = markdown_it.MarkdownIt()
    tokens = parser.parse(markdown_text)

    headings = []
    for token in tokens:
        if token.type == "heading_open":
            level = int(token.tag[1])  # 'h1' -> 1, 'h2' -> 2, etc.
            start_line = token.map[0]  # 0-indexed line in text
            markup = token.markup
            headings.append({"level": level, "start_line": start_line, "markup": markup})

    if not headings:
        return markdown_text, False, "No headings found in markdown"

    first_heading = headings[0]
    first_level = first_heading["level"]
    other_headings = headings[1:]
    other_levels = [h["level"] for h in other_headings]

    # Check if the first header is already the unique highest level (H1)
    if first_level == 1 and all(lvl > 1 for lvl in other_levels):
        return markdown_text, False, "First header is already top H1"

    lines = markdown_text.splitlines(keepends=True)

    # Elevate first heading to Level 1 (#)
    first_line_idx = first_heading["start_line"]
    first_line = lines[first_line_idx]
    m = re.match(r"^(\s*)(#{1,6})(\s+.*)$", first_line)
    if m:
        lines[first_line_idx] = f"{m.group(1)}#{m.group(3)}"
    else:
        # If Setext or other syntax
        lines[first_line_idx] = f"# {first_line.strip()}\n"

    # Demote any subsequent H1 headings to H2 so the first header remains the top header
    demoted_count = 0
    for h in other_headings:
        if h["level"] == 1:
            s_line_idx = h["start_line"]
            s_line = lines[s_line_idx]
            sm = re.match(r"^(\s*)(#{1,6})(\s+.*)$", s_line)
            if sm:
                lines[s_line_idx] = f"{sm.group(1)}##{sm.group(3)}"
                demoted_count += 1

    new_text = "".join(lines)
    msg = f"Promoted first header from H{first_level} to H1"
    if demoted_count > 0:
        msg += f" and demoted {demoted_count} conflicting subsequent H1 header(s) to H2"

    return new_text, True, msg


def normalize_markdown_headers(markdown_path: Path) -> bool:
    """
    Read the markdown file at `markdown_path`, normalize heading levels so the
    first header is top-level H1, and write the file back in-place if modified.
    
    Args:
        markdown_path: Path to the .md file.
        
    Returns:
        True if the file was modified, False otherwise.
    """
    if not markdown_path or not markdown_path.is_file():
        return False

    try:
        content = markdown_path.read_text(encoding="utf-8")
        updated_content, modified, reason = normalize_markdown_content(content)
        if modified:
            markdown_path.write_text(updated_content, encoding="utf-8")
            logger.info(f"Header normalization ({markdown_path.name}): {reason}")
            return True
        else:
            logger.debug(f"Header normalization skipped ({markdown_path.name}): {reason}")
            return False
    except Exception as exc:
        logger.warning(f"Failed to normalize headers for {markdown_path.name}: {exc}")
        return False


def strip_markdown_images(markdown_path: Path) -> bool:
    """
    Remove all image references (![...](...) and <img ...>) from the Markdown file.
    
    Args:
        markdown_path: Path to the .md file.
        
    Returns:
        True if images were stripped, False otherwise.
    """
    if not markdown_path or not markdown_path.is_file():
        return False

    try:
        content = markdown_path.read_text(encoding="utf-8")
        # Matches ![alt](src) and <img src=...>
        image_pattern = re.compile(
            r"(!\[.*?\]\([^)]+\)|<img[^\>]+src=[\"'][^\"']+[\"'][^\>]*>)[ \t]*(\r?\n)?",
            re.MULTILINE
        )
        new_content, count = image_pattern.subn("", content)
        if count > 0:
            # Clean up excessive 3+ newlines left by removed lines
            new_content = re.sub(r"\n{3,}", "\n\n", new_content)
            markdown_path.write_text(new_content, encoding="utf-8")
            logger.info(f"Stripped {count} image reference(s) from {markdown_path.name}")
            return True
        return False
    except Exception as exc:
        logger.warning(f"Failed to strip images from {markdown_path.name}: {exc}")
        return False


def create_standard_aliases(
    manifest: OutputFileManifest,
    create_v_aliases: bool = True
) -> None:
    """
    Create convenient file aliases for content lists if desired (e.g., _v1list.json, _v2list.json).
    
    Args:
        manifest: Output manifest containing resolved file paths.
        create_v_aliases: If True, creates copy/symlink aliases for easier access.
    """
    if not create_v_aliases or not manifest.output_directory:
        return

    doc_dir = manifest.output_directory
    stem = manifest.document_name

    # Create v1 alias if needed
    if manifest.content_list_v1_path and manifest.content_list_v1_path.exists():
        v1_alias = doc_dir / f"{stem}_v1list.json"
        if not v1_alias.exists():
            try:
                shutil.copy2(manifest.content_list_v1_path, v1_alias)
            except Exception as exc:
                logger.debug(f"Could not create v1 alias: {exc}")

    # Create v2 alias if needed
    if manifest.content_list_v2_path and manifest.content_list_v2_path.exists():
        v2_alias = doc_dir / f"{stem}_v2list.json"
        if not v2_alias.exists():
            try:
                shutil.copy2(manifest.content_list_v2_path, v2_alias)
            except Exception as exc:
                logger.debug(f"Could not create v2 alias: {exc}")

