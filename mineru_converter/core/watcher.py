"""Background folder watcher and serial conversion queue runner."""

import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from mineru_converter.config.constants import DEFAULT_OUTPUT_DIR, SUPPORTED_PDF_EXTENSIONS
from mineru_converter.config.settings import ConversionConfig
from mineru_converter.core.converter import MinerUConverter
from mineru_converter.core.database import JobDatabase, JobStatus
from mineru_converter.utils.file_utils import compute_file_sha256
from mineru_converter.utils.logging_utils import get_logger

logger = get_logger()


class BackgroundWatcher:
    """
    Listens to an input directory, processes documents one at a time,
    writes outputs to the output directory, and persists state in SQLite.
    """

    def __init__(
        self,
        input_dir: Path,
        output_dir: Path = DEFAULT_OUTPUT_DIR,
        db_path: Optional[Path] = None,
        poll_interval: float = 3.0,
        config: Optional[ConversionConfig] = None,
        on_job_update: Optional[Callable[[dict], None]] = None,
    ):
        self.input_dir = Path(input_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.poll_interval = poll_interval
        self.on_job_update = on_job_update
        self.running = False

        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if db_path is None:
            self.db_path = self.output_dir / "mineru_jobs.db"
        else:
            self.db_path = Path(db_path).resolve()

        self.db = JobDatabase(self.db_path)

        self.config = config or ConversionConfig(output_dir=self.output_dir)
        self.config.output_dir = self.output_dir
        self.converter = MinerUConverter(config=self.config)

    def _is_file_ready(self, file_path: Path) -> bool:
        """Check if file has finished writing by sampling its size."""
        try:
            initial_size = file_path.stat().st_size
            time.sleep(0.5)
            final_size = file_path.stat().st_size
            return initial_size == final_size and initial_size > 0
        except OSError:
            return False

    def scan_for_candidates(self):
        """Find supported files in input directory sorted by creation time."""
        if not self.input_dir.exists():
            return []

        candidates = []
        for entry in self.input_dir.iterdir():
            if entry.is_file() and entry.suffix.lower() in SUPPORTED_PDF_EXTENSIONS:
                if not entry.name.startswith((".", "~$")):
                    candidates.append(entry)

        candidates.sort(key=lambda p: p.stat().st_mtime)
        return candidates

    def process_one(self, file_path: Path) -> bool:
        """Process a single document file."""
        if not self._is_file_ready(file_path):
            logger.debug(f"File {file_path.name} is still being written or is empty; skipping for now.")
            return False

        try:
            sha256 = compute_file_sha256(file_path)
            file_size = file_path.stat().st_size
        except Exception as e:
            logger.warning(f"Failed to read file info for {file_path.name}: {e}")
            return False

        # Register in database or check existing
        job = self.db.register_or_get_job(file_path, file_hash=sha256, file_size=file_size)

        # Skip if already completed
        if job["status"] == JobStatus.COMPLETED:
            return False

        # Mark as processing
        self.db.mark_processing(job["id"])
        job["status"] = JobStatus.PROCESSING
        if self.on_job_update:
            self.on_job_update(job)

        logger.info(f"▶ [Queue] Processing 1 document: {file_path.name} (Job #{job['id']})")
        start_time = time.time()

        try:
            result = self.converter.convert(file_path)
            duration = time.time() - start_time

            # Before persisting results, verify job was not deleted by user during conversion
            current_job = self.db.get_job(job["id"])
            if not current_job:
                logger.info(f"Job #{job['id']} was deleted during processing; discarding outputs.")
                return False

            if result.success:
                md_path = result.manifest.markdown_path
                self.db.mark_completed(
                    job_id=job["id"],
                    output_dir=result.manifest.output_directory,
                    markdown_file=md_path,
                    duration_seconds=duration,
                )
                logger.info(
                    f"✓ [Job #{job['id']}] Completed {file_path.name} in {duration:.2f}s "
                    f"-> {result.manifest.output_directory}"
                )
                job["status"] = JobStatus.COMPLETED
                job["output_dir"] = str(result.manifest.output_directory)
                if self.on_job_update:
                    self.on_job_update(job)
                return True
            else:
                err = result.error_message or "Unknown conversion error"
                self.db.mark_failed(job["id"], error_message=err, duration_seconds=duration)
                logger.error(f"✗ [Job #{job['id']}] Failed {file_path.name}: {err}")
                job["status"] = JobStatus.FAILED
                job["error_message"] = err
                if self.on_job_update:
                    self.on_job_update(job)
                return False

        except Exception as exc:
            duration = time.time() - start_time
            if self.db.get_job(job["id"]):
                self.db.mark_failed(job["id"], error_message=str(exc), duration_seconds=duration)
                logger.exception(f"✗ [Job #{job['id']}] Unexpected exception converting {file_path.name}: {exc}")
                job["status"] = JobStatus.FAILED
                job["error_message"] = str(exc)
                if self.on_job_update:
                    self.on_job_update(job)
            return False

    def run_once(self) -> int:
        """Scan input directory once and process up to one pending file."""
        candidates = self.scan_for_candidates()
        for candidate in candidates:
            existing = self.db.get_job_by_filepath(candidate)
            if not existing or existing["status"] in (JobStatus.PENDING, JobStatus.FAILED):
                processed = self.process_one(candidate)
                if processed:
                    return 1
        return 0

    def start(self):
        """Start listening to input folder indefinitely until stopped."""
        self.running = True
        logger.info(
            f"🚀 Background runner listening on: [bold cyan]{self.input_dir}[/bold cyan]\n"
            f"   Output folder: [bold cyan]{self.output_dir}[/bold cyan]\n"
            f"   SQLite DB: [bold cyan]{self.db_path}[/bold cyan]\n"
            f"   Poll interval: {self.poll_interval}s\n"
            f"   Processing mode: Strictly 1 file at a time (FIFO)"
        )

        def _handle_exit(sig, frame):
            logger.info("Received stop signal. Shutting down watcher gracefully...")
            self.running = False

        signal.signal(signal.SIGINT, _handle_exit)
        signal.signal(signal.SIGTERM, _handle_exit)

        while self.running:
            try:
                candidates = self.scan_for_candidates()
                for file_path in candidates:
                    if not self.running:
                        break
                    existing = self.db.get_job_by_filepath(file_path)
                    # Process if new or pending (or retry failed)
                    if not existing or existing["status"] == JobStatus.PENDING:
                        self.process_one(file_path)
                        # Process strictly one at a time and re-scan
                        break
            except Exception as e:
                logger.error(f"Error during directory poll: {e}")

            time.sleep(self.poll_interval)

        logger.info("Watcher stopped.")

    def stop(self):
        self.running = False
