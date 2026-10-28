"""Core MinerU conversion engine orchestrator."""

import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Union

from mineru_converter.core.patches import apply_transformers_compat_patches
apply_transformers_compat_patches()

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
from mineru_converter.exporters.hierarchy_corrector import (
    process_content_list_v2_to_clean_markdown,
)
from mineru_converter.exporters.redactor import (
    redact_markdown_file,
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
        override_config: Optional[ConversionConfig] = None,
        step_callback: Optional[Callable[[str], None]] = None,
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

        backend_val = cfg.backend.value if hasattr(cfg.backend, "value") else str(cfg.backend)
        method_val = cfg.method.value if hasattr(cfg.method, "value") else str(cfg.method)
        effort_val = cfg.effort.value if hasattr(cfg.effort, "value") else str(cfg.effort)

        # 3. Execute MinerU parser
        if step_callback:
            step_callback("PARSING")

        parse_start_time = time.time()
        logger.info(
            f"⚙️ [MinerU Parser] Starting parsing engine for '{doc_stem}' "
            f"(backend={backend_val}, method={method_val}, pages={processed_pages}, "
            f"formula={cfg.formula_enable}, table={cfg.table_enable})..."
        )

        try:
            do_parse(
                output_dir=str(target_root_dir),
                pdf_file_names=[doc_stem],
                pdf_bytes_list=[pdf_bytes],
                p_lang_list=[cfg.lang],
                backend=backend_val,
                parse_method=method_val,
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
                effort=effort_val,
            )
            parse_dur = time.time() - parse_start_time
            logger.info(f"✓ [MinerU Parser] Core document parse completed in {parse_dur:.2f}s for '{doc_stem}'.")
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
        method_str = method_val
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

        logger.info(f"📂 [MinerU Parser] Output directory resolved: {actual_doc_dir}")

        # If image saving is disabled, remove the extracted images folder
        if not cfg.dump_images:
            images_dir = actual_doc_dir / "images"
            if images_dir.exists() and images_dir.is_dir():
                try:
                    shutil.rmtree(images_dir)
                    logger.info(f"🖼️ [MinerU Parser] Removed images directory: {images_dir.name}")
                except Exception as rm_err:
                    logger.warning(f"Could not remove images directory: {rm_err}")

        # 5. Build Artifact Manifest
        manifest = build_output_manifest(
            output_directory=actual_doc_dir,
            doc_stem=doc_stem
        )
        logger.info(
            f"📦 [MinerU Parser] Artifacts generated: "
            f"markdown={manifest.markdown_path.name if manifest.markdown_path else 'none'}, "
            f"v1_json={manifest.content_list_v1_path.name if manifest.content_list_v1_path else 'none'}, "
            f"v2_json={manifest.content_list_v2_path.name if manifest.content_list_v2_path else 'none'}"
        )

        # 6. Normalize Markdown headers if requested (elevate first header to top H1 level)
        if cfg.capture_header and manifest.markdown_path and manifest.markdown_path.exists():
            normalize_markdown_headers(manifest.markdown_path)

        # 7. Strip image tags from Markdown if image saving is disabled
        if not cfg.dump_images and manifest.markdown_path and manifest.markdown_path.exists():
            strip_markdown_images(manifest.markdown_path)

        # 8. Generate clean markdown and duplicate corrected V2 list if enabled
        if step_callback:
            step_callback("HIERARCHY_CORRECTION")

        if cfg.generate_clean_markdown and manifest.content_list_v2_path and manifest.content_list_v2_path.exists():
            pdf_source_for_fonts = manifest.origin_pdf_path if (manifest.origin_pdf_path and manifest.origin_pdf_path.exists()) else pdf_file
            try:
                if cfg.use_llm_hierarchy:
                    logger.info(
                        f"🤖 [Hierarchy] Running LLM-based hierarchy correction for {pdf_file.name} "
                        f"(deployment={cfg.azure_openai_deployment or 'default'})..."
                    )
                else:
                    logger.info(
                        f"📐 [Hierarchy] Running font-size heuristic hierarchy correction for {pdf_file.name}..."
                    )

                corr_v2_path, clean_md_path, summary = process_content_list_v2_to_clean_markdown(
                    v2_json_path=manifest.content_list_v2_path,
                    pdf_path=pdf_source_for_fonts,
                    filter_images=True,
                    include_footnotes=False,
                    use_llm=cfg.use_llm_hierarchy,
                    openai_api_key=cfg.openai_api_key,
                    openai_model=cfg.openai_model,
                    openai_base_url=cfg.openai_base_url,
                    azure_endpoint=cfg.azure_openai_endpoint,
                    azure_api_key=cfg.azure_openai_api_key,
                    azure_deployment=cfg.azure_openai_deployment,
                    azure_api_version=cfg.azure_openai_api_version,
                    temperature=cfg.llm_temperature,
                    timeout=cfg.llm_timeout,
                )
                manifest.corrected_content_list_v2_path = corr_v2_path
                manifest.clean_markdown_path = clean_md_path
                logger.info(
                    f"✓ [Hierarchy] Completed for {pdf_file.name} "
                    f"using strategy: {summary.get('method', 'heuristics')} "
                    f"({summary.get('corrected_count', 0)}/{summary.get('total_titles', 0)} headings corrected)"
                )
            except Exception as clean_err:
                logger.warning(f"Could not generate clean markdown for {pdf_file.name}: {clean_err}")

        # 9. Redact clean markdown with Microsoft Presidio (masked with stars) if enabled
        if step_callback:
            step_callback("REDACTION")

        if cfg.redact_pii:
            source_md_for_redaction = (
                manifest.clean_markdown_path
                if (manifest.clean_markdown_path and manifest.clean_markdown_path.exists())
                else manifest.markdown_path
            )
            if source_md_for_redaction and source_md_for_redaction.exists():
                try:
                    logger.info(f"🔒 [Presidio Redaction] Redacting sensitive PII from {source_md_for_redaction.name}...")
                    redact_t0 = time.time()
                    redacted_md_path, redact_summary = redact_markdown_file(
                        input_clean_md_path=source_md_for_redaction,
                        entities=cfg.pii_entities,
                        score_threshold=cfg.pii_score_threshold,
                        language="en",
                    )
                    redact_dur = time.time() - redact_t0
                    manifest.redacted_markdown_path = redacted_md_path
                    logger.info(
                        f"✓ [Presidio Redaction] Redaction finished in {redact_dur:.2f}s: "
                        f"{redact_summary.get('total_redactions', 0)} entity/entities masked -> {redacted_md_path.name}"
                    )
                except Exception as redact_err:
                    logger.warning(f"Could not redact markdown for {pdf_file.name}: {redact_err}")

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
            f"🎉 [MinerU Pipeline] Successfully finished conversion for {pdf_file.name} in {elapsed:.2f}s "
            f"({len(manifest.all_files())} files generated) -> {actual_doc_dir}"
        )

        if step_callback:
            step_callback("COMPLETED")

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
