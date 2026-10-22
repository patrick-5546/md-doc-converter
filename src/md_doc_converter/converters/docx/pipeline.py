"""Convert with MarkItDown, then layer each recoverable Word structure on top of it."""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

from markitdown import MarkItDown, MarkItDownException

from md_doc_converter.converters.docx import diagnostics as _diagnostics
from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import navigation as _navigation
from md_doc_converter.converters.docx import normalization as _normalization
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx import publication as _publication
from md_doc_converter.converters.docx import recovery as _recovery
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import source as _source
from md_doc_converter.converters.docx import validation as _validation
from md_doc_converter.converters.docx.diagnostics import (
    ALIGNED_TRANSFORMS,
    FATAL_ERRORS,
    TransformReport,
)
from md_doc_converter.converters.docx.models import (
    ConversionError,
    ConversionPaths,
    HeadingChange,
    SourceDocument,
    SourceImage,
    WordPackage,
)

_NO_PANDOC = "Pandoc is not installed; see md-doc-converter doctor"


def _markitdown(path: Path, style_map: str | None = None) -> str:
    try:
        converter = MarkItDown(enable_plugins=False)
        return converter.convert_local(path, keep_data_uris=True, style_map=style_map).markdown
    except MarkItDownException as error:
        raise ConversionError(f"MarkItDown conversion failed: {error}") from error


def _aligned_markdown(
    paths: ConversionPaths,
    work: Path,
    package: WordPackage,
    pandoc: str,
    stager: _media.ImageStager,
    report: TransformReport,
) -> tuple[str, SourceDocument, dict[str, int]]:
    """Align MarkItDown and Pandoc through markers in private copies of the document."""
    source, primary = _source.prepare_source(package, paths, work, report)
    converted = _markitdown(primary, _source.CODE_STYLE_MAP)
    markdown = _media.tokenize_data_images(converted, source.prefix, source.data_images)
    (work / "markitdown.md").write_text(markdown, encoding="utf-8")
    with _diagnostics.transform(report, "literal-text"):
        markdown = _recovery.restore_literal_text(markdown, source, pandoc)
        report.applied("literal-text", count=source.literal_count)
    if report.records["literal-text"].failure is not None:
        report.unavailable(("line-breaks",), "literal-text failed; see literal-text")
    else:
        report.applied("line-breaks", count=source.restored_breaks)
        for _break in range(source.heading_breaks):
            report.skipped(
                "line-breaks", "Word line break in a heading; a Markdown heading is one line"
            )
    reference = _reference.run_command(
        [
            pandoc,
            str(work / "reference.docx"),
            "--from=docx",
            "--to=html5",
            "--wrap=none",
            "--track-changes=accept",
            "--strip-comments",
            f"--extract-media={work / 'pandoc-media'}",
        ],
        "Pandoc DOCX structural reference",
    )
    (work / "pandoc.html").write_text(reference, encoding="utf-8")
    markdown, stats = _recovery.recover_source_structure(
        markdown, reference, source, paths, work, report, pandoc, stager
    )
    return markdown, source, stats


def _basic_markdown(
    paths: ConversionPaths,
    work: Path,
    package: WordPackage,
    stager: _media.ImageStager,
    report: TransformReport,
) -> str:
    """MarkItDown alone; its images are published where it put them."""
    source = SourceDocument(package.root, "MDCONVDOCX" + uuid.uuid4().hex.upper())
    report.prefix = source.prefix
    converted = _markitdown(_source.baseline_input(package, paths, work, report))
    markdown = _media.tokenize_data_images(converted, source.prefix, source.data_images)
    (work / "baseline.md").write_text(markdown, encoding="utf-8")
    with _diagnostics.transform(report, "images"):
        return _recovery.resolve_data_images(markdown, source, paths, report, stager)
    return _recovery.strip_markers(markdown, source, report)


def _source_checks(report: TransformReport) -> frozenset[str]:
    """The source comparisons that still hold after the layers that were applied."""
    records = report.records

    def clean(*names: str) -> bool:
        return all(records[name].failure is None for name in names)

    checks: set[str] = set()
    if clean("bookmarks", "pages"):
        checks.add("bookmarks")
    if clean("tables", "lists", "headings") and not records["tables"].skipped:
        checks.add("tables")
    if clean("images") and not report.baseline_images:
        checks.add("images")
    if clean("links", "bookmarks", "pages") and not records["pages"].skipped:
        checks.add("links")
    return frozenset(checks)


def _convert_in_work(
    paths: ConversionPaths,
    work: Path,
    *,
    qualify_duplicates: bool,
    dry_run: bool,
    report: TransformReport | None = None,
) -> dict[str, int]:
    report = TransformReport() if report is None else report
    findings = report.findings
    stats: dict[str, int] = {}
    changes: list[HeadingChange] = []
    published = False
    source: SourceDocument | None = None
    try:
        package = _source.read_package(paths)
        stager = _media.ImageStager(work, package)
        pandoc = shutil.which("pandoc")
        markdown: str | None = None
        if pandoc is None:
            report.unavailable(
                ("alignment", *ALIGNED_TRANSFORMS, "pages", "title", "checks"), _NO_PANDOC
            )
        else:
            attempt = report.child()
            try:
                markdown, source, stats = _aligned_markdown(
                    paths, work, package, pandoc, stager, attempt
                )
            except FATAL_ERRORS:
                raise
            except Exception as error:  # noqa: BLE001 - the MarkItDown baseline remains
                source = None
                report.failed("alignment", error)
                report.unavailable(ALIGNED_TRANSFORMS, "alignment failed; see alignment")
                stager.reset()
            else:
                report.merge(attempt)
                report.prefix = attempt.prefix
                report.complete("alignment")
        if markdown is None:
            markdown = _basic_markdown(paths, work, package, stager, report)
            tables = sum(1 for _table in _ooxml.scoped_iter(package.root, _ooxml.WORD + "tbl"))
            stats = {"tables": tables, "html_tables": 0, "multiline_tables": 0}
        with _diagnostics.transform(report, "cleanup"):
            markdown, stats["list_fixes"] = _normalization.normalize_unordered_lists(markdown)
            report.applied("cleanup", "list marker(s) normalized", stats["list_fixes"])
            markdown, stats["hyperlink_fixes"] = _normalization.repair_word_tooltip_links(markdown)
            report.applied("cleanup", "tooltip link(s) repaired", stats["hyperlink_fixes"])
            markdown, stats["trimmed_lines"] = _normalization.trim_trailing_whitespace(markdown)
            report.applied(
                "cleanup", "line(s) with trailing whitespace trimmed", stats["trimmed_lines"]
            )
        if qualify_duplicates:
            with _diagnostics.transform(report, "duplicate-headings"):
                markdown, changes = _navigation.qualify_duplicate_headings(markdown)
                report.applied("duplicate-headings", count=len(changes))
        else:
            report.not_requested("duplicate-headings", "--qualify-duplicate-headings")
        markdown = _normalization.normalize_final_newline(markdown)
        pages = {Path(paths.output.name): markdown}
        if pandoc is not None:
            title = source.title if source is not None else _source.document_title(package.root)
            with _diagnostics.transform(report, "pages"):
                pages = _navigation.split_markdown_pages(
                    markdown,
                    paths.output,
                    paths.split_depth,
                    title,
                    pandoc,
                    source=source,
                    report=report,
                )
            with _diagnostics.transform(report, "cleanup"):
                for page, content in list(pages.items()):
                    pages[page], removed = _normalization.drop_unneeded_escapes(content, pandoc)
                    report.applied("cleanup", "unneeded escape(s) removed", removed)
            with _diagnostics.transform(report, "checks"):
                shapes: list[dict[str, str]] | None = [] if report.explaining else None
                issues = _validation.validate_outputs(
                    pages,
                    paths,
                    stager.root,
                    pandoc,
                    source,
                    compare=_source_checks(report),
                    shapes=shapes,
                )
                for index, (reason, detail) in enumerate(issues):
                    shape = shapes[index] if shapes is not None else None
                    report.skipped("checks", reason, detail, shape=shape)
                report.complete("checks")
        recovered: list[SourceImage] = (
            [image for image in source.images if image.token in source.recovered_images]
            if source is not None
            else []
        )
        stats.update(
            {
                "qualified_headings": len(changes),
                "bookmarks": len(source.anchors) if source is not None else 0,
                "image_occurrences": len(recovered) + report.baseline_images,
                "unique_images": stager.count,
                "unresolved_alt_text": sum(
                    image.description.alt_text is None for image in recovered
                ),
                "pages": len(pages),
            }
        )
        staged_markdown = work / "final.md"
        staged_markdown.write_text(pages[Path(paths.output.name)], encoding="utf-8")
        staged_pages: Path | None = None
        if paths.split_depth:
            staged_pages = work / "final-pages"
            staged_pages.mkdir()
            for page, content in pages.items():
                if page == Path(paths.output.name):
                    continue
                destination = staged_pages / page.relative_to(paths.output.stem)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content, encoding="utf-8")
        _diagnostics.write_diagnostics(
            work, paths, findings, stats, changes, "validated", source=source, report=report
        )
        if not dry_run:
            _publication.publish_outputs(staged_markdown, stager.root, paths, staged_pages)
            published = True
        _diagnostics.write_diagnostics(
            work,
            paths,
            findings,
            stats,
            changes,
            "dry-run" if dry_run else "published",
            source=source,
            report=report,
        )
        return stats
    except (ConversionError, OSError, UnicodeError) as error:
        detail = (
            f"Outputs were published, but completing diagnostics failed: {error}"
            if published
            else str(error)
        )
        try:
            _diagnostics.write_diagnostics(
                work,
                paths,
                findings,
                stats,
                changes,
                "failed",
                detail,
                source=source,
                report=report,
            )
        except OSError as diagnostic_error:
            raise ConversionError(
                f"{detail}; cannot write diagnostic report at {work / 'diagnostics.json'}: "
                f"{diagnostic_error}"
            ) from error
        if isinstance(error, ConversionError) and not published:
            raise
        raise ConversionError(detail) from error


def _scratch_parent(paths: ConversionPaths) -> Path:
    for candidate in dict.fromkeys((Path(tempfile.gettempdir()), Path("/tmp"), Path("/var/tmp"))):
        parent = candidate.resolve()
        if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
            continue
        if any(
            parent == directory or directory in parent.parents
            for directory in (paths.images, paths.pages)
        ):
            continue
        return parent
    raise ConversionError("No writable scratch location outside the generated destinations")


def convert_docx(
    input_path: Path,
    output_path: Path | None = None,
    *,
    split_depth: int = 0,
    qualify_duplicate_headings: bool = False,
    dry_run: bool = False,
    replace_generated: bool = False,
    keep_intermediates: Path | None = None,
    report: TransformReport | None = None,
) -> dict[str, int]:
    """Convert *input_path*; *report* records which layers were applied and why others were not."""
    paths = _publication.validate_paths(
        input_path,
        output_path,
        split_depth=split_depth,
        replace_generated=replace_generated,
        keep_intermediates=keep_intermediates,
    )
    if paths.diagnostics is not None:
        paths.diagnostics.mkdir(mode=0o700, parents=True, exist_ok=True)
        return _convert_in_work(
            paths,
            paths.diagnostics,
            qualify_duplicates=qualify_duplicate_headings,
            dry_run=dry_run,
            report=report,
        )
    with tempfile.TemporaryDirectory(prefix="docx-to-md-", dir=_scratch_parent(paths)) as temporary:
        work = Path(temporary)
        if any(
            _publication.overlaps(work, destination)
            for destination in (
                paths.source,
                paths.source_entry,
                paths.output,
                paths.images,
                paths.pages,
            )
        ):
            raise ConversionError("Private scratch overlaps a protected conversion path")
        return _convert_in_work(
            paths,
            work,
            qualify_duplicates=qualify_duplicate_headings,
            dry_run=dry_run,
            report=report,
        )
