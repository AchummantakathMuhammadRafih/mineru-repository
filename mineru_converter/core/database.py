"""SQLite state tracking repository for background document parsing."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class JobStatus:
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobStep:
    QUEUED = "QUEUED"
    PARSING = "PARSING"
    HIERARCHY_CORRECTION = "HIERARCHY_CORRECTION"
    REDACTION = "REDACTION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


from contextlib import contextmanager

class JobDatabase:
    """Manages SQLite storage for background document processing jobs."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversion_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_name TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    file_hash TEXT,
                    file_size INTEGER,
                    status TEXT NOT NULL,
                    current_step TEXT DEFAULT 'QUEUED',
                    output_dir TEXT,
                    markdown_file TEXT,
                    error_message TEXT,
                    duration_seconds REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP
                )
                """
            )
            # Automatic column migration for existing databases
            try:
                conn.execute(
                    "ALTER TABLE conversion_jobs ADD COLUMN current_step TEXT DEFAULT 'QUEUED'"
                )
            except sqlite3.OperationalError:
                pass

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status ON conversion_jobs(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_filepath ON conversion_jobs(file_path)"
            )
            conn.commit()

    def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        """Get a single job by its integer ID."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM conversion_jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_job_by_filepath(self, file_path: Path) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM conversion_jobs WHERE file_path = ?",
                (str(file_path.resolve()),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_next_pending_job(self) -> Optional[Dict[str, Any]]:
        """Fetch the oldest pending job in FIFO order."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM conversion_jobs
                WHERE status = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (JobStatus.PENDING,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def reset_stale_processing_jobs(self) -> int:
        """Reset any interrupted jobs stuck in PROCESSING back to PENDING on server startup."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE conversion_jobs
                SET status = ?, current_step = ?
                WHERE status = ?
                """,
                (JobStatus.PENDING, JobStep.QUEUED, JobStatus.PROCESSING),
            )
            conn.commit()
            return cur.rowcount

    def register_or_get_job(
        self,
        file_path: Path,
        file_hash: Optional[str] = None,
        file_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        resolved = str(file_path.resolve())
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM conversion_jobs WHERE file_path = ?",
                (resolved,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)

            now_iso = datetime.utcnow().isoformat()
            cur = conn.execute(
                """
                INSERT INTO conversion_jobs (
                    file_name, file_path, file_hash, file_size, status, current_step, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_path.name,
                    resolved,
                    file_hash,
                    file_size or 0,
                    JobStatus.PENDING,
                    JobStep.QUEUED,
                    now_iso,
                ),
            )
            conn.commit()
            last_id = cur.lastrowid
            cur = conn.execute("SELECT * FROM conversion_jobs WHERE id = ?", (last_id,))
            return dict(cur.fetchone())

    def update_step(self, job_id: int, step: str) -> None:
        """Update the granular execution step (PARSING, HIERARCHY_CORRECTION, REDACTION, etc.)."""
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE conversion_jobs
                SET current_step = ?
                WHERE id = ?
                """,
                (step, job_id),
            )
            conn.commit()

    def mark_processing(self, job_id: int, step: str = JobStep.PARSING) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE conversion_jobs
                SET status = ?, current_step = ?, started_at = ?
                WHERE id = ?
                """,
                (JobStatus.PROCESSING, step, datetime.utcnow().isoformat(), job_id),
            )
            conn.commit()

    def mark_completed(
        self,
        job_id: int,
        output_dir: Path,
        markdown_file: Optional[Path],
        duration_seconds: float,
    ) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE conversion_jobs
                SET status = ?,
                    current_step = ?,
                    output_dir = ?,
                    markdown_file = ?,
                    duration_seconds = ?,
                    completed_at = ?,
                    error_message = NULL
                WHERE id = ?
                """,
                (
                    JobStatus.COMPLETED,
                    JobStep.COMPLETED,
                    str(output_dir),
                    str(markdown_file) if markdown_file else None,
                    duration_seconds,
                    datetime.utcnow().isoformat(),
                    job_id,
                ),
            )
            conn.commit()

    def mark_failed(self, job_id: int, error_message: str, duration_seconds: float = 0.0) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE conversion_jobs
                SET status = ?,
                    current_step = ?,
                    error_message = ?,
                    duration_seconds = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    JobStatus.FAILED,
                    JobStep.FAILED,
                    error_message,
                    duration_seconds,
                    datetime.utcnow().isoformat(),
                    job_id,
                ),
            )
            conn.commit()

    def list_jobs(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            if status:
                cur = conn.execute(
                    """
                    SELECT * FROM conversion_jobs
                    WHERE status = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (status, limit),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT * FROM conversion_jobs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            return [dict(row) for row in cur.fetchall()]

    def get_stats(self) -> Dict[str, int]:
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT status, count(*) as count
                FROM conversion_jobs
                GROUP BY status
                """
            )
            stats = {
                JobStatus.PENDING: 0,
                JobStatus.PROCESSING: 0,
                JobStatus.COMPLETED: 0,
                JobStatus.FAILED: 0,
            }
            for row in cur.fetchall():
                stats[row["status"]] = row["count"]
            return stats

    def delete_job(self, job_id: int) -> bool:
        """Delete a single job by ID. Returns True if deleted."""
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM conversion_jobs WHERE id = ?", (job_id,))
            conn.commit()
            return cur.rowcount > 0

    def clear_all_jobs(self) -> int:
        """Clear all job records. Returns count of deleted jobs."""
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM conversion_jobs")
            conn.commit()
            return cur.rowcount
