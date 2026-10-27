"""Rich terminal output formatters, banners, panels, and tables."""

from pathlib import Path
from typing import List, Optional
from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mineru_converter.models.schemas import BatchSummary, ConversionResult, OutputFileManifest
from mineru_converter.utils.file_utils import format_file_size
from mineru_converter.utils.logging_utils import console


def print_banner() -> None:
    """Print a stylish CLI banner."""
    banner_text = Text()
    banner_text.append(" MinerU PDF Conversion Toolkit ", style="bold white on blue")
    banner_text.append(" v1.0.0\n", style="dim white")
    banner_text.append("Modular pipeline for Markdown, Content Lists, Middle AST & Media extraction", style="italic cyan")

    panel = Panel(
        banner_text,
        box=ROUNDED,
        border_style="bright_blue",
        padding=(0, 2),
    )
    console.print(panel)


def print_conversion_result(result: ConversionResult) -> None:
    """Render a beautiful summary table of all generated artifacts and statistics."""
    manifest = result.manifest
    stats = result.stats

    if not result.success:
        err_panel = Panel(
            f"[bold red]Conversion Failed:[/bold red] {result.error_message}",
            title=f"[red]{result.document_name}[/red]",
            border_style="red",
            box=ROUNDED,
        )
        console.print(err_panel)
        return

    # 1. Main Artifacts Table
    table = Table(
        title=f"Generated Artifacts: [bold green]{result.document_name}[/bold green]",
        box=ROUNDED,
        border_style="bright_cyan",
        header_style="bold magenta",
    )
    table.add_column("Artifact Type", style="cyan", no_wrap=True)
    table.add_column("File Name", style="white")
    table.add_column("Size", justify="right", style="green")
    table.add_column("Status", justify="center")

    artifacts = [
        ("Markdown (.md)", manifest.markdown_path),
        ("Content List V1 (.json)", manifest.content_list_v1_path),
        ("Content List V2 (.json)", manifest.content_list_v2_path),
        ("Middle AST (.json)", manifest.middle_json_path),
        ("Model Output (.json)", manifest.model_json_path),
        ("Layout Visual (.pdf)", manifest.layout_pdf_path),
        ("Span Visual (.pdf)", manifest.span_pdf_path),
        ("Origin Slice (.pdf)", manifest.origin_pdf_path),
        ("Manifest (.json)", manifest.manifest_path),
        ("Summary (.json)", manifest.summary_path),
    ]

    for name, path in artifacts:
        if path and path.exists():
            size_str = format_file_size(path.stat().st_size)
            table.add_row(name, path.name, size_str, "[bold green]OK[/bold green]")
        elif path:
            table.add_row(name, path.name, "-", "[yellow]Missing[/yellow]")
        else:
            table.add_row(name, "-", "-", "[dim]Disabled[/dim]")

    # Images info
    if manifest.images_dir and manifest.images_dir.exists():
        img_count = len(manifest.image_files)
        total_img_size = sum(f.stat().st_size for f in manifest.image_files if f.exists())
        table.add_row(
            f"Images Directory ({img_count} items)",
            "images/",
            format_file_size(total_img_size),
            "[bold green]OK[/bold green]" if img_count > 0 else "[dim]Empty[/dim]"
        )

    console.print(table)

    # 2. Document Stats Panel
    if stats:
        stats_text = (
            f"[bold]Pages Processed:[/bold] {stats.processed_pages} / {stats.total_document_pages}   "
            f"[bold]Duration:[/bold] {stats.conversion_duration_seconds:.2f}s   "
            f"[bold]Output Size:[/bold] {format_file_size(stats.output_total_size_bytes)}\n"
            f"[cyan]* Characters:[/cyan] {stats.character_count:,}   "
            f"[cyan]* Words:[/cyan] {stats.word_count:,}   "
            f"[cyan]* Headings:[/cyan] {stats.heading_count}   "
            f"[cyan]* Paragraphs:[/cyan] {stats.paragraph_count}\n"
            f"[yellow]* Tables:[/yellow] {stats.table_count}   "
            f"[yellow]* Formulas:[/yellow] {stats.formula_count}   "
            f"[yellow]* Images Extracted:[/yellow] {stats.image_count}"
        )
        stats_panel = Panel(
            stats_text,
            title="[bold yellow]Document Content Breakdown[/bold yellow]",
            border_style="yellow",
            box=ROUNDED,
        )
        console.print(stats_panel)

    console.print(
        f"[dim]Output Directory: [link=file:///{manifest.output_directory.resolve()}]{manifest.output_directory.resolve()}[/link][/dim]\n"
    )


def print_batch_summary(summary: BatchSummary) -> None:
    """Render summary table for batch conversions."""
    table = Table(
        title="Batch Conversion Summary",
        box=ROUNDED,
        border_style="bright_blue",
        header_style="bold cyan",
    )
    table.add_column("Document", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Pages", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Images", justify="right")
    table.add_column("Output Size", justify="right")

    for res in summary.results:
        if res.success and res.stats:
            status = "[bold green]SUCCESS[/bold green]"
            pages = f"{res.stats.processed_pages}/{res.stats.total_document_pages}"
            dur = f"{res.duration_seconds:.2f}s"
            imgs = str(res.stats.image_count)
            size = format_file_size(res.stats.output_total_size_bytes)
        else:
            status = "[bold red]FAILED[/bold red]"
            pages = "-"
            dur = f"{res.duration_seconds:.2f}s"
            imgs = "-"
            size = "-"

        table.add_row(res.document_name, status, pages, dur, imgs, size)

    console.print(table)

    summary_panel = Panel(
        f"[bold]Total Files:[/bold] {summary.total_documents}  |  "
        f"[bold green]Succeeded:[/bold green] {summary.succeeded_count}  |  "
        f"[bold red]Failed:[/bold red] {summary.failed_count}  |  "
        f"[bold cyan]Success Rate:[/bold cyan] {summary.success_rate:.1f}%  |  "
        f"[bold magenta]Total Time:[/bold magenta] {summary.total_duration_seconds:.2f}s\n"
        f"[dim]Output Root: {summary.output_directory.resolve()}[/dim]",
        title="[bold blue]Batch Results Overview[/bold blue]",
        border_style="blue",
        box=ROUNDED,
    )
    console.print(summary_panel)


def print_watcher_jobs(jobs: list, stats: dict, db_path: Path) -> None:
    """Render table of tracked SQLite jobs."""
    table = Table(
        title=f"Background Worker Job Queue (SQLite: {db_path.name})",
        box=ROUNDED,
        border_style="bright_blue",
        header_style="bold cyan",
    )
    table.add_column("ID", justify="right", style="cyan")
    table.add_column("File Name", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Duration", justify="right")
    table.add_column("Markdown Output", style="green")
    table.add_column("Error", style="red")

    for j in jobs:
        st = j["status"]
        if st == "COMPLETED":
            st_fmt = "[bold green]COMPLETED[/bold green]"
        elif st == "PROCESSING":
            st_fmt = "[bold yellow]PROCESSING[/bold yellow]"
        elif st == "FAILED":
            st_fmt = "[bold red]FAILED[/bold red]"
        else:
            st_fmt = "[dim]PENDING[/dim]"

        dur = f"{j['duration_seconds']:.2f}s" if j.get("duration_seconds") else "-"
        md = Path(j["markdown_file"]).name if j.get("markdown_file") else "-"
        err = (j.get("error_message") or "-")[:35]

        table.add_row(str(j["id"]), j["file_name"], st_fmt, dur, md, err)

    console.print(table)

    stats_str = " | ".join(f"[bold]{k}:[/bold] {v}" for k, v in stats.items())
    console.print(Panel(stats_str, title="[bold]Queue Summary[/bold]", border_style="blue", box=ROUNDED))
