"""Integration tests for FastAPI conversion server."""

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from mineru_converter.core.database import JobDatabase
from mineru_converter.core.queue_worker import QueueWorker
from mineru_converter.server.app import app, get_worker


class TestServerAPI(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.input_dir = self.base_dir / "input"
        self.output_dir = self.base_dir / "output"
        self.db_path = self.output_dir / "test_api_jobs.db"

        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.worker = QueueWorker(
            input_dir=self.input_dir,
            output_dir=self.output_dir,
            db_path=self.db_path,
            poll_interval=0.1,
        )

        # Patch get_worker to use test worker instance
        self.worker_patcher = patch("mineru_converter.server.app.get_worker", return_value=self.worker)
        self.worker_patcher.start()

        # Patch INPUT_DIR and OUTPUT_DIR in server
        self.input_patcher = patch("mineru_converter.server.app.INPUT_DIR", self.input_dir)
        self.output_patcher = patch("mineru_converter.server.app.OUTPUT_DIR", self.output_dir)
        self.input_patcher.start()
        self.output_patcher.start()

        self.client = TestClient(app)

    def tearDown(self):
        if self.worker.is_running:
            self.worker.stop(timeout=1.0)
        self.input_patcher.stop()
        self.output_patcher.stop()
        self.worker_patcher.stop()
        self.temp_dir.cleanup()

    def test_render_web_ui(self):
        """Verify Web Dashboard HTML is served with 200 OK."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("MinerU Conversion Server", response.text)
        self.assertIn("drop-zone", response.text)
        self.assertIn("jobs-table-body", response.text)

    def test_health_check(self):
        """Verify /api/health endpoint returns system metadata."""
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("cuda_available", data)
        self.assertIn("torch_version", data)

    def test_upload_rejection_for_non_pdf(self):
        """Verify non-PDF uploads are rejected with 400 Bad Request."""
        file_content = b"Not a PDF"
        files = [("files", ("test.txt", io.BytesIO(file_content), "text/plain"))]
        response = self.client.post("/api/upload", files=files)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only PDF documents are allowed", response.json()["detail"])

    def test_upload_valid_pdf_and_query_jobs(self):
        """Verify uploading a valid PDF enqueues job and makes it queryable via API."""
        pdf_bytes = b"%PDF-1.4 sample content"
        files = [("files", ("sample_report.pdf", io.BytesIO(pdf_bytes), "application/pdf"))]

        response = self.client.post("/api/upload", files=files)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        job = data[0]
        self.assertEqual(job["file_name"], "sample_report.pdf")
        self.assertEqual(job["status"], "PENDING")
        self.assertEqual(job["current_step"], "QUEUED")

        job_id = job["id"]

        # Check job in /api/jobs
        list_resp = self.client.get("/api/jobs")
        self.assertEqual(list_resp.status_code, 200)
        jobs_list = list_resp.json()
        self.assertTrue(any(j["id"] == job_id for j in jobs_list))

        # Check single job details
        detail_resp = self.client.get(f"/api/jobs/{job_id}")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.json()["id"], job_id)

        # Check stats
        stats_resp = self.client.get("/api/stats")
        self.assertEqual(stats_resp.status_code, 200)
        stats = stats_resp.json()
        self.assertEqual(stats["pending"], 1)

        # Delete job
        del_resp = self.client.delete(f"/api/jobs/{job_id}")
        self.assertEqual(del_resp.status_code, 200)
        self.assertTrue(del_resp.json()["success"])

        # Check job is removed
        not_found_resp = self.client.get(f"/api/jobs/{job_id}")
        self.assertEqual(not_found_resp.status_code, 404)

    def test_get_logs(self):
        """Verify /api/logs retrieves lines from the server log file."""
        log_resp = self.client.get("/api/logs?tail=50")
        self.assertEqual(log_resp.status_code, 200)
        data = log_resp.json()
        self.assertIn("log_file", data)
        self.assertIn("lines", data)
        self.assertIsInstance(data["lines"], list)

    def test_detached_helpers(self):
        """Verify server.py helper methods is_process_running and PID operations."""
        import server
        # Current process should be running
        self.assertTrue(server.is_process_running(server.os.getpid()))
        # Non-existent or invalid PID should return False
        self.assertFalse(server.is_process_running(999999))
        self.assertFalse(server.is_process_running(-1))


if __name__ == "__main__":
    unittest.main()

