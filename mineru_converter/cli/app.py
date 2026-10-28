"""Click CLI commands for MinerU PDF conversion."""

import os
import sys
from pathlib import Path
from typing import Optional
import click

from mineru_converter.config.constants import (
    DEFAULT_OUTPUT_DIR,
    SUPPORTED_LANGUAGES,
    BackendType,
    HybridEffort,
    ParseMethod,
)
from mineru_converter.config.settings import ConversionConfig
from mineru_converter.core.converter import MinerUConverter
from mineru_converter.exporters.manifest import build_output_manifest, calculate_document_stats
from mineru_converter.cli.formatters import (
    print_banner,
    print_batch_summary,
    print_conversion_result,
    print_watcher_jobs,
)
from mineru_converter.core.database import JobDatabase
from mineru_converter.core.watcher import BackgroundWatcher
from mineru_converter.utils.file_utils import find_input_files, format_file_size
from mineru_converter.utils.logging_utils import console, setup_logger


@click.group(invoke_without_command=False)
@click.version_option(version="1.0.0", prog_name="mineru-converter")
def cli():
    """MinerU PDF Converter - High-fidelity PDF to Markdown, JSON, and Media toolkit."""
    pass


@cli.command("convert")
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "-o", "--output", "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_OUTPUT_DIR,
    show_default=True,
    help="Target directory for output artifacts."
)
@click.option(
    "-b", "--backend",
    type=click.Choice([b.value for b in BackendType]),
    default=BackendType.PIPELINE.value,
    show_default=True,
    help="MinerU backend parser engine."
)
@click.option(
    "-m", "--method",
    type=click.Choice([m.value for m in ParseMethod]),
    default=ParseMethod.AUTO.value,
    show_default=True,
    help="Parsing mode (auto, txt for fast text extraction, ocr for scans)."
)
@click.option(
    "-l", "--lang",
    type=str,
    default="ch",
    show_default=True,
    help=f"Language code for OCR (e.g. {', '.join(SUPPORTED_LANGUAGES[:6])}, en)."
)
@click.option(
    "--effort",
    type=click.Choice([e.value for e in HybridEffort]),
    default=HybridEffort.MEDIUM.value,
    show_default=True,
    help="Effort level for hybrid backend (medium or high)."
)
@click.option("-s", "--start-page", type=int, default=0, show_default=True, help="Start page index (0-indexed).")
@click.option("-e", "--end-page", type=int, default=None, help="End page index (0-indexed, inclusive).")
@click.option("--no-formula", is_flag=True, default=False, help="Disable formula/LaTeX extraction.")
@click.option("--no-table", is_flag=True, default=False, help="Disable table structure recognition.")
@click.option("--no-images", is_flag=True, default=False, help="Skip saving extracted images to disk under images/ folder.")
@click.option("--no-layout-pdf", is_flag=True, default=False, help="Skip generating annotated layout PDF.")
@click.option("--no-span-pdf", is_flag=True, default=False, help="Skip generating annotated span PDF.")
@click.option("--no-manifest", is_flag=True, default=False, help="Skip writing manifest/summary JSON files.")
@click.option("--no-redact", is_flag=True, default=False, help="Skip PII redaction with Microsoft Presidio.")
@click.option("--capture-header/--no-capture-header", default=True, show_default=True, help="Elevate first heading to top level (#) if needed.")
@click.option("--use-llm-hierarchy", is_flag=True, default=False, help="Use LLM (OpenAI / Azure OpenAI) to semantically correct heading hierarchy instead of font heuristics.")
@click.option("--openai-api-key", type=str, default=None, help="OpenAI API key.")
@click.option("--openai-model", type=str, default=None, help="OpenAI model name (e.g. gpt-4o, gpt-4o-mini).")
@click.option("--openai-base-url", type=str, default=None, help="OpenAI base URL.")
@click.option("--azure-endpoint", type=str, default=None, help="Azure OpenAI endpoint URL.")
@click.option("--azure-deployment", type=str, default=None, help="Azure OpenAI deployment name.")
@click.option("--azure-api-key", type=str, default=None, help="Azure OpenAI API key.")
@click.option("-u", "--server-url", type=str, default=None, help="URL for remote VLM/hybrid server.")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose debug logging.")
@click.option("-q", "--quiet", is_flag=True, default=False, help="Suppress informational messages.")
def convert_cmd(
    input_path: Path,
    output_dir: Path,
    backend: str,
    method: str,
    lang: str,
    effort: str,
    start_page: int,
    end_page: Optional[int],
    no_formula: bool,
    no_table: bool,
    no_images: bool,
    no_layout_pdf: bool,
    no_span_pdf: bool,
    no_manifest: bool,
    no_redact: bool,
    capture_header: bool,
    use_llm_hierarchy: bool,
    openai_api_key: Optional[str],
    openai_model: Optional[str],
    openai_base_url: Optional[str],
    azure_endpoint: Optional[str],
    azure_deployment: Optional[str],
    azure_api_key: Optional[str],
    server_url: Optional[str],
    verbose: bool,
    quiet: bool,
):
    """Convert a single PDF file into Markdown, V1/V2 Content Lists, Middle AST, Model JSON, Visual PDFs, and extracted images."""
    if not quiet:
        print_banner()

    setup_logger(verbose=verbose, quiet=quiet)

    config = ConversionConfig(
        output_dir=output_dir,
        backend=BackendType(backend),
        method=ParseMethod(method),
        lang=lang,
        effort=HybridEffort(effort),
        start_page=start_page,
        end_page=end_page,
        formula_enable=not no_formula,
        table_enable=not no_table,
        dump_images=not no_images,
        draw_layout_bbox=not no_layout_pdf,
        draw_span_bbox=not no_span_pdf,
        generate_manifest=not no_manifest,
        redact_pii=not no_redact,
        capture_header=capture_header,
        use_llm_hierarchy=use_llm_hierarchy,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        openai_base_url=openai_base_url,
        azure_openai_endpoint=azure_endpoint,
        azure_openai_deployment=azure_deployment,
        azure_openai_api_key=azure_api_key,
        server_url=server_url,
    )

    converter = MinerUConverter(config=config)
    result = converter.convert(input_path)

    if not quiet:
        print_conversion_result(result)

    if not result.success:
        sys.exit(1)


@cli.command("batch")
@click.argument("input_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "-o", "--output", "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_OUTPUT_DIR,
    show_default=True,
    help="Target directory for output artifacts."
)
@click.option("-r", "--recursive/--no-recursive", default=True, show_default=True, help="Search directory recursively.")
@click.option(
    "-b", "--backend",
    type=click.Choice([b.value for b in BackendType]),
    default=BackendType.PIPELINE.value,
    show_default=True,
    help="MinerU backend parser engine."
)
@click.option(
    "-m", "--method",
    type=click.Choice([m.value for m in ParseMethod]),
    default=ParseMethod.AUTO.value,
    show_default=True,
    help="Parsing mode (auto, txt, ocr)."
)
@click.option("-l", "--lang", type=str, default="ch", show_default=True, help="Language code for OCR.")
@click.option("--no-images", is_flag=True, default=False, help="Skip saving extracted images to disk under images/ folder.")
@click.option("--capture-header/--no-capture-header", default=True, show_default=True, help="Elevate first heading to top level (#) if needed.")
@click.option("--stop-on-error", is_flag=True, default=False, help="Halt batch conversion on first error.")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose debug logging.")
@click.option("-q", "--quiet", is_flag=True, default=False, help="Suppress informational messages.")
def batch_cmd(
    input_dir: Path,
    output_dir: Path,
    recursive: bool,
    backend: str,
    method: str,
    lang: str,
    no_images: bool,
    capture_header: bool,
    stop_on_error: bool,
    verbose: bool,
    quiet: bool,
):
    """Batch convert all PDF files within a directory."""
    if not quiet:
        print_banner()

    setup_logger(verbose=verbose, quiet=quiet)

    # Discover files
    try:
        files = find_input_files(input_dir, recursive=recursive, only_pdf=True)
    except Exception as exc:
        console.print(f"[bold red]Error discovering files:[/bold red] {exc}")
        sys.exit(1)

    if not files:
        console.print(f"[bold yellow]No PDF files found in:[/bold yellow] {input_dir}")
        return

    console.print(f"Found [bold cyan]{len(files)}[/bold cyan] PDF documents to convert.")

    config = ConversionConfig(
        output_dir=output_dir,
        backend=BackendType(backend),
        method=ParseMethod(method),
        lang=lang,
        dump_images=not no_images,
        capture_header=capture_header,
    )

    converter = MinerUConverter(config=config)
    summary = converter.convert_batch(files, stop_on_error=stop_on_error)

    if not quiet:
        print_batch_summary(summary)

    if summary.failed_count > 0:
        sys.exit(1)


@cli.command("inspect")
@click.argument("target_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
def inspect_cmd(target_dir: Path):
    """Inspect and display generated MinerU artifacts and statistics in a directory."""
    print_banner()

    doc_stem = target_dir.name
    # Handle if pointing to method subfolder
    if target_dir.name in ("auto", "txt", "ocr", "vlm", "hybrid", "pipeline"):
        doc_stem = target_dir.parent.name

    manifest = build_output_manifest(target_dir, doc_stem=doc_stem)

    if not any(manifest.all_files()):
        console.print(f"[bold red]No MinerU output artifacts found in:[/bold red] {target_dir}")
        return

    stats = calculate_document_stats(
        manifest=manifest,
        input_file=Path(doc_stem + ".pdf"),
        total_pages=0,
        processed_pages=0,
        duration_seconds=0.0
    )

    from mineru_converter.models.schemas import ConversionResult
    dummy_result = ConversionResult(
        success=True,
        input_path=Path(doc_stem + ".pdf"),
        document_name=doc_stem,
        manifest=manifest,
        stats=stats,
    )
    print_conversion_result(dummy_result)


@cli.command("listen")
@click.argument("input_dir", type=click.Path(file_okay=False, path_type=Path), default=Path("./input"))
@click.option(
    "-o", "--output", "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_OUTPUT_DIR,
    show_default=True,
    help="Target directory for output artifacts."
)
@click.option(
    "--db-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Custom path for SQLite state database (default: output_dir/mineru_jobs.db)."
)
@click.option(
    "--interval",
    type=float,
    default=3.0,
    show_default=True,
    help="Poll interval in seconds."
)
@click.option(
    "-b", "--backend",
    type=click.Choice([b.value for b in BackendType]),
    default=BackendType.PIPELINE.value,
    show_default=True,
    help="MinerU backend parser engine."
)
@click.option(
    "-m", "--method",
    type=click.Choice([m.value for m in ParseMethod]),
    default=ParseMethod.AUTO.value,
    show_default=True,
    help="Parsing mode (auto, txt, ocr)."
)
@click.option("-l", "--lang", type=str, default="ch", show_default=True, help="Language code for OCR.")
@click.option("--no-images/--images", default=True, show_default=True, help="Skip saving extracted images to disk and strip images from markdown.")
@click.option("--no-redact", is_flag=True, default=False, help="Skip PII redaction with Microsoft Presidio.")
@click.option("--capture-header/--no-capture-header", default=True, show_default=True, help="Elevate first heading to top level (#).")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose debug logging.")
@click.option("-q", "--quiet", is_flag=True, default=False, help="Suppress informational messages.")
def listen_cmd(
    input_dir: Path,
    output_dir: Path,
    db_path: Optional[Path],
    interval: float,
    backend: str,
    method: str,
    lang: str,
    no_images: bool,
    no_redact: bool,
    capture_header: bool,
    verbose: bool,
    quiet: bool,
):
    """Start background watcher listening to input_dir, processing 1 file at a time, persisting to SQLite."""
    if not quiet:
        print_banner()

    setup_logger(verbose=verbose, quiet=quiet)

    config = ConversionConfig(
        output_dir=output_dir,
        backend=BackendType(backend),
        method=ParseMethod(method),
        lang=lang,
        dump_images=not no_images,
        redact_pii=not no_redact,
        capture_header=capture_header,
    )

    watcher = BackgroundWatcher(
        input_dir=input_dir,
        output_dir=output_dir,
        db_path=db_path,
        poll_interval=interval,
        config=config,
    )
    watcher.start()


@cli.command("jobs")
@click.option(
    "--db-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_OUTPUT_DIR / "mineru_jobs.db",
    show_default=True,
    help="Path to SQLite state database."
)
@click.option(
    "--status",
    type=click.Choice(["PENDING", "PROCESSING", "COMPLETED", "FAILED"]),
    default=None,
    help="Filter jobs by status."
)
@click.option(
    "-n", "--limit",
    type=int,
    default=25,
    show_default=True,
    help="Maximum jobs to list."
)
def jobs_cmd(db_path: Path, status: Optional[str], limit: int):
    """View status of background jobs tracked in SQLite."""
    print_banner()
    if not db_path.exists():
        console.print(f"[bold red]SQLite database not found at:[/bold red] {db_path}")
        return

    db = JobDatabase(db_path)
    jobs = db.list_jobs(status=status, limit=limit)
    stats = db.get_stats()
    print_watcher_jobs(jobs, stats, db_path)



@cli.command("info")
def info_cmd():
    """Display environment, MinerU version, GPU/device status, and cached model paths."""
    print_banner()
    import torch
    import mineru

    console.print("[bold cyan]System & MinerU Environment Information:[/bold cyan]")
    console.print(f"* [bold]MinerU Version:[/bold] {mineru.__version__ if hasattr(mineru, '__version__') else 'Installed'}")
    console.print(f"* [bold]Python Executable:[/bold] {sys.executable}")
    console.print(f"* [bold]PyTorch Version:[/bold] {torch.__version__}")
    console.print(f"* [bold]CUDA Available:[/bold] {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        console.print(f"* [bold]GPU Device:[/bold] {torch.cuda.get_device_name(0)}")
        console.print(f"* [bold]Device Count:[/bold] {torch.cuda.device_count()}")
    else:
        console.print("* [bold]Compute Mode:[/bold] CPU")

    home = Path.home()
    console.print(f"* [bold]User Home:[/bold] {home}")
    mineru_json = home / "mineru.json"
    console.print(f"* [bold]mineru.json Config:[/bold] {'[green]Exists[/green]' if mineru_json.exists() else '[yellow]Not Found[/yellow]'}")
    hf_cache = home / ".cache" / "huggingface" / "hub"
    console.print(f"* [bold]HuggingFace Cache:[/bold] {hf_cache} ({'[green]Found[/green]' if hf_cache.exists() else '[yellow]Not Found[/yellow]'})")


@cli.command("server")
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host address.")
@click.option("--port", default=8000, show_default=True, type=int, help="Bind port number.")
@click.option("--reload", is_flag=True, default=False, help="Enable auto-reload for development.")
@click.option("-d", "--detach", is_flag=True, default=False, help="Run server in background detached mode.")
@click.option("--stop", is_flag=True, default=False, help="Stop currently running detached server.")
@click.option("--status", is_flag=True, default=False, help="Check detached server process status.")
@click.option("--log-file", type=click.Path(dir_okay=False, path_type=Path), default=None, help="Custom log file path.")
def server_cmd(host: str, port: int, reload: bool, detach: bool, stop: bool, status: bool, log_file: Optional[Path]):
    """Start the asynchronous MinerU conversion API server with background queue worker & Web UI."""
    from server import DEFAULT_LOG_FILE, run_detached, run_foreground, show_status, stop_server
    import argparse

    if stop:
        stop_server()
        return

    if status:
        show_status()
        return

    target_log = log_file or DEFAULT_LOG_FILE
    args = argparse.Namespace(host=host, port=port, reload=reload)

    if detach:
        run_detached(args, target_log)
    else:
        print_banner()
        run_foreground(args, target_log)


