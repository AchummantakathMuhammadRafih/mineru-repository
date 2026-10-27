"""Unit tests for Microsoft Presidio PII redaction pipeline."""

import pytest
from pathlib import Path
from mineru_converter.exporters.redactor import (
    redact_markdown_text,
    redact_markdown_file,
    DEFAULT_PII_ENTITIES,
)


def test_redact_markdown_text_pii_masked_with_stars():
    sample_text = (
        "# Patient Record\n\n"
        "Contact: John Doe\n"
        "Email: john.doe@example.com\n"
        "Phone: 555-123-4567\n"
    )

    redacted_text, summary = redact_markdown_text(sample_text)

    # Markdown heading preserved
    assert "# Patient Record" in redacted_text

    # John Doe should be masked with stars
    assert "John Doe" not in redacted_text
    assert "********" in redacted_text

    # Email should be masked with stars
    assert "john.doe@example.com" not in redacted_text

    # Phone should be masked with stars
    assert "555-123-4567" not in redacted_text

    # Summary checks
    assert summary["total_redactions"] >= 2
    assert "PERSON" in summary["entities_detected"] or "EMAIL_ADDRESS" in summary["entities_detected"]


def test_redact_markdown_text_preserves_length():
    sample_text = "Patient Alice Smith resides in New York and can be reached at 555-987-6543."
    redacted_text, summary = redact_markdown_text(sample_text)

    # Exact length preservation when masked 1:1 with stars
    assert len(redacted_text) == len(sample_text)
    assert "Alice Smith" not in redacted_text
    assert "*" in redacted_text


def test_redact_markdown_text_empty_and_whitespace():
    empty_text, summary = redact_markdown_text("")
    assert empty_text == ""
    assert summary["total_redactions"] == 0

    ws_text, ws_summary = redact_markdown_text("   \n\n  ")
    assert ws_text == "   \n\n  "
    assert ws_summary["total_redactions"] == 0


def test_redact_markdown_file_creates_output(tmp_path):
    input_file = tmp_path / "sample_clean.md"
    input_file.write_text(
        "# Emergo Report\n\nLead Investigator: Jane Doe\nEmail: jane.doe@emergo.org\n",
        encoding="utf-8"
    )

    out_file, summary = redact_markdown_file(input_file)

    assert out_file.exists()
    assert out_file.name == "sample_redacted.md"

    content = out_file.read_text(encoding="utf-8")
    assert "# Emergo Report" in content
    assert "Jane Doe" not in content
    assert "jane.doe@emergo.org" not in content
    assert summary["total_redactions"] >= 1
