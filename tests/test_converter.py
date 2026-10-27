"""Unit and integration tests for MinerU PDF Converter."""

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from click.testing import CliRunner

from mineru_converter.config.constants import BackendType, ParseMethod
from mineru_converter.config.settings import ConversionConfig
from mineru_converter.core.validator import validate_pdf_document, PDFValidationError
from mineru_converter.exporters.manifest import (
    build_output_manifest,
    calculate_document_stats,
)
from mineru_converter.exporters.postprocessor import (
    normalize_markdown_content,
    normalize_markdown_headers,
)
from mineru_converter.models.schemas import OutputFileManifest
from mineru_converter.utils.file_utils import (
    compute_file_sha256,
    find_input_files,
    format_file_size,
    sanitize_filename,
)
from mineru_converter.cli.app import cli
from mineru_converter.utils.logging_utils import get_logger


class TestMinerUConverter(unittest.TestCase):

    def test_conversion_config_defaults(self):
        config = ConversionConfig()
        self.assertEqual(config.backend, BackendType.PIPELINE)
        self.assertEqual(config.method, ParseMethod.AUTO)
        self.assertEqual(config.lang, "ch")
        self.assertTrue(config.formula_enable)
        self.assertTrue(config.table_enable)
        self.assertTrue(config.dump_md)
        self.assertTrue(config.dump_content_list)
        self.assertTrue(config.dump_middle_json)
        self.assertTrue(config.draw_layout_bbox)
        self.assertTrue(config.draw_span_bbox)

    def test_language_normalization(self):
        cfg1 = ConversionConfig(lang="en")
        self.assertEqual(cfg1.lang, "ch")
        cfg2 = ConversionConfig(lang="english")
        self.assertEqual(cfg2.lang, "ch")
        cfg3 = ConversionConfig(lang="korean")
        self.assertEqual(cfg3.lang, "korean")

    def test_file_utilities(self):
        self.assertEqual(format_file_size(500), "500 B")
        self.assertEqual(format_file_size(1024), "1.00 KB")
        self.assertEqual(format_file_size(1024 * 1024 * 3), "3.00 MB")
        self.assertEqual(sanitize_filename('sample:file/name*?'), "sample_file_name")

    def test_pdf_validation_real_pdf(self):
        pdf_path = Path("input/UsabilityTestingOfMedicalDevices_2014-Ch12.pdf")
        if pdf_path.exists():
            total, start, end = validate_pdf_document(pdf_path, start_page=0, end_page=2)
            self.assertEqual(total, 40)
            self.assertEqual(start, 0)
            self.assertEqual(end, 2)

    def test_pdf_validation_missing_file(self):
        with self.assertRaises(PDFValidationError):
            validate_pdf_document(Path("non_existent_file.pdf"))

    def test_pdf_validation_invalid_bounds(self):
        pdf_path = Path("input/UsabilityTestingOfMedicalDevices_2014-Ch12.pdf")
        if pdf_path.exists():
            with self.assertRaises(PDFValidationError):
                validate_pdf_document(pdf_path, start_page=100)
            with self.assertRaises(PDFValidationError):
                validate_pdf_document(pdf_path, start_page=5, end_page=2)

    def test_manifest_builder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            doc_stem = "sample_doc"
            
            # Create dummy artifacts
            (tmp_path / f"{doc_stem}.md").write_text("# Title\nSample text", encoding="utf-8")
            (tmp_path / f"{doc_stem}_content_list.json").write_text("[]", encoding="utf-8")
            (tmp_path / f"{doc_stem}_content_list_v2.json").write_text("[]", encoding="utf-8")
            (tmp_path / f"{doc_stem}_middle.json").write_text('{"pdf_info": []}', encoding="utf-8")
            (tmp_path / f"{doc_stem}_model.json").write_text("{}", encoding="utf-8")
            
            manifest = build_output_manifest(tmp_path, doc_stem)
            self.assertEqual(manifest.markdown_path, tmp_path / f"{doc_stem}.md")
            self.assertEqual(manifest.content_list_v1_path, tmp_path / f"{doc_stem}_content_list.json")
            self.assertEqual(manifest.content_list_v2_path, tmp_path / f"{doc_stem}_content_list_v2.json")
            self.assertEqual(manifest.middle_json_path, tmp_path / f"{doc_stem}_middle.json")
            self.assertEqual(manifest.model_json_path, tmp_path / f"{doc_stem}_model.json")

    def test_cli_info_command(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["info"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("MinerU", result.output)

    def test_cli_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("convert", result.output)
        self.assertIn("batch", result.output)
        self.assertIn("inspect", result.output)
        self.assertIn("info", result.output)

    def test_cli_convert_help_has_capture_header(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["convert", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--capture-header", result.output)

    def test_normalize_markdown_content_h2_to_h1(self):
        input_md = "## Chapter 1: Introduction\n\nSome text\n\n## Section 1.1\nMore text"
        output_md, modified, msg = normalize_markdown_content(input_md)
        self.assertTrue(modified)
        self.assertTrue(output_md.startswith("# Chapter 1: Introduction"))
        self.assertIn("## Section 1.1", output_md)

    def test_normalize_markdown_content_leaves_top_h1(self):
        input_md = "# Chapter 1: Introduction\n\nSome text\n\n## Section 1.1\nMore text"
        output_md, modified, msg = normalize_markdown_content(input_md)
        self.assertFalse(modified)
        self.assertEqual(output_md, input_md)

    def test_normalize_markdown_content_demotes_conflicting_subsequent_h1(self):
        input_md = "## Chapter 1: Introduction\n\n# Conflicting Large Header\n\n## Normal Section"
        output_md, modified, msg = normalize_markdown_content(input_md)
        self.assertTrue(modified)
        self.assertTrue(output_md.startswith("# Chapter 1: Introduction"))
        self.assertIn("## Conflicting Large Header", output_md)
        self.assertIn("## Normal Section", output_md)

    def test_normalize_markdown_headers_in_place(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test_doc.md"
            file_path.write_text("## 12 Conducting the Test\n\n## Section 1\n", encoding="utf-8")
            result = normalize_markdown_headers(file_path)
            self.assertTrue(result)
            updated = file_path.read_text(encoding="utf-8")
            self.assertTrue(updated.startswith("# 12 Conducting the Test"))
            self.assertIn("## Section 1", updated)


class TestBackgroundWatcherAndDatabase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.input_dir = self.base / "input"
        self.output_dir = self.base / "output"
        self.input_dir.mkdir()
        self.output_dir.mkdir()
        self.db_path = self.base / "test_jobs.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_database_lifecycle(self):
        from mineru_converter.core.database import JobDatabase, JobStatus

        db = JobDatabase(self.db_path)
        dummy_file = self.input_dir / "sample.pdf"
        dummy_file.write_bytes(b"%PDF-1.4 test")

        job = db.register_or_get_job(dummy_file, file_hash="hash123", file_size=13)
        self.assertEqual(job["status"], JobStatus.PENDING)
        self.assertEqual(job["file_name"], "sample.pdf")

        db.mark_processing(job["id"])
        job_proc = db.get_job_by_filepath(dummy_file)
        self.assertEqual(job_proc["status"], JobStatus.PROCESSING)

        db.mark_completed(
            job["id"],
            output_dir=self.output_dir / "sample",
            markdown_file=self.output_dir / "sample" / "sample.md",
            duration_seconds=1.25,
        )
        job_done = db.get_job_by_filepath(dummy_file)
        self.assertEqual(job_done["status"], JobStatus.COMPLETED)
        self.assertEqual(job_done["duration_seconds"], 1.25)

        stats = db.get_stats()
        self.assertEqual(stats[JobStatus.COMPLETED], 1)
        self.assertEqual(stats[JobStatus.PENDING], 0)

    def test_watcher_process_one(self):
        from mineru_converter.core.watcher import BackgroundWatcher
        from mineru_converter.models.schemas import ConversionResult, OutputFileManifest

        dummy_pdf = self.input_dir / "doc1.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 content")

        watcher = BackgroundWatcher(
            input_dir=self.input_dir,
            output_dir=self.output_dir,
            db_path=self.db_path,
        )

        mock_manifest = OutputFileManifest(
            document_name="doc1",
            output_directory=self.output_dir / "doc1",
            markdown_path=self.output_dir / "doc1" / "doc1.md",
        )
        mock_result = ConversionResult(
            success=True,
            input_path=dummy_pdf,
            document_name="doc1",
            manifest=mock_manifest,
        )

        with patch.object(watcher.converter, "convert", return_value=mock_result):
            processed = watcher.process_one(dummy_pdf)
            self.assertTrue(processed)

        job = watcher.db.get_job_by_filepath(dummy_pdf)
        self.assertEqual(job["status"], "COMPLETED")
        self.assertIn("doc1", job["output_dir"])

    def test_create_job_zip_bundles_original_and_outputs(self):
        import io
        import zipfile
        from mineru_converter.utils.zip_utils import create_job_zip

        orig = self.input_dir / "my_doc.pdf"
        orig.write_bytes(b"dummy pdf bytes")

        out_sub = self.output_dir / "my_doc"
        out_sub.mkdir(parents=True)
        (out_sub / "my_doc.md").write_text("# Title", encoding="utf-8")
        img_dir = out_sub / "images"
        img_dir.mkdir()
        (img_dir / "img1.png").write_bytes(b"fake image")

        zip_data = create_job_zip(output_dir=out_sub, original_file_path=orig)
        self.assertIsInstance(zip_data, bytes)
        self.assertGreater(len(zip_data), 0)

        # Inspect zip contents
        with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zf:
            namelist = zf.namelist()
            self.assertIn("original/my_doc.pdf", namelist)
            self.assertIn("outputs/my_doc.md", namelist)
            self.assertIn("outputs/images/img1.png", namelist)


if __name__ == "__main__":
    unittest.main()

