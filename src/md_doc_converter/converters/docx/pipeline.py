"""Convert a Word document: Pandoc reads a prepared copy, and the transforms rewrite its tree."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from md_doc_converter.converters.docx import bookmarks as _bookmarks
from md_doc_converter.converters.docx import diagnostics as _diagnostics
from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import navigation as _navigation
from md_doc_converter.converters.docx import normalization as _normalization
from md_doc_converter.converters.docx import package as _package
from md_doc_converter.converters.docx import prepare as _prepare
from md_doc_converter.converters.docx import publication as _publication
from md_doc_converter.converters.docx import tables as _tables
from md_doc_converter.converters.docx import transforms as _transforms
from md_doc_converter.converters.docx import tree as _tree
from md_doc_converter.converters.docx import validation as _validation
from md_doc_converter.converters.docx.diagnostics import TransformReport
from md_doc_converter.converters.docx.models import (
    ConversionError,
    ConversionPaths,
    DocumentNavigation,
    HeadingChange,
    WordPackage,
)

_NEEDS_PANDOC = "Word conversion needs Pandoc, which is not installed; run md-doc-converter setup"


def _tree_markdown(
    paths: ConversionPaths,
    work: Path,
    package: WordPackage,
    pandoc: str,
    stager: _media.ImageStager,
    report: TransformReport,
    math: _tree.MathSyntax,
) -> tuple[str, DocumentNavigation, str | None, dict[str, int]]:
    """The Markdown of the prepared document as Pandoc reads it, with each transform applied."""
    prepared = _prepare.prepare_document(package, paths, work, report)
    tree = _tree.read_docx(prepared.path, work / "pandoc-media", pandoc)
    blocks, api = _tree.items(tree["blocks"]), tree["pandoc-api-version"]
    resolved: dict[str, str] = {}
    with _diagnostics.transform(report, "bookmarks"):
        resolved = _bookmarks.read_probe(blocks, prepared.probe, prepared.bookmarks)
    _transforms.unwrap_containers(blocks)
    _transforms.single_lines(blocks, report)
    _transforms.tidy_line_breaks(blocks)
    _transforms.short_rules(blocks)
    _transforms.merge_code(blocks)
    images = _transforms.ImageCounts()
    with _diagnostics.transform(report, "images"):
        images = _transforms.publish_images(
            blocks, prepared, stager, paths, package.media_folder, report
        )
    _transforms.tidy_links(blocks)
    navigation = DocumentNavigation()
    with _diagnostics.transform(report, "bookmarks"):
        navigation = _bookmarks.collect_navigation(blocks, resolved, report)
    report.applied("links", count=sum(found["t"] == "Link" for found in _tree.walk(blocks)))
    _transforms.drop_empty_paragraphs(blocks)
    with _diagnostics.transform(report, "lists"):
        _transforms.tidy_lists(blocks, report)
    with _diagnostics.transform(report, "code"):
        _transforms.fence_code_blocks(blocks, report)
    _transforms.unnest_emphasis(blocks)
    _transforms.html_emphasis(blocks)
    _transforms.escape_rules_and_underlines(blocks)
    _transforms.verify_emphasis(blocks, pandoc, api)
    notes = _transforms.extract_notes(blocks, report)
    tables = _tables.TableCounts()
    with _diagnostics.transform(report, "tables"):
        tables = _tables.render_tables([blocks, *notes.contents], pandoc, api, math, report)
    _transforms.define_notes(blocks, notes, pandoc, api, math)
    markdown = _tree.write(tree, pandoc, _tree.gfm_writer(math), "Writing Markdown")
    markdown = tables.restore(notes.references(markdown))
    stats = {
        "tables": tables.tables,
        "html_tables": tables.html_tables,
        "multiline_tables": tables.multiline_tables,
        "image_occurrences": images.occurrences,
        "unresolved_alt_text": images.unresolved_alt_text,
    }
    return markdown, navigation, prepared.title, stats


def _convert_in_work(
    paths: ConversionPaths,
    work: Path,
    *,
    qualify_duplicates: bool,
    dry_run: bool,
    report: TransformReport,
    math: _tree.MathSyntax = "github",
) -> dict[str, int]:
    findings = report.findings
    stats: dict[str, int] = {}
    changes: list[HeadingChange] = []
    published = False
    navigation: DocumentNavigation | None = None
    try:
        package = _package.read_package(paths)
        pandoc = shutil.which("pandoc")
        if pandoc is None:
            raise ConversionError(_NEEDS_PANDOC)
        stager = _media.ImageStager(work, package)
        markdown, navigation, title, stats = _tree_markdown(
            paths, work, package, pandoc, stager, report, math
        )
        with _diagnostics.transform(report, "cleanup"):
            # Pandoc pads ordered list markers to four columns; Markdown linters expect one space.
            markdown, _spacing = _normalization.normalize_ordered_list_spacing(markdown)
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
        with _diagnostics.transform(report, "pages"):
            pages = _navigation.split_markdown_pages(
                markdown,
                paths.output,
                paths.split_depth,
                title,
                pandoc,
                source=navigation,
                report=report,
                math=math,
            )
        with _diagnostics.transform(report, "cleanup"):
            for page, content in list(pages.items()):
                pages[page], removed = _normalization.drop_unneeded_escapes(content, pandoc)
                report.applied("cleanup", "unneeded escape(s) removed", removed)
        with _diagnostics.transform(report, "checks"):
            issues = _validation.validate_outputs(
                pages,
                paths,
                stager.root,
                pandoc,
                navigation,
            )
            for reason, detail in issues:
                report.skipped("checks", reason, detail)
            report.complete("checks")
        stats.update(
            {
                "qualified_headings": len(changes),
                "bookmarks": len(navigation.bookmarks),
                "unique_images": stager.count,
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
            work, paths, findings, stats, changes, "validated", source=navigation, report=report
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
            source=navigation,
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
                source=navigation,
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
    math: _tree.MathSyntax = "github",
) -> dict[str, int]:
    """Convert *input_path*; *report* records which layers were applied and why others were not."""
    report = TransformReport() if report is None else report
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
            math=math,
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
            math=math,
        )
