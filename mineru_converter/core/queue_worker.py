"""Asynchronous sequential background queue worker for MinerU document processing."""

import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mineru_converter.config.constants import DEFAULT_OUTPUT_DIR, SUPPORTED_DOC_EXTENSIONS, SUPPORTED_PDF_EXTENSIONS
from mineru_converter.config.settings import ConversionConfig
from mineru_converter.core.converter import MinerUConverter
from mineru_converter.core.database import JobDatabase, JobStatus, JobStep
from mineru_converter.utils.file_utils import compute_file_sha256
from mineru_converter.utils.logging_utils import get_logger

logger = get_logger()


class QueueWorker:
    """
    Sequential background worker that continuously picks up jobs from a FIFO queue,
    executes MinerU parsing, dominant font-size hierarchy correction, Presidio PII redaction,
    and updates SQLite database state in real-time.
    """

    def __init__(
        self,
        input_dir: Path,
        output_dir: Path = DEFAULT_OUTPUT_DIR,
        db_path: Optional[Path] = None,
        poll_interval: float = 1.0,
        config: Optional[ConversionConfig] = None,
        on_job_update: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.input_dir = Path(input_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.poll_interval = poll_interval
        self.on_job_update = on_job_update

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

        self._queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._current_job: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def current_job(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._current_job) if self._current_job else None

    def start(self) -> None:
        """Start background worker thread."""
        with self._lock:
            if self.is_running:
                logger.warning("QueueWorker is already running.")
                return

            # Recover any stale processing jobs from previous unexpected shutdown
            reset_count = self.db.reset_stale_processing_jobs()
            if reset_count > 0:
                logger.info(f"Reset {reset_count} interrupted job(s) from PROCESSING back to PENDING.")

            # Scan input directory for any pre-existing PDF files not yet in database
            self.scan_and_enqueue_input_dir()

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="MinerU-QueueWorker",
                daemon=True,
            )
            self._thread.start()
            logger.info("⚡ Background QueueWorker thread started (FIFO sequential mode).")

    def stop(self, timeout: float = 10.0) -> None:
        """Signal background worker to stop and wait for loop termination."""
        self._stop_event.set()
        # Wake up the queue get
        self._queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            logger.info("Background QueueWorker thread stopped.")

    def scan_and_enqueue_input_dir(self) -> int:
        """Scan input directory for any PDF files and ensure they are registered in the DB."""
        if not self.input_dir.exists():
            return 0

        registered_count = 0
        candidates = []
        for entry in self.input_dir.iterdir():
            if entry.is_file() and entry.suffix.lower() in SUPPORTED_DOC_EXTENSIONS:
                if not entry.name.startswith((".", "~$")):
                    candidates.append(entry)

        candidates.sort(key=lambda p: p.stat().st_mtime)
        for cand in candidates:
            existing = self.db.get_job_by_filepath(cand)
            if not existing:
                try:
                    size = cand.stat().st_size
                    sha256 = compute_file_sha256(cand)
                    job = self.db.register_or_get_job(cand, file_hash=sha256, file_size=size)
                    self._queue.put(job["id"])
                    registered_count += 1
                except Exception as exc:
                    logger.warning(f"Could not register file {cand.name}: {exc}")
            elif existing["status"] == JobStatus.PENDING:
                self._queue.put(existing["id"])

        return registered_count

    def submit_file(self, file_path: Path, parser_engine: str = "mineru") -> Dict[str, Any]:
        """
        Submit a newly uploaded file to the processing queue.
        Registers the job in SQLite and enqueues it for the worker thread.
        """
        p = Path(file_path).resolve()
        file_size = p.stat().st_size if p.exists() else 0
        try:
            sha256 = compute_file_sha256(p) if p.exists() else None
        except Exception:
            sha256 = None

        job = self.db.register_or_get_job(
            p,
            file_hash=sha256,
            file_size=file_size,
            parser_engine=parser_engine or "mineru",
        )
        self._queue.put(job["id"])
        logger.info(f"📥 Enqueued job #{job['id']} (engine={job.get('parser_engine', 'mineru')}) for file: {p.name}")
        return job

    def _worker_loop(self) -> None:
        """Main loop executed by the background thread."""
        logger.info("QueueWorker loop entering active polling cycle.")
        while not self._stop_event.is_set():
            try:
                # 1. Fetch next pending job from database (FIFO order)
                job = self.db.get_next_pending_job()

                if not job:
                    # Scan filesystem to see if any file was directly copied into input_dir
                    self.scan_and_enqueue_input_dir()
                    job = self.db.get_next_pending_job()

                if not job:
                    # Wait on in-memory queue notification or timeout
                    try:
                        self._queue.get(timeout=self.poll_interval)
                    except queue.Empty:
                        pass
                    continue

                # 2. Process this single job
                self._execute_job(job)

            except Exception as exc:
                logger.error(f"Unexpected error in QueueWorker loop: {exc}", exc_info=True)
                time.sleep(self.poll_interval)

        logger.info("QueueWorker loop exited.")

    def _execute_job(self, job: Dict[str, Any]) -> bool:
        """Process a single document job through parse -> hierarchy correction -> redaction."""
        job_id = job["id"]
        file_path = Path(job["file_path"])

        if not file_path.exists():
            err_msg = f"Target file not found on disk: {file_path}"
            logger.error(f"✗ [Job #{job_id}] {err_msg}")
            self.db.mark_failed(job_id, error_message=err_msg)
            return False

        with self._lock:
            self._current_job = dict(job)
            self._current_job["status"] = JobStatus.PROCESSING
            self._current_job["current_step"] = JobStep.PARSING

        self.db.mark_processing(job_id, step=JobStep.PARSING)
        logger.info(f"▶ [QueueWorker] Started processing Job #{job_id}: {file_path.name}")
        start_time = time.time()

        def _step_callback(step_name: str) -> None:
            """Callback from converter as each pipeline stage begins."""
            self.db.update_step(job_id, step_name)
            with self._lock:
                if self._current_job and self._current_job.get("id") == job_id:
                    self._current_job["current_step"] = step_name
            if self.on_job_update:
                updated = self.db.get_job(job_id)
                if updated:
                    self.on_job_update(updated)
            logger.info(f"🔄 [Job #{job_id}] Progress step: {step_name}")

        try:
            # Check if this specific job requests a particular parser engine
            job_engine_str = job.get("parser_engine")
            if job_engine_str:
                try:
                    from mineru_converter.models.enums import ParserEngine
                    target_engine = ParserEngine(job_engine_str.lower())
                    self.converter.config.parser_engine = target_engine
                except Exception:
                    pass

            # Execute conversion (Pipeline: Parse -> Hierarchy Correction -> Presidio Redaction)
            result = self.converter.convert(file_path, step_callback=_step_callback)
            duration = time.time() - start_time

            # Verify job was not deleted during processing
            current_db_job = self.db.get_job(job_id)
            if not current_db_job:
                logger.info(f"Job #{job_id} was deleted during execution; discarding result.")
                return False

            if result.success:
                out_dir = result.manifest.output_directory
                md_path = result.manifest.markdown_path
                self.db.mark_completed(
                    job_id=job_id,
                    output_dir=out_dir,
                    markdown_file=md_path,
                    duration_seconds=duration,
                )
                logger.info(
                    f"✓ [Job #{job_id}] Finished successfully in {duration:.2f}s -> {out_dir}"
                )
                if self.on_job_update:
                    final_job = self.db.get_job(job_id)
                    if final_job:
                        self.on_job_update(final_job)
                return True
            else:
                err = result.error_message or "Unknown conversion error"
                self.db.mark_failed(job_id, error_message=err, duration_seconds=duration)
                logger.error(f"✗ [Job #{job_id}] Failed: {err}")
                if self.on_job_update:
                    final_job = self.db.get_job(job_id)
                    if final_job:
                        self.on_job_update(final_job)
                return False

        except Exception as exc:
            duration = time.time() - start_time
            logger.exception(f"✗ [Job #{job_id}] Unhandled error: {exc}")
            if self.db.get_job(job_id):
                self.db.mark_failed(job_id, error_message=str(exc), duration_seconds=duration)
            if self.on_job_update:
                final_job = self.db.get_job(job_id)
                if final_job:
                    self.on_job_update(final_job)
            return False

        finally:
            with self._lock:
                self._current_job = None

    def process_one_job_sync(self) -> bool:
        """Process up to one pending job synchronously (convenient for testing)."""
        job = self.db.get_next_pending_job()
        if not job:
            self.scan_and_enqueue_input_dir()
            job = self.db.get_next_pending_job()
        if job:
            return self._execute_job(job)
        return False
