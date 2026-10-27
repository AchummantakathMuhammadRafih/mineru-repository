# MinerU Document Conversion Toolkit 📄⚡

A modular, production-grade CLI suite and Python framework for high-fidelity PDF parsing and artifact extraction using **MinerU** (`magic-pdf`).

---

## 🌟 Key Features

- **Multi-Engine Document Parsing**:
  - Full MinerU extraction suite for PDFs (Markdown, Content Lists V1/V2, Layout JSON, Model ASTs, Annotated visual PDFs).
  - 📝 **Markdown (`.md`)**: Clean, formatted text with LaTeX math and image markdown links.
  - 📋 **V1 Content List (`_content_list.json` / `_v1list.json`)**: Flat block-level element stream.
  - 📊 **V2 Content List (`_content_list_v2.json` / `_v2list.json`)**: Rich hierarchical block stream with bounding boxes and metadata.
  - 🌳 **Middle AST JSON (`_middle.json`)**: Full document layout analysis intermediate tree.
  - 🤖 **Model JSON (`_model.json`)**: Raw neural network layout/OCR/table detection outputs.
  - 🖼️ **Visual Bounding Box Overlays**:
    - `_layout.pdf`: Color-coded block layout bounding boxes.
    - `_span.pdf`: Fine-grained inline token/span bounding boxes.
  - 📑 **Original Slice (`_origin.pdf`)**: Normalized PDF source for the processed page range.
  - 🎨 **Extracted Media (`images/`)**: High-res extracted diagrams, tables, equations, and photo figures.
  - 🧾 **Conversion Manifest (`conversion_manifest.json`)**: Checksums, file paths, and execution metadata.
  - 📈 **Document Summary (`conversion_summary.json`)**: Word counts, character counts, tables, formulas, and timing stats.

- **Modular Architecture**: Built following clean architectural principles with separated config, models, core engines, exporters, and CLI layers.
- **Multiple Backends**:
  - `pipeline`: Robust local hybrid pipeline (DocLayout, OCR, Table Cls/Rec, Unimernet).
  - `hybrid-engine`: High-accuracy local VLM + layout engine.
  - `vlm-engine`: Pure vision-language model inference.
  - `hybrid-http-client` / `vlm-http-client`: Connect to remote MinerU API / OpenAI-compatible servers.
- **Rich Terminal UI**: Beautiful banners, colored tables, progress indicators, and artifact status tables powered by `rich`.
- **Batch Processing**: Recursively convert entire directories of PDFs with progress tracking and failure isolation.
- **Python Library API**: Use directly within your Python applications as an importable module.

---

## 🏗️ Project Structure

```
mineru-parser/
├── mineru_converter/               # Core PDF Conversion Package
│   ├── __init__.py                 # Root exports & public API
│   ├── __main__.py                 # Entrypoint for `python -m mineru_converter`
│   ├── config/                     # Configuration management
│   ├── models/                     # Data contracts and schemas
│   ├── core/                       # Core engine orchestrators
│   ├── exporters/                  # Output file formatters & manifest builders
│   ├── utils/                      # Shared utilities
│   └── cli/                        # Click CLI commands (convert, batch, inspect, info)
├── tests/                          # Test suite
│   └── test_converter.py           # Unit & integration tests
├── cli.py                          # Top-level CLI launcher
└── setup.py                        # Package setup & console scripts
```

---

## 🚀 CLI Quick Start

### 1. Single PDF Conversion
Convert a PDF and generate all output artifacts:
```bash
# Using installed console script
mineru-convert convert input/document.pdf -o output/results

# Or using cli.py
python cli.py convert input/document.pdf -o output/results
```

### 2. Page Range Selection
Process only a specific page range (0-indexed, inclusive):
```bash
# Convert only the first 3 pages (pages 0, 1, 2)
mineru-convert convert input/document.pdf -s 0 -e 2 -o output/results
```

### 3. Backend & Parsing Mode
Choose backend engine and parsing mode:
```bash
# Use fast text-extraction mode for clean digital PDFs
mineru-convert convert input/document.pdf -m txt

# Force OCR mode for scanned documents
mineru-convert convert input/document.pdf -m ocr -l ch
```

### 4. Batch Directory Conversion
Convert all PDFs in a directory recursively:
```bash
mineru-convert batch ./input -o ./output/batch_results
```

### 5. Asynchronous Conversion Server & Web Dashboard 🚀
Run everything as a unified, asynchronous server with a dedicated background worker thread, REST API, interactive Swagger docs, and built-in glassmorphic Web UI.

#### Running in Detached Mode (Background Daemon)
Run the server in the background completely detached from the current terminal:
```bash
# Start in detached mode (writes logs to server.log, tracks PID in server.pid)
python server.py --detach

# Or via CLI
mineru-convert server --detach

# Check if the detached server is running
python server.py --status

# Stop the detached server
python server.py --stop
```

#### Running in Interactive Mode (Foreground)
```bash
# Start in foreground
python server.py --host 0.0.0.0 --port 8000

# Or using the CLI command
mineru-convert server --port 8000
```

#### Monitoring Logs
- 📜 **Log File**: All server requests and pipeline worker steps are logged to [`server.log`](file:///c:/Users/Muhammad.Rafih/Code/mineru-parser/server.log) with automatic rotating file backups.
- 💻 **Follow Live in Terminal**:
  - PowerShell: `Get-Content -Wait server.log`
  - Linux/Git Bash: `tail -f server.log`
- 🌐 **Web & API Access**:
  - Web Dashboard: `http://localhost:8000/` (Drag-and-drop upload, live polling table, step indicators, and artifact preview tabs).
  - Interactive Swagger Docs: `http://localhost:8000/docs`
  - Direct Log API: `http://localhost:8000/api/logs?tail=100`
- ⚡ **Sequential FIFO Processing**: An asynchronous background worker thread picks up pending jobs **strictly one at a time**, executing:
  1. **Upload & Enqueue** (`POST /api/upload`)
  2. **MinerU Parser Execution** (`Step: PARSING`)
  3. **Dominant Font Hierarchy Correction** (`Step: HIERARCHY_CORRECTION` -> produces clean Markdown `*_clean.md`)
  4. **Presidio PII Redaction** (`Step: REDACTION` -> masks sensitive entities with stars `*_redacted.md`)
  5. **Artifacts & Download** (`GET /api/jobs/{id}/download` for complete ZIP package)

### 6. Background Folder Runner (CLI Watcher)
Listen to an input directory, process files strictly **one at a time** (FIFO), route outputs to the destination directory, and persist execution states (`PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`) in SQLite:
```bash
# Start listening to ./input (default output ./output/mineru, DB: ./output/mineru/mineru_jobs.db)
mineru-convert listen ./input -o ./output

# Custom poll interval and custom SQLite database path
mineru-convert listen ./input -o ./output --interval 2.0 --db-path ./output/my_jobs.db
```

View the live or historical queue in terminal:
```bash
# View all recent jobs
mineru-convert jobs --db-path ./output/mineru_jobs.db

# Filter by state (PENDING, PROCESSING, COMPLETED, FAILED)
mineru-convert jobs --status COMPLETED
```

### 7. Streamlit Web UI
Alternatively, launch the Streamlit frontend:
```bash
streamlit run app.py
```

### 8. Environment & GPU Info
Display installed MinerU versions, CUDA devices, and model paths:
```bash
mineru-convert info
```

---

## 💻 Python Library Usage

You can seamlessly import `mineru_converter` into any Python script or pipeline:

```python
from pathlib import Path
from mineru_converter import MinerUConverter, ConversionConfig, BackendType, ParseMethod

# 1. Configure the conversion pipeline
config = ConversionConfig(
    output_dir=Path("./output/my_docs"),
    backend=BackendType.PIPELINE,
    method=ParseMethod.AUTO,
    formula_enable=True,
    table_enable=True,
    start_page=0,
    end_page=5,  # Process first 6 pages
    generate_manifest=True,
)

# 2. Instantiate converter
converter = MinerUConverter(config=config)

# 3. Execute conversion
result = converter.convert("input/sample.pdf")

# 4. Access output files and stats
if result.success:
    manifest = result.manifest
    stats = result.stats
    
    print(f"Markdown path: {manifest.markdown_path}")
    print(f"Content List V1: {manifest.content_list_v1_path}")
    print(f"Content List V2: {manifest.content_list_v2_path}")
    print(f"Middle AST JSON: {manifest.middle_json_path}")
    print(f"Layout PDF: {manifest.layout_pdf_path}")
    print(f"Images extracted: {len(manifest.image_files)}")
    print(f"Total Words: {stats.word_count:,}")
    print(f"Total Tables: {stats.table_count}")
    print(f"Total Formulas: {stats.formula_count}")
else:
    print(f"Conversion failed: {result.error_message}")
```

### Batch Conversion via Python:
```python
pdf_files = ["doc1.pdf", "doc2.pdf", "doc3.pdf"]
summary = converter.convert_batch(pdf_files, stop_on_error=False)

print(f"Batch completed: {summary.succeeded_count}/{summary.total_documents} succeeded")
print(f"Total duration: {summary.total_duration_seconds:.2f}s")
```

---

## 📖 Output Artifact Reference

When converting `MyDocument.pdf`, the output folder contains:

| Artifact | File Name | Description |
|---|---|---|
| **Raw Markdown** | `MyDocument.md` | Standard MinerU Markdown with equations formatted in LaTeX (`$...$`, `$$...$$`) and image references. |
| **Clean Markdown** | `MyDocument_clean.md` | Hierarchy-corrected Markdown with normalized heading levels (`#`, `##`, `###`) and filtered noise (headers/footers/images stripped). |
| **Redacted Markdown** | `MyDocument_redacted.md` | PII-redacted Markdown produced via Microsoft Presidio, with sensitive entities masked 1:1 with asterisks (`*`). |
| **Content List V1** | `MyDocument_content_list.json` | Stream of page items (text, table, equation, image) compatible with MinerU v1 integrations. |
| **Content List V2** | `MyDocument_content_list_v2.json` | Original MinerU structured stream with bounding boxes, font attributes, table cells, and caption relationships. |
| **Corrected Content List V2** | `MyDocument_content_list_v2_corrected.json` | Duplicate V2 JSON structure with `"level"` values corrected according to dominant PDF font sizes. |
| **Middle AST** | `MyDocument_middle.json` | The intermediate document AST produced by MinerU layout analysis, containing block positions, spans, and raw OCR text. |
| **Model Output** | `MyDocument_model.json` | Raw output from layout detection models (YOLO/PP-DocLayout, Unimernet, etc.). |
| **Layout Visual** | `MyDocument_layout.pdf` | Visual PDF with highlighted bounding boxes for layout blocks (Title, Text, Table, Figure). |
| **Span Visual** | `MyDocument_span.pdf` | Visual PDF highlighting individual inline text token spans and characters. |
| **Origin PDF** | `MyDocument_origin.pdf` | Normalized original document slice for the selected page range. |
| **Images** | `images/` | Directory containing all extracted crop images, diagrams, photos, formula renderings, and table snapshots. |
| **Manifest** | `conversion_manifest.json` | Index of all generated files with SHA256 checksums, byte sizes, and timestamps. |
| **Summary** | `conversion_summary.json` | High-level metrics: character count, word count, headings, tables, formulas, and duration. |

---

## ⚙️ CLI Options Reference

### `mineru-convert convert [OPTIONS] INPUT_PATH`

| Flag | Short | Default | Description |
|---|---|---|---|
| `--output` | `-o` | `./output/mineru` | Output directory root |
| `--backend` | `-b` | `pipeline` | Backend engine (`pipeline`, `hybrid-engine`, `vlm-engine`, `hybrid-http-client`, `vlm-http-client`) |
| `--method` | `-m` | `auto` | Parsing mode (`auto`, `txt`, `ocr`) |
| `--lang` | `-l` | `ch` | OCR language (`ch`, `en`, `korean`, `arabic`, `devanagari`, etc.) |
| `--effort` | | `medium` | Hybrid effort level (`medium`, `high`) |
| `--start-page` | `-s` | `0` | Start page index (0-indexed) |
| `--end-page` | `-e` | `None` | End page index (0-indexed, inclusive) |
| `--no-formula` | | `False` | Disable formula extraction |
| `--no-table` | | `False` | Disable table extraction |
| `--no-images` | | `False` | Skip saving extracted images to disk and strip images from markdown |
| `--no-redact` | | `False` | Skip PII redaction with Microsoft Presidio |
| `--no-layout-pdf` | | `False` | Skip generating `_layout.pdf` |
| `--no-span-pdf` | | `False` | Skip generating `_span.pdf` |
| `--no-manifest` | | `False` | Skip generating `conversion_manifest.json` |
| `--server-url` | `-u` | `None` | Endpoint for remote HTTP client backends |
| `--verbose` | `-v` | `False` | Enable debug logs |
| `--quiet` | `-q` | `False` | Suppress non-error console output |

---

## ☁️ Remote VM Deployment Setup

To deploy on a fresh remote Linux/Ubuntu VM (GPU or CPU) without missing dependencies, use the automated setup script [`setup_vm.sh`](file:///c:/Users/Muhammad.Rafih/Code/mineru-parser/setup_vm.sh):

```bash
chmod +x setup_vm.sh
./setup_vm.sh
```

This script:
1. Installs OS libraries (`libgl1`, `libglib2.0-0`, `poppler-utils`, `ffmpeg`, `build-essential`, `python3-dev`).
2. Creates and activates a Python virtual environment (`.venv`).
3. Detects NVIDIA GPU via `nvidia-smi` and installs matching CUDA PyTorch (or CPU PyTorch).
4. Installs `mineru-converter` and all dependencies (`pymupdf`, `presidio-analyzer`, `presidio-anonymizer`, `spacy`, `streamlit`, `pytest`).
5. Downloads the spaCy English model (`en_core_web_sm`) required for Presidio PII analysis.
6. Pre-downloads all MinerU pipeline model weights (`mineru-models-download -s huggingface -m pipeline`).
7. Verifies MinerU CLI, PyMuPDF font extraction, and Presidio PII redaction.

---

## 🧪 Running Tests

Run the test suite:
```bash
pytest tests/ -v
```


