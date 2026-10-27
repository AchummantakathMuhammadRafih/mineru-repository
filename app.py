"""Streamlit UI for MinerU PDF Converter: Upload, Live Status, and Zip Download."""

import os
import shutil
import sys
import time
from pathlib import Path

# Add project root to sys.path so modules can be imported
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from mineru_converter.core.database import JobDatabase, JobStatus
from mineru_converter.utils.zip_utils import create_job_zip
from mineru_converter.core.watcher import BackgroundWatcher
from mineru_converter.config.settings import ConversionConfig

# Configuration paths
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"
DB_PATH = OUTPUT_DIR / "mineru_jobs.db"

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Page configuration
st.set_page_config(
    page_title="MinerU PDF Parser",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for polished, modern look
st.markdown(
    """
    <style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 700;
        margin-bottom: 0.2rem;
        color: #1E293B;
    }
    .sub-title {
        color: #64748B;
        margin-bottom: 1.5rem;
        font-size: 1.05rem;
    }
    .metric-card {
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 12px 18px;
        text-align: center;
    }
    .metric-value {
        font-size: 1.6rem;
        font-weight: 700;
        color: #0F172A;
    }
    .metric-label {
        font-size: 0.85rem;
        color: #64748B;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .status-badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .status-completed { background-color: #DCFCE7; color: #166534; }
    .status-processing { background-color: #FEF3C7; color: #92400E; }
    .status-pending { background-color: #F1F5F9; color: #475569; }
    .status-failed { background-color: #FEE2E2; color: #991B1B; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Initialize Database
db = JobDatabase(DB_PATH)

# Sidebar settings & runner controls
with st.sidebar:
    st.header("⚙️ Configuration")
    poll_interval = st.slider("Auto-refresh interval (seconds)", min_value=1, max_value=15, value=3)
    auto_refresh = st.checkbox("Auto-refresh queue", value=True)

    st.divider()
    st.subheader("🤖 Background Worker")

    # In-memory background runner trigger for single-process testing
    run_embedded = st.checkbox("Run embedded worker in this app", value=False, help="Process uploaded files inside this Streamlit process without needing a separate terminal worker.")
    
    if run_embedded:
        # Check if there is a pending job and process 1 file
        if "watcher" not in st.session_state:
            worker_cfg = ConversionConfig(output_dir=OUTPUT_DIR, dump_images=False, redact_pii=True)
            st.session_state["watcher"] = BackgroundWatcher(
                input_dir=INPUT_DIR,
                output_dir=OUTPUT_DIR,
                db_path=DB_PATH,
                config=worker_cfg,
            )
        watcher = st.session_state["watcher"]
        
        with st.status("Worker running...", expanded=False) as worker_status:
            did_work = watcher.run_once()
            if did_work:
                worker_status.update(label="Processed 1 document!", state="complete")
            else:
                worker_status.update(label="Worker idle (waiting for input)", state="running")

    st.caption(f"Input: `{INPUT_DIR}`")
    st.caption(f"Output: `{OUTPUT_DIR}`")
    st.caption(f"Database: `{DB_PATH.name}`")

# Main Header
st.markdown('<div class="main-title">MinerU Document Parser & Downloader</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Upload documents, process queue one file at a time, and download complete artifact ZIP packages.</div>', unsafe_allow_html=True)

# Initialize uploader key in session state so we can reset it on deletion or save
if "uploader_key" not in st.session_state:
    st.session_state["uploader_key"] = 0

# 1. Upload Card
with st.container():
    uploaded_files = st.file_uploader(
        "Upload PDF documents (files are saved directly to input/ folder for FIFO processing):",
        type=["pdf"],
        accept_multiple_files=True,
        key=f"pdf_uploader_{st.session_state['uploader_key']}",
        help="Upload one or multiple PDFs. The background watcher processes them strictly one at a time.",
    )

    if uploaded_files:
        saved_count = 0
        for uploaded_file in uploaded_files:
            target_path = INPUT_DIR / uploaded_file.name
            if not target_path.exists():
                with open(target_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                db.register_or_get_job(target_path, file_size=uploaded_file.size)
                saved_count += 1

        if saved_count > 0:
            st.session_state["uploader_key"] += 1
            st.toast(f"Saved {saved_count} file(s) to queue.")
            st.rerun()

st.divider()

# 2. Stats Row
stats = db.get_stats()
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown(
        f'<div class="metric-card"><div class="metric-value">{stats.get("PENDING", 0)}</div><div class="metric-label">Pending In Queue</div></div>',
        unsafe_allow_html=True,
    )
with col2:
    st.markdown(
        f'<div class="metric-card"><div class="metric-value">{stats.get("PROCESSING", 0)}</div><div class="metric-label">Currently Processing</div></div>',
        unsafe_allow_html=True,
    )
with col3:
    st.markdown(
        f'<div class="metric-card"><div class="metric-value">{stats.get("COMPLETED", 0)}</div><div class="metric-label">Completed</div></div>',
        unsafe_allow_html=True,
    )
with col4:
    st.markdown(
        f'<div class="metric-card"><div class="metric-value">{stats.get("FAILED", 0)}</div><div class="metric-label">Failed</div></div>',
        unsafe_allow_html=True,
    )

st.write("")

# 3. Job Queue Table & Download Actions
queue_col1, queue_col2 = st.columns([3, 1])
queue_col1.subheader("📋 Processing Queue & Downloads")
with queue_col2:
    if st.button("🗑️ Clear All Records", help="Clear all records from database and clean input/output files", use_container_width=True):
        # Clean input and output files for all jobs
        all_jobs = db.list_jobs(limit=1000)
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
                # Remove specific doc folder under output/
                doc_root = out_p.parent if out_p.name in ("auto", "txt", "ocr") else out_p
                if doc_root.exists() and doc_root.is_dir() and doc_root != OUTPUT_DIR:
                    try:
                        shutil.rmtree(doc_root, ignore_errors=True)
                    except OSError:
                        pass
        count = db.clear_all_jobs()
        st.session_state["uploader_key"] += 1
        st.toast(f"Cleared {count} record(s) and associated files.")
        st.rerun()

jobs = db.list_jobs(limit=100)

queue_container = st.container()

with queue_container:
    if not jobs:
        st.info("No documents uploaded yet. Upload a PDF above to begin.")
    else:
        # Table header
        header_cols = st.columns([0.6, 2.3, 1.1, 0.9, 1.5, 1.5, 0.9])
        header_cols[0].markdown("**ID**")
        header_cols[1].markdown("**File Name**")
        header_cols[2].markdown("**Status**")
        header_cols[3].markdown("**Duration**")
        header_cols[4].markdown("**Completed At**")
        header_cols[5].markdown("**Download**")
        header_cols[6].markdown("**Action**")
        st.divider()

        for job in jobs:
            row_cols = st.columns([0.6, 2.3, 1.1, 0.9, 1.5, 1.5, 0.9])
            job_id = job["id"]
            file_name = job["file_name"]
            status = job["status"]
            duration = f"{job['duration_seconds']:.1f}s" if job.get("duration_seconds") else "-"
            completed_at = job["completed_at"].split(".")[0].replace("T", " ") if job.get("completed_at") else "-"

            # ID
            row_cols[0].write(f"#{job_id}")

            # Name with error tooltip if failed
            if status == JobStatus.FAILED:
                row_cols[1].markdown(f"**{file_name}**  \n:red[Error: {job.get('error_message') or 'Conversion failed'}]")
            else:
                row_cols[1].markdown(f"**{file_name}**")

            # Status badge
            badge_class = {
                JobStatus.COMPLETED: "status-completed",
                JobStatus.PROCESSING: "status-processing",
                JobStatus.PENDING: "status-pending",
                JobStatus.FAILED: "status-failed",
            }.get(status, "status-pending")
            row_cols[2].markdown(f'<span class="status-badge {badge_class}">{status}</span>', unsafe_allow_html=True)

            # Duration & Timestamp
            row_cols[3].write(duration)
            row_cols[4].write(completed_at)

            # Download button
            if status == JobStatus.COMPLETED and job.get("output_dir"):
                output_dir = Path(job["output_dir"])
                original_file = Path(job["file_path"])
                stem_name = Path(file_name).stem
                zip_filename = f"{stem_name}.zip"

                if output_dir.exists():
                    try:
                        zip_bytes = create_job_zip(output_dir=output_dir, original_file_path=original_file)
                        row_cols[5].download_button(
                            label="📦 Download ZIP",
                            data=zip_bytes,
                            file_name=zip_filename,
                            mime="application/zip",
                            key=f"dl_{job_id}",
                        )
                    except Exception as zip_err:
                        row_cols[5].error("Zip error")
                else:
                    row_cols[5].caption("Artifacts missing")
            elif status == JobStatus.PROCESSING:
                row_cols[5].caption("⏳ Converting...")
            elif status == JobStatus.PENDING:
                row_cols[5].caption("🕒 In Queue")
            else:
                row_cols[5].caption("—")

            # Delete specific record action
            if row_cols[6].button("🗑️", key=f"del_{job_id}", help=f"Delete job #{job_id} ({file_name}) and clear files"):
                # 1. Remove input file
                orig_p = Path(job["file_path"])
                if orig_p.exists():
                    try:
                        orig_p.unlink()
                    except OSError:
                        pass

                # 2. Remove output artifacts directory if it exists
                if job.get("output_dir"):
                    out_p = Path(job["output_dir"])
                    doc_root = out_p.parent if out_p.name in ("auto", "txt", "ocr") else out_p
                    if doc_root.exists() and doc_root.is_dir() and doc_root != OUTPUT_DIR:
                        try:
                            shutil.rmtree(doc_root, ignore_errors=True)
                        except OSError:
                            pass

                db.delete_job(job_id)
                st.session_state["uploader_key"] += 1
                st.toast(f"Deleted job #{job_id} ({file_name}) and cleared files")
                st.rerun()

            # Expandable output previews for completed documents
            if status == JobStatus.COMPLETED and job.get("output_dir"):
                out_path = Path(job["output_dir"])
                stem = Path(file_name).stem
                clean_md = out_path / f"{stem}_clean.md"
                redacted_md = out_path / f"{stem}_redacted.md"
                raw_md = out_path / f"{stem}.md"

                if clean_md.exists() or redacted_md.exists() or raw_md.exists():
                    with st.expander(f"👁️ Preview Document Artifacts (#{job_id} - {file_name})", expanded=False):
                        tab_redacted, tab_clean, tab_raw = st.tabs([
                            "🛡️ Redacted Markdown (Presidio)",
                            "✨ Clean Markdown (Hierarchy)",
                            "📄 Raw MinerU Markdown",
                        ])
                        with tab_redacted:
                            if redacted_md.exists():
                                red_text = redacted_md.read_text(encoding="utf-8", errors="replace")
                                st.download_button(
                                    "📥 Download Redacted Markdown",
                                    data=red_text,
                                    file_name=f"{stem}_redacted.md",
                                    mime="text/markdown",
                                    key=f"dl_red_{job_id}",
                                )
                                st.markdown(red_text)
                            else:
                                st.info("Redacted markdown not found for this document.")
                        with tab_clean:
                            if clean_md.exists():
                                cl_text = clean_md.read_text(encoding="utf-8", errors="replace")
                                st.download_button(
                                    "📥 Download Clean Markdown",
                                    data=cl_text,
                                    file_name=f"{stem}_clean.md",
                                    mime="text/markdown",
                                    key=f"dl_cl_{job_id}",
                                )
                                st.markdown(cl_text)
                            else:
                                st.info("Clean markdown not found for this document.")
                        with tab_raw:
                            if raw_md.exists():
                                raw_text = raw_md.read_text(encoding="utf-8", errors="replace")
                                st.download_button(
                                    "📥 Download Raw Markdown",
                                    data=raw_text,
                                    file_name=f"{stem}.md",
                                    mime="text/markdown",
                                    key=f"dl_raw_{job_id}",
                                )
                                st.markdown(raw_text)
                            else:
                                st.info("Raw markdown not found for this document.")

# Auto-refresh loop
if auto_refresh:
    time.sleep(poll_interval)
    st.rerun()
