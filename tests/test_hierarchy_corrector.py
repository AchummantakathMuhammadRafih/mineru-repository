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
    # Check markdown has content and titles
    md_text = out_md.read_text(encoding="utf-8")
    assert len(md_text) > 50
    assert len(summary["title_details"]) >= 7

    # Verify SIDEBAR was corrected from level 2 to level 3 based on 10pt font size (if present in sample)
    sidebar_items = [t for t in summary["title_details"] if "SIDEBAR" in t["text"]]
    if sidebar_items:
        sidebar_item = sidebar_items[0]
        assert sidebar_item["old_level"] == 2
        assert sidebar_item["new_level"] == 3
        assert "### SIDEBAR 2.1" in md_text


def test_correct_title_hierarchy_llm_success(monkeypatch):
    """Test LLM-based heading hierarchy correction with mocked AzureOpenAI client."""
    import json
    from unittest.mock import MagicMock
    from mineru_converter.exporters.hierarchy_corrector import correct_title_hierarchy_llm

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Document Title"}], "level": 1}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Intro paragraph."}]}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "1. Introduction"}], "level": 1}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "1.1 Background"}], "level": 1}},
        ]
    ]

    mock_llm_response = {
        "headings": [
            {"id": 0, "level": 1},
            {"id": 1, "level": 2},
            {"id": 2, "level": 3},
        ]
    }

    mock_chat_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(mock_llm_response)
    mock_chat_completion.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_chat_completion

    import openai
    monkeypatch.setattr(openai, "AzureOpenAI", lambda *args, **kwargs: mock_client)

    corrected_v2, summary = correct_title_hierarchy_llm(
        content_list_v2=mock_v2,
        azure_endpoint="https://fake-resource.openai.azure.com/",
        azure_api_key="fake-test-key",
        azure_deployment="gpt-4o-mini",
    )

    assert summary["method"] == "azure_openai_semantic"
    assert summary["total_titles"] == 3
    assert summary["corrected_count"] == 2  # 1. Introduction and 1.1 Background changed
    assert corrected_v2[0][0]["content"]["level"] == 1
    assert corrected_v2[0][2]["content"]["level"] == 2
    assert corrected_v2[0][3]["content"]["level"] == 3

    # Generate markdown from corrected v2
    md = convert_v2_to_markdown(corrected_v2)
    assert "# Document Title" in md
    assert "## 1. Introduction" in md
    assert "### 1.1 Background" in md


def test_correct_title_hierarchy_llm_missing_credentials(monkeypatch):
    """Test LLM-based heading hierarchy correction handles missing credentials gracefully."""
    import os
    from mineru_converter.exporters.hierarchy_corrector import correct_title_hierarchy_llm

    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Doc Title"}], "level": 1}},
        ]
    ]

    corrected_v2, summary = correct_title_hierarchy_llm(
        content_list_v2=mock_v2,
        azure_endpoint=None,
        azure_api_key=None,
    )

    assert "fallback" in summary["method"]
    assert corrected_v2[0][0]["content"]["level"] == 1


def test_process_content_list_v2_with_llm_flag(tmp_path, monkeypatch):
    """Test end-to-end process_content_list_v2_to_clean_markdown with use_llm=True."""
    import json
    from unittest.mock import MagicMock

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Root Cause Analysis"}], "level": 1}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Section 1"}], "level": 1}},
        ]
    ]

    v2_json = tmp_path / "test_v2.json"
    v2_json.write_text(json.dumps(mock_v2), encoding="utf-8")

    mock_llm_response = {
        "headings": [
            {"id": 0, "level": 1},
            {"id": 1, "level": 2},
        ]
    }

    mock_chat_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(mock_llm_response)
    mock_chat_completion.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_chat_completion

    import openai
    monkeypatch.setattr(openai, "AzureOpenAI", lambda *args, **kwargs: mock_client)

    out_v2 = tmp_path / "out_v2.json"
    out_md = tmp_path / "out.md"

    res_v2, res_md, summary = process_content_list_v2_to_clean_markdown(
        v2_json_path=v2_json,
        pdf_path=None,  # PDF not needed when use_llm=True!
        output_v2_path=out_v2,
        output_md_path=out_md,
        use_llm=True,
        azure_endpoint="https://fake.openai.azure.com",
        azure_api_key="fake-key",
    )

    assert out_v2.exists()
    assert out_md.exists()
    assert summary["method"] == "azure_openai_semantic"
    md_content = out_md.read_text(encoding="utf-8")
    assert "# Root Cause Analysis" in md_content
    assert "## Section 1" in md_content


def test_correct_title_hierarchy_llm_logging(monkeypatch, caplog):
    """Test that LLM hierarchy correction logs request details, token usage, and adjustments."""
    import logging
    import json
    from unittest.mock import MagicMock
    from mineru_converter.exporters.hierarchy_corrector import correct_title_hierarchy_llm

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Title 1"}], "level": 1}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Sub 1.1"}], "level": 1}},
        ]
    ]

    mock_llm_response = {
        "headings": [
            {"id": 0, "level": 1},
            {"id": 1, "level": 2},
        ]
    }

    mock_chat_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(mock_llm_response)
    mock_chat_completion.choices = [mock_choice]
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 42
    mock_usage.completion_tokens = 15
    mock_usage.total_tokens = 57
    mock_chat_completion.usage = mock_usage

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_chat_completion

    import openai
    monkeypatch.setattr(openai, "AzureOpenAI", lambda *args, **kwargs: mock_client)

    with caplog.at_level(logging.INFO):
        corr_v2, summary = correct_title_hierarchy_llm(
            content_list_v2=mock_v2,
            azure_endpoint="https://fake.openai.azure.com",
            azure_api_key="fake-key",
            azure_deployment="gpt-4o-mini",
        )

    log_text = caplog.text
    assert "Initiating Azure OpenAI call" in log_text
    assert "Dispatching request with 2 headings" in log_text
    assert "Received response from 'gpt-4o-mini'" in log_text
    assert "prompt=42" in log_text
    assert "total=57" in log_text
    assert "Applied mappings: 1/2 heading levels adjusted." in log_text
    assert "Sample adjustments:" in log_text


def test_logging_utils_excludes_access_logs(tmp_path):
    """Verify add_file_handler_to_loggers does not attach rotating file handler to uvicorn.access."""
    import logging
    from mineru_converter.utils.logging_utils import add_file_handler_to_loggers, LOGGER_NAME

    log_file = tmp_path / "test_server.log"
    handler = add_file_handler_to_loggers(log_file)

    app_logger = logging.getLogger(LOGGER_NAME)
    uvicorn_err = logging.getLogger("uvicorn.error")
    uvicorn_acc = logging.getLogger("uvicorn.access")

    assert handler in app_logger.handlers
    assert handler in uvicorn_err.handlers
    assert handler not in uvicorn_acc.handlers
    assert uvicorn_acc.disabled is True
    assert uvicorn_acc.propagate is False


def test_logging_utils_attaches_loguru(tmp_path):
    """Verify loguru messages (used by MinerU) are written to the rotating log file."""
    from loguru import logger as loguru_logger
    from mineru_converter.utils.logging_utils import add_file_handler_to_loggers

    log_file = tmp_path / "test_mineru_loguru.log"
    handler = add_file_handler_to_loggers(log_file)

    loguru_logger.info("MinerU model init cost: 1.23s")
    loguru_logger.debug("MinerU infer finished, cost: 0.45s")
    handler.flush()

    content = log_file.read_text(encoding="utf-8")
    assert "MinerU model init cost: 1.23s" in content
    assert "MinerU infer finished, cost: 0.45s" in content


def test_demote_consecutive_headings_two_same_level():
    """Test that two consecutive headings at the same level without paragraph demote the second one."""
    from mineru_converter.exporters.hierarchy_corrector import demote_consecutive_headings

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Use Error"}], "level": 2}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "PERFORMED INACCURATE MASS TRACE"}], "level": 2}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Two participants traced..."}]}},
        ]
    ]

    updated_v2, summary = demote_consecutive_headings(mock_v2)
    assert summary["consecutive_demotions"] == 1
    # First stays H2
    assert updated_v2[0][0]["content"]["level"] == 2
    # Second demoted to H3
    assert updated_v2[0][1]["content"]["level"] == 3


def test_demote_consecutive_headings_three_cascade():
    """Test that 3 consecutive headings cascade downwards (e.g. H2, H2, H2 -> H2, H3, H4)."""
    from mineru_converter.exporters.hierarchy_corrector import demote_consecutive_headings

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Category A"}], "level": 2}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Subcategory B"}], "level": 2}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Item C"}], "level": 2}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Body text for Item C"}]}},
        ]
    ]

    updated_v2, summary = demote_consecutive_headings(mock_v2)
    assert summary["consecutive_demotions"] == 2
    assert updated_v2[0][0]["content"]["level"] == 2
    assert updated_v2[0][1]["content"]["level"] == 3
    assert updated_v2[0][2]["content"]["level"] == 4


def test_demote_consecutive_headings_with_intervening_paragraph():
    """Test that headings separated by a paragraph are untouched."""
    from mineru_converter.exporters.hierarchy_corrector import demote_consecutive_headings

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "First Section"}], "level": 2}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Paragraph in section 1"}]}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Second Section"}], "level": 2}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Paragraph in section 2"}]}},
        ]
    ]

    updated_v2, summary = demote_consecutive_headings(mock_v2)
    assert summary["consecutive_demotions"] == 0
    assert updated_v2[0][0]["content"]["level"] == 2
    assert updated_v2[0][2]["content"]["level"] == 2


def test_demote_consecutive_headings_with_intervening_image():
    """Test that headings separated by an image are not treated as consecutive."""
    from mineru_converter.exporters.hierarchy_corrector import demote_consecutive_headings

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Figure Title"}], "level": 2}},
            {"type": "image", "content": {"image_source": {"path": "fig.png"}}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "Next Section"}], "level": 2}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Some text"}]}},
        ]
    ]

    updated_v2, summary = demote_consecutive_headings(mock_v2)
    assert summary["consecutive_demotions"] == 0
    assert updated_v2[0][0]["content"]["level"] == 2
    assert updated_v2[0][2]["content"]["level"] == 2


def test_demote_consecutive_headings_subtree_cascade():
    """Test that demoting a parent heading also shifts down its child headings until a same-or-higher header is reached."""
    from mineru_converter.exporters.hierarchy_corrector import demote_consecutive_headings

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "## hh"}], "level": 2}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "## gg"}], "level": 2}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Body in gg"}]}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "### ddd"}], "level": 3}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Body in ddd"}]}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "#### eee"}], "level": 4}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Body in eee"}]}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "## next_major_section"}], "level": 2}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Body in next section"}]}},
        ]
    ]

    updated_v2, summary = demote_consecutive_headings(mock_v2)

    # ## hh stays H2
    assert updated_v2[0][0]["content"]["level"] == 2
    # ## gg was consecutive with hh -> demoted to H3
    assert updated_v2[0][1]["content"]["level"] == 3
    # ### ddd was child of gg -> shifted down to H4
    assert updated_v2[0][3]["content"]["level"] == 4
    # #### eee was child of ddd -> shifted down to H5
    assert updated_v2[0][5]["content"]["level"] == 5
    # ## next_major_section is H2 (<= original gg level 2) -> untouched, stays H2
    assert updated_v2[0][7]["content"]["level"] == 2


def test_demote_consecutive_headings_higher_level_demoted():
    """Test Option B: when h1 is H3 and next consecutive heading h2 is H2 with no body, h2 is demoted to H4."""
    from mineru_converter.exporters.hierarchy_corrector import demote_consecutive_headings

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "### h1"}], "level": 3}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "## h2"}], "level": 2}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Body in h2"}]}},
        ]
    ]

    updated_v2, summary = demote_consecutive_headings(mock_v2)

    # h1 stays H3
    assert updated_v2[0][0]["content"]["level"] == 3
    # h2 is demoted to min(6, 3 + 1) = H4
    assert updated_v2[0][1]["content"]["level"] == 4
    assert summary["consecutive_demotions"] == 1


def test_demote_consecutive_headings_nested_multi_level():
    """Test 3 consecutive headings followed by child headings."""
    from mineru_converter.exporters.hierarchy_corrector import demote_consecutive_headings

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "## A"}], "level": 2}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "## B"}], "level": 2}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "## C"}], "level": 2}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Body in C"}]}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "### D"}], "level": 3}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Body in D"}]}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "## NextSection"}], "level": 2}},
        ]
    ]

    updated_v2, summary = demote_consecutive_headings(mock_v2)

    # A stays H2
    assert updated_v2[0][0]["content"]["level"] == 2
    # B becomes H3
    assert updated_v2[0][1]["content"]["level"] == 3
    # C becomes H4
    assert updated_v2[0][2]["content"]["level"] == 4
    # D was H3 child under C (which was demoted from 2 -> 4, delta=+2) -> D should be H5
    assert updated_v2[0][4]["content"]["level"] == 5
    # NextSection stays H2
    assert updated_v2[0][6]["content"]["level"] == 2


def test_demote_nested_consecutive_headings_with_grandchild():
    """
    Test nested consecutive headings inside an already-demoted subtree:
    ## Section A (H2)
    ## Section B (H2 -> demoted to H3, delta=+1)
    Paragraph
    ### Sub C (H3 -> shifted to H4)
    ### Sub D (H3 -> shifted to H4, then consecutive with Sub C -> demoted to H5, delta=+1)
    Paragraph
    #### Grandchild E (H4 -> shifted to H5 with Section B, then shifted to H6 with Sub D!)
    Paragraph
    ## Section Next (H2 -> stops all subtrees, stays H2)
    """
    from mineru_converter.exporters.hierarchy_corrector import demote_consecutive_headings

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "## Section A"}], "level": 2}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "## Section B"}], "level": 2}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Body in Section B"}]}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "### Sub C"}], "level": 3}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "### Sub D"}], "level": 3}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Body in Sub D"}]}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "#### Grandchild E"}], "level": 4}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Body in Grandchild E"}]}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "## Section Next"}], "level": 2}},
        ]
    ]

    updated_v2, summary = demote_consecutive_headings(mock_v2)

    # Section A stays H2
    assert updated_v2[0][0]["content"]["level"] == 2
    # Section B demoted to H3
    assert updated_v2[0][1]["content"]["level"] == 3
    # Sub C was shifted to H4 (as child of Section B)
    assert updated_v2[0][3]["content"]["level"] == 4
    # Sub D is consecutive with Sub C (both H4) -> demoted to H5!
    assert updated_v2[0][4]["content"]["level"] == 5
    # Grandchild E was shifted by Section B (+1 -> H5), then Sub D demoted (+1 -> H6)
    assert updated_v2[0][6]["content"]["level"] == 6
    # Section Next untouched, stays H2
    assert updated_v2[0][8]["content"]["level"] == 2




def test_correct_title_hierarchy_direct_openai_success(monkeypatch):
    """Test LLM-based heading hierarchy correction with direct OpenAI client."""
    import json
    from unittest.mock import MagicMock
    from mineru_converter.exporters.hierarchy_corrector import correct_title_hierarchy_llm

    mock_v2 = [
        [
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "OpenAI Document"}], "level": 1}},
            {"type": "paragraph", "content": {"paragraph_content": [{"type": "text", "content": "Body."}]}},
            {"type": "title", "content": {"title_content": [{"type": "text", "content": "1. Section One"}], "level": 1}},
        ]
    ]

    mock_llm_response = {
        "headings": [
            {"id": 0, "level": 1},
            {"id": 1, "level": 2},
        ]
    }

    mock_chat_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(mock_llm_response)
    mock_chat_completion.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_chat_completion

    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda *args, **kwargs: mock_client)

    corrected_v2, summary = correct_title_hierarchy_llm(
        content_list_v2=mock_v2,
        openai_api_key="sk-fake-openai-key",
        openai_model="gpt-4o",
    )

    assert summary["method"] == "openai_semantic"
    assert summary["provider"] == "openai"
    assert summary["model"] == "gpt-4o"
    assert summary["total_titles"] == 2
    assert summary["corrected_count"] == 1
    assert corrected_v2[0][0]["content"]["level"] == 1
    assert corrected_v2[0][2]["content"]["level"] == 2




