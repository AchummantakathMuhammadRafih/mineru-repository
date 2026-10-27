"""Unit tests for hierarchy corrector and markdown generator."""

import pytest
from pathlib import Path
from mineru_converter.exporters.hierarchy_corrector import (
    cluster_font_sizes,
    convert_v2_to_markdown,
    correct_title_hierarchy,
    process_content_list_v2_to_clean_markdown,
)


def test_cluster_font_sizes():
    sizes = [35.0, 14.0, 10.0]
    mapping = cluster_font_sizes(sizes)
    assert mapping[35.0] == 1
    assert mapping[14.0] == 2
    assert mapping[10.0] == 3


def test_convert_v2_to_markdown_filters():
    mock_v2 = [
        [
            {"type": "page_header", "content": {"page_header_content": [{"type": "text", "content": "Header"}]}},
            {"type": "page_number", "content": {"page_number_content": [{"type": "text", "content": "1"}]}},
            {"type": "image", "content": {"image_source": {"path": "img.png"}}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Sample Title"}], "level": 1}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Sample body text."}]}},
            {"type": "page_footer", "content": {"page_footer_content": [{"type": "text", "content": "Footer"}]}},
        ]
    ]

    md = convert_v2_to_markdown(mock_v2, filter_images=True, filter_headers_footers=True)
    assert "# Sample Title" in md
    assert "Sample body text." in md
    assert "Header" not in md
    assert "Footer" not in md
    assert "img.png" not in md


def test_process_content_list_v2_on_existing_document(tmp_path):
    v2_path = Path("output/Medical_Device_Use_Error_-_Root_Cause_Analysis_-_FOR_EMERGO_INTERNAL_USE_ONLY/auto/Medical_Device_Use_Error_-_Root_Cause_Analysis_-_FOR_EMERGO_INTERNAL_USE_ONLY_content_list_v2.json")
    pdf_path = Path("output/Medical_Device_Use_Error_-_Root_Cause_Analysis_-_FOR_EMERGO_INTERNAL_USE_ONLY/auto/Medical_Device_Use_Error_-_Root_Cause_Analysis_-_FOR_EMERGO_INTERNAL_USE_ONLY_origin.pdf")

    if not v2_path.exists() or not pdf_path.exists():
        pytest.skip("Test sample files not present")

    out_v2 = tmp_path / "test_corr_v2.json"
    out_md = tmp_path / "test_clean.md"

    corr_v2_p, clean_md_p, summary = process_content_list_v2_to_clean_markdown(
        v2_json_path=v2_path,
        pdf_path=pdf_path,
        output_v2_path=out_v2,
        output_md_path=out_md,
        filter_images=True,
    )

    assert out_v2.exists()
    assert out_md.exists()
    assert summary["total_titles"] >= 7
    # Verify SIDEBAR was corrected from level 2 to level 3 based on 10pt font size
    sidebar_item = [t for t in summary["title_details"] if "SIDEBAR" in t["text"]][0]
    assert sidebar_item["old_level"] == 2
    assert sidebar_item["new_level"] == 3

    # Check markdown has ### for Sidebar
    md_text = out_md.read_text(encoding="utf-8")
    assert "### SIDEBAR 2.1" in md_text
    assert "# Introduction" in md_text
    assert "## STEP 1: DEFINE THE USE ERROR" in md_text
