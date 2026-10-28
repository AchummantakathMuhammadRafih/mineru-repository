"""Unit tests for Marker parser integration and Markdown hierarchy correction."""

from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

from mineru_converter.models.enums import ParserEngine
from mineru_converter.config.settings import ConversionConfig
from mineru_converter.parsers.marker_parser import is_marker_available, convert_pdf_with_marker
from mineru_converter.exporters.markdown_hierarchy import correct_markdown_heading_hierarchy
from mineru_converter.core.database import JobDatabase, JobStatus


def test_markdown_hierarchy_correction_gap_compression():
    """Test that Markdown level skips (H1 -> H4 -> H6) are compressed into (H1 -> H2 -> H3)."""
    raw_md = """# Document Title

Some intro text.

#### Section A Skipping to H4

More text.

###### Sub-subsection skipping to H6

Detail text.

#### Section B Siblings

End text.
"""
    clean_md, summary = correct_markdown_heading_hierarchy(raw_md)
    assert summary["headings_count"] == 4
    assert summary["adjusted_count"] >= 2
    assert "# Document Title" in clean_md
    assert "## Section A Skipping to H4" in clean_md
    assert "### Sub-subsection skipping to H6" in clean_md
    assert "## Section B Siblings" in clean_md


def test_database_parser_engine_registration(tmp_path):
    """Test that JobDatabase stores and retrieves parser_engine correctly."""
    db_path = tmp_path / "test_jobs.db"
    db = JobDatabase(db_path)

    fake_pdf = tmp_path / "sample.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 dummy")

    job1 = db.register_or_get_job(fake_pdf, parser_engine="marker")
    assert job1["parser_engine"] == "marker"

    fetched = db.get_job(job1["id"])
    assert fetched["parser_engine"] == "marker"


def test_marker_parser_import_error_handling(tmp_path):
    """Test that convert_pdf_with_marker raises helpful ImportError when marker is not installed."""
    fake_pdf = tmp_path / "sample.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 dummy")
    out_dir = tmp_path / "out"

    with patch("shutil.which", return_value=None):
        with pytest.raises(ImportError, match="marker-pdf is not installed"):
            convert_pdf_with_marker(fake_pdf, out_dir)


def test_converter_marker_flow(tmp_path):
    """Test MinerUConverter processes Marker output through hierarchy and redaction."""
    from mineru_converter.core.converter import MinerUConverter
    from mineru_converter.models.schemas import OutputFileManifest

    fake_pdf = tmp_path / "test_doc.pdf"
    # Valid minimal 1-page PDF
    import fitz
    doc = fitz.open()
    doc.new_page()
    doc.save(str(fake_pdf))
    doc.close()

    cfg = ConversionConfig(
        output_dir=tmp_path / "output",
        parser_engine=ParserEngine.MARKER,
        redact_pii=False,
    )
    converter = MinerUConverter(config=cfg)

    def fake_convert(pdf_path, output_dir, **kwargs):
        md = output_dir / f"{pdf_path.stem}.md"
        md.write_text("# Title\n\n### Subtitle\n\nContent", encoding="utf-8")
        return md, None

    with patch("mineru_converter.core.converter.convert_pdf_with_marker", side_effect=fake_convert):
        res = converter.convert(fake_pdf)
        assert res.success is True
        assert res.manifest.markdown_path.exists()
        assert res.manifest.clean_markdown_path.exists()
        clean_text = res.manifest.clean_markdown_path.read_text(encoding="utf-8")
        # H3 Subtitle was compressed to H2 child
        assert "## Subtitle" in clean_text
