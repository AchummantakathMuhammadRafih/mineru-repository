"""Core MinerU conversion engine orchestrator."""

import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from mineru.cli.common import do_parse, MakeMode

from mineru_converter.config.settings import ConversionConfig
from mineru_converter.core.validator import validate_pdf_document, PDFValidationError
from mineru_converter.exporters.manifest import (
    build_output_manifest,
    calculate_document_stats,
    write_manifest_and_summary,
)
from mineru_converter.exporters.postprocessor import (
    create_standard_aliases,
    normalize_markdown_headers,
    strip_markdown_images,
)
from mineru_converter.models.enums import BackendType, ParseMethod
from mineru_converter.models.schemas import BatchSummary, ConversionResult, OutputFileManifest
from mineru_converter.utils.file_utils import ensure_directory, sanitize_filename
from mineru_converter.utils.logging_utils import get_logger

logger = get_logger()


class MinerUConverter:
    """
    High-level orchestrator for converting PDF and document files using MinerU.
    Produces full artifact suite: Markdown, V1 List, V2 List, Middle JSON,
    Model Output, Visual PDFs, extracted images, and manifest.
    """

    def __init__(self, config: Optional[ConversionConfig] = None):
        """
        Initialize the converter with the given configuration.
        
        Args:
            config: Conversion settings. If None, uses default settings.
        """
        self.config = config or ConversionConfig()

    def convert(
        self,
        input_path: Union[str, Path],
        override_config: Optional[ConversionConfig] = None
    ) -> ConversionResult:
        """
        Convert a single PDF file into all MinerU artifact formats.
        
        Args:
            input_path: Path to the target PDF file.
            override_config: Optional configuration to override instance config.
            
        Returns:
            ConversionResult containing manifest, statistics, status, and duration.
        """
        cfg = override_config or self.config
        pdf_file = Path(input_path).resolve()
        doc_stem = sanitize_filename(pdf_file.stem)
        start_time = time.time()
        start_dt = datetime.utcnow()

        logger.info(f"Starting conversion for [bold cyan]{pdf_file.name}[/bold cyan]")
        logger.debug(f"Configuration: backend={cfg.backend}, method={cfg.method}, lang={cfg.lang}")

        # Ensure output directory
        target_root_dir = ensure_directory(cfg.output_dir)

        # 1. Validate PDF & resolve page range
        try:
            total_pages, eff_start, eff_end = validate_pdf_document(
                pdf_path=pdf_file,
                start_page=cfg.start_page,
                end_page=cfg.end_page
            )
            processed_pages = (eff_end - eff_start) + 1
            logger.info(
                f"Document pages: total={total_pages}, converting range: [{eff_start}..{eff_end}] ({processed_pages} pages)"
            )
        except PDFValidationError as val_err:
            logger.error(f"Validation failed for {pdf_file.name}: {val_err}")
            empty_manifest = OutputFileManifest(
                document_name=doc_stem,
                output_directory=target_root_dir / doc_stem
            )
            return ConversionResult(
                success=False,
                input_path=pdf_file,
                document_name=doc_stem,
                manifest=empty_manifest,
                error_message=str(val_err),
                started_at=start_dt,
                finished_at=datetime.utcnow(),
                duration_seconds=time.time() - start_time,
            )

        # 2. Read PDF bytes
        try:
            with open(pdf_file, "rb") as f:
                pdf_bytes = f.read()
        except Exception as read_err:
            logger.error(f"Failed to read file bytes: {read_err}")
            empty_manifest = OutputFileManifest(
                document_name=doc_stem,
                output_directory=target_root_dir / doc_stem
            )
            return ConversionResult(
                success=False,
                input_path=pdf_file,
                document_name=doc_stem,
                manifest=empty_manifest,
                error_message=f"File read error: {read_err}",
                started_at=start_dt,
                finished_at=datetime.utcnow(),
                duration_seconds=time.time() - start_time,
            )

        # 3. Execute MinerU parser
        try:
            do_parse(
                output_dir=str(target_root_dir),
                pdf_file_names=[doc_stem],
                pdf_bytes_list=[pdf_bytes],
                p_lang_list=[cfg.lang],
                backend=str(cfg.backend),
                parse_method=str(cfg.method),
                formula_enable=cfg.formula_enable,
                table_enable=cfg.table_enable,
                server_url=cfg.server_url,
                f_draw_layout_bbox=cfg.draw_layout_bbox,
                f_draw_span_bbox=cfg.draw_span_bbox,
                f_dump_md=cfg.dump_md,
                f_dump_middle_json=cfg.dump_middle_json,
                f_dump_model_output=cfg.dump_model_output,
                f_dump_orig_pdf=cfg.dump_orig_pdf,
                f_dump_content_list=cfg.dump_content_list,
                f_make_md_mode=MakeMode.MM_MD,
                start_page_id=eff_start,
                end_page_id=eff_end,
                image_analysis=cfg.image_analysis,
                client_side_output_generation=cfg.client_side_output_generation,
                effort=str(cfg.effort),
            )
        except Exception as parse_err:
            logger.exception(f"MinerU execution error on {pdf_file.name}: {parse_err}")
            empty_manifest = OutputFileManifest(
                document_name=doc_stem,
                output_directory=target_root_dir / doc_stem
            )
            return ConversionResult(
                success=False,
                input_path=pdf_file,
                document_name=doc_stem,
                manifest=empty_manifest,
                error_message=f"MinerU conversion failed: {parse_err}",
                started_at=start_dt,
                finished_at=datetime.utcnow(),
                duration_seconds=time.time() - start_time,
            )

        # 4. Resolve output directory
        # MinerU puts files into output_dir / doc_stem / method (or similar)
        # Check standard subdirectories
        actual_doc_dir = None
        method_str = str(cfg.method)
        possible_dirs = [
            target_root_dir / doc_stem / method_str,
            target_root_dir / doc_stem / "auto",
            target_root_dir / doc_stem / "vlm",
            target_root_dir / doc_stem / "hybrid",
            target_root_dir / doc_stem / "pipeline",
            target_root_dir / doc_stem,
        ]
        for p_dir in possible_dirs:
            if p_dir.exists() and any(p_dir.iterdir()):
                actual_doc_dir = p_dir
                break

        if actual_doc_dir is None:
            actual_doc_dir = target_root_dir / doc_stem / method_str

        # If image saving is disabled, remove the extracted images folder
        if not cfg.dump_images:
            images_dir = actual_doc_dir / "images"
            if images_dir.exists() and images_dir.is_dir():
                try:
                    shutil.rmtree(images_dir)
                    logger.debug(f"Removed images directory: {images_dir}")
                except Exception as rm_err:
                    logger.warning(f"Could not remove images directory: {rm_err}")

        # 5. Build Artifact Manifest
        manifest = build_output_manifest(
            output_directory=actual_doc_dir,
            doc_stem=doc_stem
        )

        # 6. Normalize Markdown headers if requested (elevate first header to top H1 level)
        if cfg.capture_header and manifest.markdown_path and manifest.markdown_path.exists():
            normalize_markdown_headers(manifest.markdown_path)

        # 7. Strip image tags from Markdown if image saving is disabled
        if not cfg.dump_images and manifest.markdown_path and manifest.markdown_path.exists():
            strip_markdown_images(manifest.markdown_path)

        elapsed = time.time() - start_time
        end_dt = datetime.utcnow()

        # 7. Calculate statistics
        stats = calculate_document_stats(
            manifest=manifest,
            input_file=pdf_file,
            total_pages=total_pages,
            processed_pages=processed_pages,
            duration_seconds=elapsed,
        )

        # 7. Write conversion manifest & summary if requested
        if cfg.generate_manifest:
            write_manifest_and_summary(
                manifest=manifest,
                stats=stats,
                input_file=pdf_file,
                backend=str(cfg.backend),
                method=str(cfg.method)
            )

        # 8. Create v1/v2 alias files if needed
        create_standard_aliases(manifest, create_v_aliases=True)

        logger.info(
            f"Successfully converted [bold green]{pdf_file.name}[/bold green] in {elapsed:.2f}s "
            f"({len(manifest.all_files())} files generated)"
        )

        return ConversionResult(
            success=True,
            input_path=pdf_file,
            document_name=doc_stem,
            manifest=manifest,
            stats=stats,
            started_at=start_dt,
            finished_at=end_dt,
            duration_seconds=elapsed,
        )

    def convert_batch(
        self,
        input_paths: List[Union[str, Path]],
        override_config: Optional[ConversionConfig] = None,
        stop_on_error: bool = False
    ) -> BatchSummary:
        """
        Convert a batch of PDF documents.
        
        Args:
            input_paths: List of file paths to convert.
            override_config: Optional configuration overrides.
            stop_on_error: If True, halts immediately on first conversion error.
            
        Returns:
            BatchSummary with aggregated results.
        """
        cfg = override_config or self.config
        start_time = time.time()
        results: List[ConversionResult] = []
        succeeded = 0
        failed = 0

        logger.info(f"Starting batch conversion of {len(input_paths)} documents...")

        for idx, file_path in enumerate(input_paths, 1):
            p = Path(file_path)
            logger.info(f"Processing [{idx}/{len(input_paths)}]: {p.name}")
            
            res = self.convert(p, override_config=cfg)
            results.append(res)
            
            if res.success:
                succeeded += 1
            else:
                failed += 1
                if stop_on_error:
                    logger.warning(f"Batch stopped on error: {res.error_message}")
                    break

        total_duration = time.time() - start_time
        logger.info(
            f"Batch completed: {succeeded}/{len(input_paths)} succeeded, "
            f"{failed} failed in {total_duration:.2f}s"
        )

        return BatchSummary(
            total_documents=len(input_paths),
            succeeded_count=succeeded,
            failed_count=failed,
            results=results,
            total_duration_seconds=total_duration,
            output_directory=Path(cfg.output_dir),
        )
