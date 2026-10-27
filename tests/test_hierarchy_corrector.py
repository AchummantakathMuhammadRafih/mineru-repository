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


