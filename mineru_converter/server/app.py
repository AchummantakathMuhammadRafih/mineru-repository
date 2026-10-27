"""FastAPI server for asynchronous MinerU PDF parsing, hierarchy correction, and PII redaction."""

import io
import logging
import os
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from mineru_converter.config.constants import DEFAULT_OUTPUT_DIR
from mineru_converter.config.settings import ConversionConfig
from mineru_converter.core.database import JobDatabase, JobStatus, JobStep
from mineru_converter.core.queue_worker import QueueWorker
from mineru_converter.utils.file_utils import format_file_size, sanitize_filename
from mineru_converter.utils.logging_utils import add_file_handler_to_loggers, get_logger
from mineru_converter.utils.zip_utils import create_job_zip

logger = get_logger()

# Base directories
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"
DB_PATH = OUTPUT_DIR / "mineru_jobs.db"
DEFAULT_LOG_FILE = PROJECT_ROOT / "server.log"

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Global worker singleton
_worker: Optional[QueueWorker] = None


def get_worker() -> QueueWorker:
    """Return active global QueueWorker instance."""
    global _worker
    if _worker is None:
        cfg = ConversionConfig(output_dir=OUTPUT_DIR, dump_images=False, redact_pii=True)
        _worker = QueueWorker(
            input_dir=INPUT_DIR,
            output_dir=OUTPUT_DIR,
            db_path=DB_PATH,
            config=cfg,
            poll_interval=1.0,
        )
    return _worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to start and stop background queue worker."""
    # Ensure logs write to server.log in the project root (excluding access logs)
    add_file_handler_to_loggers(DEFAULT_LOG_FILE)
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    from mineru_converter.core.patches import apply_transformers_compat_patches
    apply_transformers_compat_patches()
    worker = get_worker()
    worker.start()
    logger.info(f"MinerU Server started successfully (logging to {DEFAULT_LOG_FILE.name}).")
    try:
        yield
    finally:
        logger.info("Stopping background QueueWorker...")
        worker.stop(timeout=5.0)
        logger.info("MinerU Server shutdown complete.")


app = FastAPI(
    title="MinerU PDF Parser Server",
    version="1.0.0",
    description="Asynchronous PDF Conversion Server: FIFO Queue -> MinerU Parser -> Hierarchy Correction -> Presidio Redaction",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------------------
# API Schemas
# ------------------------------------------------------------------------------
class JobResponse(BaseModel):
    id: int
    file_name: str
    file_size: Optional[int] = 0
    status: str
    current_step: Optional[str] = JobStep.QUEUED
    output_dir: Optional[str] = None
    markdown_file: Optional[str] = None
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class QueueStatsResponse(BaseModel):
    pending: int
    processing: int
    completed: int
    failed: int
    queue_length: int
    is_worker_running: bool
    active_job: Optional[Dict[str, Any]] = None


# ------------------------------------------------------------------------------
# REST API Endpoints
# ------------------------------------------------------------------------------
@app.post("/api/upload", response_model=List[JobResponse])
async def upload_documents(files: List[UploadFile] = File(...)):
    """
    Upload one or multiple PDF documents.
    Saves them to the input directory and enqueues them for sequential background processing.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    worker = get_worker()
    enqueued_jobs = []

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file format: {file.filename}. Only PDF documents are allowed.",
            )

        # Sanitize filename and save to input folder
        clean_name = sanitize_filename(Path(file.filename).name)
        if not clean_name.lower().endswith(".pdf"):
            clean_name += ".pdf"

        target_path = INPUT_DIR / clean_name
        
        # If file already exists with same name, make unique if needed
        if target_path.exists():
            stem = target_path.stem
            timestamp = int(time.time())
            target_path = INPUT_DIR / f"{stem}_{timestamp}.pdf"

        content = await file.read()
        with open(target_path, "wb") as f:
            f.write(content)

        job_record = worker.submit_file(target_path)
        enqueued_jobs.append(job_record)

    return enqueued_jobs


@app.get("/api/jobs", response_model=List[JobResponse])
def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status (PENDING, PROCESSING, COMPLETED, FAILED)"),
    limit: int = Query(50, ge=1, le=500),
):
    """Retrieve list of document conversion jobs with real-time status and step details."""
    worker = get_worker()
    jobs = worker.db.list_jobs(status=status, limit=limit)
    return jobs


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: int):
    """Get detailed state, current step, and error info for a specific job."""
    worker = get_worker()
    job = worker.db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job #{job_id} not found.")
    return job


@app.get("/api/jobs/{job_id}/download")
def download_job_artifacts_zip(job_id: int):
    """Download complete artifact package as a ZIP bundle (includes original PDF, clean MD, redacted MD, JSONs)."""
    worker = get_worker()
    job = worker.db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job #{job_id} not found.")

    if job["status"] != JobStatus.COMPLETED or not job.get("output_dir"):
        raise HTTPException(
            status_code=400,
            detail=f"Job #{job_id} has not completed yet (current status: {job['status']}).",
        )

    out_dir = Path(job["output_dir"])
    orig_path = Path(job["file_path"])
    if not out_dir.exists():
        raise HTTPException(status_code=404, detail="Artifact output directory is missing from disk.")

    zip_bytes = create_job_zip(output_dir=out_dir, original_file_path=orig_path)
    stem_name = Path(job["file_name"]).stem
    zip_filename = f"{stem_name}.zip"

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


@app.get("/api/jobs/{job_id}/preview")
def get_job_preview(job_id: int):
    """Fetch text preview contents of Redacted Markdown, Clean Markdown, and Raw Markdown."""
    worker = get_worker()
    job = worker.db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job #{job_id} not found.")

    if not job.get("output_dir"):
        return {
            "job_id": job_id,
            "status": job["status"],
            "redacted_markdown": None,
            "clean_markdown": None,
            "raw_markdown": None,
        }

    out_path = Path(job["output_dir"])
    stem = Path(job["file_name"]).stem
    redacted_md = out_path / f"{stem}_redacted.md"
    clean_md = out_path / f"{stem}_clean.md"
    raw_md = out_path / f"{stem}.md"

    return {
        "job_id": job_id,
        "status": job["status"],
        "file_name": job["file_name"],
        "redacted_markdown": redacted_md.read_text(encoding="utf-8", errors="replace") if redacted_md.exists() else None,
        "clean_markdown": clean_md.read_text(encoding="utf-8", errors="replace") if clean_md.exists() else None,
        "raw_markdown": raw_md.read_text(encoding="utf-8", errors="replace") if raw_md.exists() else None,
    }


@app.get("/api/jobs/{job_id}/markdown/{md_type}")
def download_job_markdown(job_id: int, md_type: str):
    """
    Download or stream a specific markdown file.
    md_type can be 'redacted', 'clean', or 'raw'.
    """
    worker = get_worker()
    job = worker.db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job #{job_id} not found.")

    if not job.get("output_dir"):
        raise HTTPException(status_code=400, detail="Outputs not generated yet.")

    out_path = Path(job["output_dir"])
    stem = Path(job["file_name"]).stem

    if md_type == "redacted":
        target_md = out_path / f"{stem}_redacted.md"
        dl_name = f"{stem}_redacted.md"
    elif md_type == "clean":
        target_md = out_path / f"{stem}_clean.md"
        dl_name = f"{stem}_clean.md"
    elif md_type == "raw":
        target_md = out_path / f"{stem}.md"
        dl_name = f"{stem}.md"
    else:
        raise HTTPException(status_code=400, detail=f"Invalid md_type '{md_type}'. Use 'redacted', 'clean', or 'raw'.")

    if not target_md.exists():
        raise HTTPException(status_code=404, detail=f"Markdown file {target_md.name} not found.")

    text_content = target_md.read_text(encoding="utf-8", errors="replace")
    return Response(
        content=text_content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{dl_name}"'},
    )


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    """Delete a job record and purge its input file and output directory from disk."""
    worker = get_worker()
    job = worker.db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job #{job_id} not found.")

    # Remove input file
    if job.get("file_path"):
        orig_p = Path(job["file_path"])
        if orig_p.exists():
            try:
                orig_p.unlink()
            except OSError:
                pass

    # Remove output artifacts folder
    if job.get("output_dir"):
        out_p = Path(job["output_dir"])
        doc_root = out_p.parent if out_p.name in ("auto", "txt", "ocr", "vlm", "hybrid", "pipeline") else out_p
        if doc_root.exists() and doc_root.is_dir() and doc_root != OUTPUT_DIR:
            try:
                shutil.rmtree(doc_root, ignore_errors=True)
            except OSError:
                pass

    deleted = worker.db.delete_job(job_id)
    return {"success": deleted, "message": f"Job #{job_id} ({job['file_name']}) deleted successfully."}


@app.delete("/api/jobs")
def clear_all_jobs():
    """Clear all job records and clean all input/output artifacts."""
    worker = get_worker()
    all_jobs = worker.db.list_jobs(limit=1000)
    for j in all_jobs:
        if j.get("file_path"):
            p = Path(j["file_path"])
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        if j.get("output_dir"):
            out_p = Path(j["output_dir"])
            doc_root = out_p.parent if out_p.name in ("auto", "txt", "ocr", "vlm", "hybrid", "pipeline") else out_p
            if doc_root.exists() and doc_root.is_dir() and doc_root != OUTPUT_DIR:
                try:
                    shutil.rmtree(doc_root, ignore_errors=True)
                except OSError:
                    pass

    count = worker.db.clear_all_jobs()
    return {"success": True, "cleared_count": count}


@app.get("/api/stats", response_model=QueueStatsResponse)
def get_stats():
    """Get queue statistics and active worker status."""
    worker = get_worker()
    db_stats = worker.db.get_stats()
    pending = db_stats.get(JobStatus.PENDING, 0)
    processing = db_stats.get(JobStatus.PROCESSING, 0)
    completed = db_stats.get(JobStatus.COMPLETED, 0)
    failed = db_stats.get(JobStatus.FAILED, 0)

    return QueueStatsResponse(
        pending=pending,
        processing=processing,
        completed=completed,
        failed=failed,
        queue_length=pending + processing,
        is_worker_running=worker.is_running,
        active_job=worker.current_job,
    )


@app.get("/api/health")
def health_check():
    """System health check and GPU/CPU detection."""
    import torch
    import mineru

    worker = get_worker()
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "worker_running": worker.is_running,
        "cuda_available": torch.cuda.is_available(),
        "gpu_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU",
        "torch_version": torch.__version__,
        "mineru_version": getattr(mineru, "__version__", "Installed"),
        "input_directory": str(INPUT_DIR),
        "output_directory": str(OUTPUT_DIR),
        "database_file": str(DB_PATH),
        "log_file": str(DEFAULT_LOG_FILE),
    }


@app.get("/api/logs")
def get_logs(tail: int = Query(100, ge=1, le=1000)):
    """Retrieve the latest lines from the server log file."""
    if not DEFAULT_LOG_FILE.exists():
        return {"log_file": str(DEFAULT_LOG_FILE), "total_lines": 0, "lines": []}
    try:
        with open(DEFAULT_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            return {
                "log_file": str(DEFAULT_LOG_FILE),
                "total_lines": len(lines),
                "lines": [line.rstrip() for line in lines[-tail:]],
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read log file: {exc}")


# ------------------------------------------------------------------------------
# Modern Glassmorphic Web UI Dashboard
# ------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def render_web_ui():
    """Render the responsive, modern web UI dashboard for MinerU PDF Conversion."""
    return HTMLResponse(content=WEB_UI_HTML, status_code=200)


WEB_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MinerU Document Processing Server</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0B0F19;
      --card-bg: rgba(22, 29, 47, 0.75);
      --card-border: rgba(255, 255, 255, 0.08);
      --card-hover: rgba(30, 41, 66, 0.85);
      --primary: #6366F1;
      --primary-hover: #4F46E5;
      --primary-light: rgba(99, 102, 241, 0.15);
      --accent: #06B6D4;
      --accent-gradient: linear-gradient(135deg, #6366F1 0%, #06B6D4 100%);
      --text: #F8FAFC;
      --text-muted: #94A3B8;
      --text-sub: #64748B;
      --success: #10B981;
      --success-bg: rgba(16, 185, 129, 0.15);
      --warning: #F59E0B;
      --warning-bg: rgba(245, 158, 11, 0.15);
      --danger: #EF4444;
      --danger-bg: rgba(239, 68, 68, 0.15);
      --purple: #8B5CF6;
      --purple-bg: rgba(139, 92, 246, 0.15);
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background-color: var(--bg);
      background-image: 
        radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.12) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(6, 182, 212, 0.08) 0px, transparent 50%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      padding: 24px;
    }

    .container {
      max-width: 1280px;
      margin: 0 auto;
      width: 100%;
    }

    /* Header */
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 28px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 14px;
    }

    .logo-badge {
      width: 46px;
      height: 46px;
      border-radius: 12px;
      background: var(--accent-gradient);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 24px;
      box-shadow: 0 8px 16px rgba(99, 102, 241, 0.3);
    }

    h1 {
      font-size: 1.5rem;
      font-weight: 700;
      letter-spacing: -0.02em;
    }

    .subtitle {
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-top: 2px;
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .badge-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text-muted);
    }

    .badge-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--success);
      box-shadow: 0 0 10px var(--success);
      animation: pulse 2s infinite;
    }

    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.5; transform: scale(1.15); }
    }

    /* KPI Row */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }

    .kpi-card {
      background: var(--card-bg);
      backdrop-filter: blur(12px);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 18px 20px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      transition: all 0.2s ease;
    }

    .kpi-card:hover {
      border-color: rgba(99, 102, 241, 0.4);
      transform: translateY(-2px);
    }

    .kpi-label {
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-sub);
      font-weight: 600;
    }

    .kpi-value {
      font-size: 1.8rem;
      font-weight: 700;
      color: var(--text);
    }

    /* Upload Zone */
    .upload-card {
      background: var(--card-bg);
      backdrop-filter: blur(12px);
      border: 2px dashed rgba(99, 102, 241, 0.35);
      border-radius: 16px;
      padding: 32px 24px;
      text-align: center;
      margin-bottom: 28px;
      cursor: pointer;
      transition: all 0.25s ease;
      position: relative;
    }

    .upload-card:hover, .upload-card.drag-over {
      border-color: var(--primary);
      background: rgba(99, 102, 241, 0.06);
      transform: scale(1.005);
    }

    .upload-icon {
      font-size: 42px;
      margin-bottom: 12px;
      display: inline-block;
      animation: float 3s ease-in-out infinite;
    }

    @keyframes float {
      0%, 100% { transform: translateY(0); }
      50% { transform: translateY(-5px); }
    }

    .upload-title {
      font-size: 1.15rem;
      font-weight: 600;
      margin-bottom: 6px;
    }

    .upload-desc {
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-bottom: 16px;
    }

    .upload-pipeline-flow {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      font-size: 0.75rem;
      color: var(--text-sub);
      flex-wrap: wrap;
    }

    .pipeline-step {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      background: rgba(255, 255, 255, 0.04);
      padding: 4px 10px;
      border-radius: 6px;
      border: 1px solid var(--card-border);
    }

    .file-input {
      display: none;
    }

    /* Queue Table Section */
    .section-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
    }

    .section-title {
      font-size: 1.15rem;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .table-card {
      background: var(--card-bg);
      backdrop-filter: blur(12px);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      overflow: hidden;
      margin-bottom: 30px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
    }

    th {
      background: rgba(11, 15, 25, 0.5);
      padding: 14px 18px;
      font-size: 0.75rem;
      font-weight: 600;
      color: var(--text-sub);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      border-bottom: 1px solid var(--card-border);
    }

    td {
      padding: 16px 18px;
      font-size: 0.85rem;
      border-bottom: 1px solid var(--card-border);
      vertical-align: middle;
    }

    tr:last-child td {
      border-bottom: none;
    }

    tr:hover td {
      background: rgba(255, 255, 255, 0.02);
    }

    .doc-name {
      font-weight: 600;
      color: var(--text);
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .doc-error {
      font-size: 0.75rem;
      color: var(--danger);
      background: var(--danger-bg);
      padding: 4px 8px;
      border-radius: 4px;
      max-width: 380px;
      word-break: break-all;
    }

    /* Status & Step Badges */
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    .badge-pending {
      background: var(--warning-bg);
      color: var(--warning);
      border: 1px solid rgba(245, 158, 11, 0.3);
    }

    .badge-processing {
      background: var(--primary-light);
      color: #818CF8;
      border: 1px solid rgba(99, 102, 241, 0.4);
      animation: pulse 1.8s infinite;
    }

    .badge-completed {
      background: var(--success-bg);
      color: var(--success);
      border: 1px solid rgba(16, 185, 129, 0.3);
    }

    .badge-failed {
      background: var(--danger-bg);
      color: var(--danger);
      border: 1px solid rgba(239, 68, 68, 0.3);
    }

    .step-badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 0.7rem;
      font-family: 'JetBrains Mono', monospace;
      background: rgba(255, 255, 255, 0.06);
      color: var(--text-muted);
    }

    .step-badge.active-step {
      background: rgba(99, 102, 241, 0.2);
      color: #A5B4FC;
      border: 1px solid rgba(99, 102, 241, 0.5);
    }

    /* Action Buttons */
    .btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 7px 12px;
      border-radius: 8px;
      font-size: 0.78rem;
      font-weight: 500;
      cursor: pointer;
      border: 1px solid transparent;
      transition: all 0.15s ease;
      text-decoration: none;
    }

    .btn-primary {
      background: var(--primary);
      color: white;
    }

    .btn-primary:hover {
      background: var(--primary-hover);
    }

    .btn-secondary {
      background: rgba(255, 255, 255, 0.06);
      border-color: var(--card-border);
      color: var(--text);
    }

    .btn-secondary:hover {
      background: rgba(255, 255, 255, 0.1);
      border-color: rgba(255, 255, 255, 0.2);
    }

    .btn-danger {
      background: transparent;
      color: var(--danger);
      border-color: rgba(239, 68, 68, 0.2);
    }

    .btn-danger:hover {
      background: var(--danger-bg);
      border-color: var(--danger);
    }

    .btn-icon {
      padding: 6px 10px;
    }

    .actions-group {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }

    /* Modal for Preview */
    .modal-overlay {
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(8px);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 100;
      padding: 24px;
    }

    .modal-overlay.active {
      display: flex;
    }

    .modal-window {
      background: #111827;
      border: 1px solid var(--card-border);
      border-radius: 16px;
      width: 100%;
      max-width: 960px;
      max-height: 85vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    }

    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 16px 20px;
      border-bottom: 1px solid var(--card-border);
    }

    .modal-title {
      font-size: 1.05rem;
      font-weight: 600;
    }

    .modal-close {
      background: none;
      border: none;
      font-size: 1.4rem;
      color: var(--text-sub);
      cursor: pointer;
    }

    .modal-close:hover {
      color: var(--text);
    }

    .tabs {
      display: flex;
      background: rgba(0, 0, 0, 0.25);
      border-bottom: 1px solid var(--card-border);
      padding: 0 16px;
    }

    .tab-btn {
      padding: 12px 18px;
      font-size: 0.82rem;
      font-weight: 600;
      background: none;
      border: none;
      color: var(--text-sub);
      cursor: pointer;
      border-bottom: 2px solid transparent;
      transition: all 0.15s ease;
    }

    .tab-btn.active {
      color: var(--text);
      border-bottom-color: var(--primary);
    }

    .modal-body {
      padding: 20px;
      overflow-y: auto;
      flex: 1;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.82rem;
      line-height: 1.6;
      white-space: pre-wrap;
      word-break: break-word;
      color: #E2E8F0;
      background: #090D16;
    }

    .empty-state {
      padding: 48px 24px;
      text-align: center;
      color: var(--text-sub);
    }

    /* Toast Notification */
    .toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      padding: 12px 20px;
      border-radius: 8px;
      background: #1F2937;
      color: var(--text);
      border: 1px solid var(--card-border);
      font-size: 0.85rem;
      display: none;
      z-index: 1000;
      box-shadow: 0 10px 25px rgba(0,0,0,0.4);
    }

    .toast.show {
      display: block;
      animation: slideIn 0.3s ease;
    }

    @keyframes slideIn {
      from { transform: translateY(20px); opacity: 0; }
      to { transform: translateY(0); opacity: 1; }
    }
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <header>
      <div class="brand">
        <div class="logo-badge">⚡</div>
        <div>
          <h1>MinerU Conversion Server</h1>
          <div class="subtitle">Asynchronous FIFO Queue • MinerU Engine • Dominant Font Hierarchy • Presidio PII Redaction</div>
        </div>
      </div>
      <div class="header-actions">
        <div class="badge-pill">
          <span class="badge-dot" id="server-status-dot"></span>
          <span id="server-status-text">Worker Active (FIFO Mode)</span>
        </div>
        <a href="/docs" target="_blank" class="btn btn-secondary" title="Open Interactive API Docs">📖 API Docs</a>
      </div>
    </header>

    <!-- KPI Row -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <span class="kpi-label">Pending In Queue</span>
        <span class="kpi-value" id="kpi-pending">0</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Currently Processing</span>
        <span class="kpi-value" id="kpi-processing" style="color: #818CF8;">0</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Completed Documents</span>
        <span class="kpi-value" id="kpi-completed" style="color: var(--success);">0</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Failed</span>
        <span class="kpi-value" id="kpi-failed" style="color: var(--danger);">0</span>
      </div>
    </div>

    <!-- Upload Card -->
    <div class="upload-card" id="drop-zone" onclick="document.getElementById('file-upload').click()">
      <input type="file" id="file-upload" class="file-input" multiple accept=".pdf" onchange="handleFileSelect(event)">
      <div class="upload-icon">📄</div>
      <div class="upload-title">Drop PDF documents here, or click to browse</div>
      <div class="upload-desc">Documents are queued immediately and processed sequentially one at a time by the async background worker.</div>
      <div class="upload-pipeline-flow">
        <span class="pipeline-step">1. Upload PDF</span>
        <span>→</span>
        <span class="pipeline-step">2. Async FIFO Queue</span>
        <span>→</span>
        <span class="pipeline-step">3. MinerU Parse</span>
        <span>→</span>
        <span class="pipeline-step">4. Font Hierarchy Correction</span>
        <span>→</span>
        <span class="pipeline-step">5. Presidio PII Masking</span>
      </div>
    </div>

    <!-- Queue Table -->
    <div class="section-header">
      <div class="section-title">
        <span>📋 Document Queue & Output Artifacts</span>
      </div>
      <div class="actions-group">
        <button class="btn btn-secondary" onclick="fetchJobs()">🔄 Refresh</button>
        <button class="btn btn-danger" onclick="clearAllJobs()">🗑️ Clear All</button>
      </div>
    </div>

    <div class="table-card">
      <table>
        <thead>
          <tr>
            <th style="width: 50px;">ID</th>
            <th>Document</th>
            <th style="width: 140px;">Status</th>
            <th style="width: 170px;">Pipeline Step</th>
            <th style="width: 90px;">Duration</th>
            <th style="width: 160px;">Completed At</th>
            <th style="width: 250px;">Actions</th>
          </tr>
        </thead>
        <tbody id="jobs-table-body">
          <tr>
            <td colspan="7" class="empty-state">Loading queue state...</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Artifact Preview Modal -->
  <div class="modal-overlay" id="preview-modal" onclick="if(event.target===this) closeModal()">
    <div class="modal-window">
      <div class="modal-header">
        <div class="modal-title" id="modal-doc-title">Document Preview</div>
        <button class="modal-close" onclick="closeModal()">&times;</button>
      </div>
      <div class="tabs">
        <button class="tab-btn active" id="tab-btn-redacted" onclick="switchTab('redacted')">🛡️ Redacted Markdown (Presidio)</button>
        <button class="tab-btn" id="tab-btn-clean" onclick="switchTab('clean')">✨ Clean Markdown (Hierarchy)</button>
        <button class="tab-btn" id="tab-btn-raw" onclick="switchTab('raw')">📄 Raw MinerU Markdown</button>
      </div>
      <div class="modal-body" id="modal-content">
        Loading preview content...
      </div>
    </div>
  </div>

  <!-- Toast -->
  <div class="toast" id="toast"></div>

  <script>
    let currentPreviewData = null;
    let activeTab = 'redacted';
    let pollInterval = 2000;

    // Toast helper
    function showToast(msg) {
      const toast = document.getElementById('toast');
      toast.innerText = msg;
      toast.classList.add('show');
      setTimeout(() => toast.classList.remove('show'), 3000);
    }

    // Drag and Drop
    const dropZone = document.getElementById('drop-zone');
    ['dragenter', 'dragover'].forEach(name => {
      dropZone.addEventListener(name, (e) => {
        e.preventDefault();
        dropZone.classList.add('drag-over');
      });
    });
    ['dragleave', 'drop'].forEach(name => {
      dropZone.addEventListener(name, (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
      });
    });
    dropZone.addEventListener('drop', (e) => {
      const dt = e.dataTransfer;
      const files = dt.files;
      if (files.length) uploadFiles(files);
    });

    function handleFileSelect(event) {
      const files = event.target.files;
      if (files.length) uploadFiles(files);
    }

    async function uploadFiles(files) {
      const formData = new FormData();
      let pdfCount = 0;
      for (let i = 0; i < files.length; i++) {
        if (files[i].name.toLowerCase().endsWith('.pdf')) {
          formData.append('files', files[i]);
          pdfCount++;
        }
      }
      if (pdfCount === 0) {
        showToast('Please select valid PDF documents.');
        return;
      }

      showToast(`Uploading ${pdfCount} document(s)...`);
      try {
        const resp = await fetch('/api/upload', {
          method: 'POST',
          body: formData,
        });
        if (!resp.ok) {
          const err = await resp.json();
          throw new Error(err.detail || 'Upload failed');
        }
        const data = await resp.json();
        showToast(`Successfully queued ${data.length} document(s)!`);
        document.getElementById('file-upload').value = '';
        fetchJobs();
      } catch (err) {
        alert('Upload Error: ' + err.message);
      }
    }

    async function fetchStats() {
      try {
        const resp = await fetch('/api/stats');
        const stats = await resp.json();
        document.getElementById('kpi-pending').innerText = stats.pending;
        document.getElementById('kpi-processing').innerText = stats.processing;
        document.getElementById('kpi-completed').innerText = stats.completed;
        document.getElementById('kpi-failed').innerText = stats.failed;
      } catch (e) {
        console.error('Stats fetch error:', e);
      }
    }

    async function fetchJobs() {
      await fetchStats();
      try {
        const resp = await fetch('/api/jobs?limit=50');
        const jobs = await resp.json();
        const tbody = document.getElementById('jobs-table-body');

        if (!jobs || jobs.length === 0) {
          tbody.innerHTML = `<tr><td colspan="7" class="empty-state">No documents submitted yet. Drop a PDF above to begin processing.</td></tr>`;
          return;
        }

        let html = '';
        jobs.forEach(job => {
          const isComp = job.status === 'COMPLETED';
          const isProc = job.status === 'PROCESSING';
          const isFail = job.status === 'FAILED';
          const isPend = job.status === 'PENDING';

          let statusClass = 'badge-pending';
          if (isProc) statusClass = 'badge-processing';
          else if (isComp) statusClass = 'badge-completed';
          else if (isFail) statusClass = 'badge-failed';

          let stepName = job.current_step || (isComp ? 'COMPLETED' : 'QUEUED');
          let stepActive = isProc ? 'active-step' : '';

          let durationStr = job.duration_seconds ? `${job.duration_seconds.toFixed(1)}s` : '—';
          let completedAtStr = job.completed_at ? job.completed_at.split('.')[0].replace('T', ' ') : '—';

          let actionsHtml = `<div class="actions-group">`;
          if (isComp) {
            actionsHtml += `
              <a href="/api/jobs/${job.id}/download" class="btn btn-primary" title="Download Complete Artifact ZIP">📦 ZIP</a>
              <button class="btn btn-secondary" onclick="openPreview(${job.id}, '${escapeQuotes(job.file_name)}')">👁️ Preview</button>
            `;
          } else if (isProc) {
            actionsHtml += `<span class="step-badge active-step">⏳ Processing...</span>`;
          } else if (isPend) {
            actionsHtml += `<span class="step-badge">🕒 In FIFO Queue</span>`;
          } else {
            actionsHtml += `<span style="color: var(--danger); font-size: 0.75rem;">Failed</span>`;
          }

          actionsHtml += `
            <button class="btn btn-danger btn-icon" onclick="deleteJob(${job.id})" title="Delete job & artifacts">🗑️</button>
          </div>`;

          let errorHtml = (isFail && job.error_message) 
            ? `<div class="doc-error">${escapeQuotes(job.error_message)}</div>` 
            : '';

          html += `
            <tr>
              <td>#${job.id}</td>
              <td>
                <div class="doc-name">
                  <span>${escapeQuotes(job.file_name)}</span>
                  ${errorHtml}
                </div>
              </td>
              <td><span class="badge ${statusClass}">${job.status}</span></td>
              <td><span class="step-badge ${stepActive}">${stepName}</span></td>
              <td>${durationStr}</td>
              <td style="color: var(--text-muted); font-size: 0.78rem;">${completedAtStr}</td>
              <td>${actionsHtml}</td>
            </tr>
          `;
        });

        tbody.innerHTML = html;
      } catch (e) {
        console.error('Jobs fetch error:', e);
      }
    }

    async function deleteJob(jobId) {
      if (!confirm(`Delete Job #${jobId} and remove all associated files?`)) return;
      try {
        const resp = await fetch(`/api/jobs/${jobId}`, { method: 'DELETE' });
        if (resp.ok) {
          showToast(`Job #${jobId} deleted.`);
          fetchJobs();
        }
      } catch (e) {
        alert('Delete failed: ' + e);
      }
    }

    async function clearAllJobs() {
      if (!confirm('Are you sure you want to clear all jobs and output artifacts?')) return;
      try {
        const resp = await fetch('/api/jobs', { method: 'DELETE' });
        const res = await resp.json();
        showToast(`Cleared ${res.cleared_count} records.`);
        fetchJobs();
      } catch (e) {
        alert('Clear failed: ' + e);
      }
    }

    async function openPreview(jobId, filename) {
      document.getElementById('modal-doc-title').innerText = `Preview: #${jobId} — ${filename}`;
      document.getElementById('modal-content').innerText = 'Loading preview content...';
      document.getElementById('preview-modal').classList.add('active');

      try {
        const resp = await fetch(`/api/jobs/${jobId}/preview`);
        currentPreviewData = await resp.json();
        switchTab(activeTab);
      } catch (e) {
        document.getElementById('modal-content').innerText = 'Failed to load preview: ' + e;
      }
    }

    function switchTab(tab) {
      activeTab = tab;
      document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
      document.getElementById(`tab-btn-${tab}`).classList.add('active');

      if (!currentPreviewData) return;
      const contentEl = document.getElementById('modal-content');

      if (tab === 'redacted') {
        contentEl.innerText = currentPreviewData.redacted_markdown || 'Redacted markdown not available.';
      } else if (tab === 'clean') {
        contentEl.innerText = currentPreviewData.clean_markdown || 'Clean hierarchy markdown not available.';
      } else if (tab === 'raw') {
        contentEl.innerText = currentPreviewData.raw_markdown || 'Raw markdown not available.';
      }
    }

    function closeModal() {
      document.getElementById('preview-modal').classList.remove('active');
    }

    function escapeQuotes(str) {
      if (!str) return '';
      return str.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // Auto-refresh interval loop
    setInterval(fetchJobs, pollInterval);
    fetchJobs();
  </script>
</body>
</html>
"""
