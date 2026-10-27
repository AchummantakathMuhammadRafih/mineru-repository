"""Unit tests for sequential QueueWorker and state tracking."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mineru_converter.config.settings import ConversionConfig
from mineru_converter.core.database import JobDatabase, JobStatus, JobStep
from mineru_converter.core.queue_worker import QueueWorker
from mineru_converter.models.schemas import ConversionResult, OutputFileManifest


class TestQueueWorker(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.input_dir = self.base_dir / "input"
        self.output_dir = self.base_dir / "output"
        self.db_path = self.output_dir / "test_jobs.db"

        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.config = ConversionConfig(output_dir=self.output_dir, dump_images=False)
        self.worker = QueueWorker(
            input_dir=self.input_dir,
            output_dir=self.output_dir,
            db_path=self.db_path,
            config=self.config,
            poll_interval=0.1,
        )

    def tearDown(self):
        if self.worker.is_running:
            self.worker.stop(timeout=1.0)
        self.temp_dir.cleanup()

    def test_database_step_tracking(self):
        """Test that JobDatabase tracks granular execution steps correctly."""
        dummy_file = self.input_dir / "doc1.pdf"
        dummy_file.write_bytes(b"%PDF-1.4 dummy content")

        job = self.worker.db.register_or_get_job(dummy_file, file_size=100)
        self.assertEqual(job["status"], JobStatus.PENDING)
        self.assertEqual(job["current_step"], JobStep.QUEUED)

        # Mark processing with initial step
        self.worker.db.mark_processing(job["id"], step=JobStep.PARSING)
        updated = self.worker.db.get_job(job["id"])
        self.assertEqual(updated["status"], JobStatus.PROCESSING)
        self.assertEqual(updated["current_step"], JobStep.PARSING)

        # Transition to hierarchy correction
        self.worker.db.update_step(job["id"], JobStep.HIERARCHY_CORRECTION)
        updated = self.worker.db.get_job(job["id"])
        self.assertEqual(updated["current_step"], JobStep.HIERARCHY_CORRECTION)

        # Transition to redaction
        self.worker.db.update_step(job["id"], JobStep.REDACTION)
        updated = self.worker.db.get_job(job["id"])
        self.assertEqual(updated["current_step"], JobStep.REDACTION)

        # Mark completed
        self.worker.db.mark_completed(
            job_id=job["id"],
            output_dir=self.output_dir / "doc1",
            markdown_file=self.output_dir / "doc1" / "doc1.md",
            duration_seconds=2.5,
        )
        completed_job = self.worker.db.get_job(job["id"])
        self.assertEqual(completed_job["status"], JobStatus.COMPLETED)
        self.assertEqual(completed_job["current_step"], JobStep.COMPLETED)

    def test_reset_stale_processing_jobs(self):
        """Verify that jobs interrupted in PROCESSING status are recovered to PENDING."""
        dummy_file = self.input_dir / "stale.pdf"
        dummy_file.write_bytes(b"%PDF-1.4 dummy")

        job = self.worker.db.register_or_get_job(dummy_file, file_size=50)
        self.worker.db.mark_processing(job["id"])

        stale = self.worker.db.get_job(job["id"])
        self.assertEqual(stale["status"], JobStatus.PROCESSING)

        recovered = self.worker.db.reset_stale_processing_jobs()
        self.assertEqual(recovered, 1)

        after = self.worker.db.get_job(job["id"])
        self.assertEqual(after["status"], JobStatus.PENDING)
        self.assertEqual(after["current_step"], JobStep.QUEUED)

    @patch("mineru_converter.core.converter.MinerUConverter.convert")
    def test_worker_process_one_job_sync(self, mock_convert):
        """Verify sequential execution through mock converter calling step callbacks."""
        dummy_file = self.input_dir / "test_doc.pdf"
        dummy_file.write_bytes(b"%PDF-1.4 mock content")

        out_doc_dir = self.output_dir / "test_doc"
        out_doc_dir.mkdir(parents=True, exist_ok=True)
        manifest = OutputFileManifest(
            document_name="test_doc",
            output_directory=out_doc_dir,
            markdown_path=out_doc_dir / "test_doc.md",
        )

        steps_called = []

        def side_effect(input_path, step_callback=None, **kwargs):
            if step_callback:
                step_callback("PARSING")
                steps_called.append("PARSING")
                step_callback("HIERARCHY_CORRECTION")
                steps_called.append("HIERARCHY_CORRECTION")
                step_callback("REDACTION")
                steps_called.append("REDACTION")
                step_callback("COMPLETED")
                steps_called.append("COMPLETED")
            return ConversionResult(
                success=True,
                input_path=Path(input_path),
                document_name="test_doc",
                manifest=manifest,
                duration_seconds=1.2,
            )

        mock_convert.side_effect = side_effect

        job = self.worker.submit_file(dummy_file)
        self.assertEqual(job["status"], JobStatus.PENDING)

        did_process = self.worker.process_one_job_sync()
        self.assertTrue(did_process)

        self.assertEqual(
            steps_called,
            ["PARSING", "HIERARCHY_CORRECTION", "REDACTION", "COMPLETED"],
        )

        completed = self.worker.db.get_job(job["id"])
        self.assertEqual(completed["status"], JobStatus.COMPLETED)
        self.assertEqual(completed["current_step"], JobStep.COMPLETED)


if __name__ == "__main__":
    unittest.main()
