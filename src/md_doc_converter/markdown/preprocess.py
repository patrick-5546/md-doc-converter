"""
Markdown preprocessor that prepares files for pandoc ingestion.

Per-file text cleanup removes Azure DevOps macros and defuses accidental
bit-notation reference definitions. Pandoc then resolves inline, full, collapsed
and shortcut links/images with the renderer's reader. Targets are checked and
made source-relative before files are combined; reference labels from different
source files cannot collide. Only private prepared Markdown is normalized;
source files are never rewritten.

Warnings cover missing images, empty placeholder data URIs, undefined reference
labels and same-file links without matching headings. Literal code is not
interpreted as links or images.

Additionally, for single-file input only, links to other ``.md`` files
are checked: only directory-collection mode rewrites such links to internal
anchors, so any left in a single-file build are reported on stderr as
unresolvable (or broken, if the target file is missing).

When the input is a directory, all ``.md`` files are collected recursively
and each file is preprocessed individually.  A ``.order`` file in any
directory controls the ordering of its entries (one stem per line).  If a
directory has a sibling ``.md`` file with the same name (e.g.
``topic.md`` next to ``topic/``), that file is used as the title page for
the directory.  Cross-file ``.md`` links are resolved to internal heading
anchors (bare links to the target's first heading, ``#fragment`` links to the
matching heading), and unresolved ones are reported on stderr.
``prepare_markdown(folder, respect_gitignore=True)`` skips files and directories
that git ignores.
"""

import base64
import os
import posixpath
import re
import secrets
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote, urldefrag, urlsplit, urlunsplit

from md_doc_converter import pandoc as _pandoc
from md_doc_converter.download import DownloadError, download
from md_doc_converter.markdown.document import READER as _RENDERER_READER
from md_doc_converter.markdown.document import MarkdownDocument, MarkdownError
from md_doc_converter.markdown.syntax import (
    container_line,
    inline_code_spans,
    markdown_prose_lines,
    transform_outside_code,
)
from md_doc_converter.media_types import IMAGE_TYPES

if TYPE_CHECKING:
    from md_doc_converter.markdown.headings import Heading

# ---------------------------------------------------------------------------

# Transform functions
#
# Text transforms return a str; target transforms edit a parsed MarkdownDocument.
# *source_path* is the path to the input markdown file (used to resolve
# relative paths and to report file locations in warnings); it is None only
# when unknown.  *stats* is a Counter, private to that transform, into which it
# records how much it changed (e.g. ``stats["paths resolved"] += 1``); check-only
# transforms leave it empty. Transforms are registered by phase below.
# ---------------------------------------------------------------------------


def resolve_relative_paths(
    document: MarkdownDocument, source_path: str | None, stats: Counter[str]
) -> None:
    """Resolve local link/image URL paths without changing titles, queries or fragments."""
    if source_path is None:
        return

    source_dir = posixpath.dirname(source_path)
    if not source_dir:
        return

    def _resolve(target: str, _image: bool) -> str:
        url = urlsplit(target)
        if url.scheme or url.netloc or not url.path or url.path.startswith("/"):
            return target
        # Link targets are URL paths. Encode filesystem characters without decoding
        # reserved URL delimiters.
        directory = quote(source_dir, safe="/")
        resolved = posixpath.normpath(posixpath.join(directory, url.path))
        result = urlunsplit(("", "", resolved, url.query, url.fragment))
        if result != target:
            stats["paths resolved"] += 1
        return result

    document.transform_targets(_resolve)


# Pandoc's Markdown reader ends a pipe-table cell at one of these HTML tags and drops the
# rest of the cell (checked with Pandoc 3.12.1); GFM renderers show the whole cell.
_CELL_BLOCK_TAG_RE = re.compile(
    r"</?(address|article|aside|blockquote|body|canvas|caption|center|dd|details|dir|div|dl|"
    r"dt|fieldset|figcaption|figure|footer|form|h[1-6]|head|header|hgroup|hr|html|li|main|"
    r"menu|nav|ol|p|pre|script|section|summary|table|tbody|td|textarea|tfoot|th|thead|title|"
    r"tr|ul)(?=[ \t/>]|$)",
    re.IGNORECASE,
)


def _table_delimiter_row(line: str) -> bool:
    stripped = line.strip()
    return (
        "|" in stripped
        and "-" in stripped
        and all(
            re.fullmatch(r"[ \t]*:?-+:?[ \t]*", cell) for cell in stripped.strip("|").split("|")
        )
    )


def warn_block_html_in_table_cells(text: str, source_path: str | None, stats: Counter[str]) -> str:
    """Warn about a pipe table with a block-level HTML tag, such as <ul> or <p>, in a cell.

    A read-only check. It runs before the other transforms, so its line numbers are the
    source file's. Tags in code are ignored.
    """
    lines = [container_line(line)[1] for line in text.splitlines()]
    prose = markdown_prose_lines(text)
    index = 0
    while index + 1 < len(lines):
        if not (
            index in prose
            and index + 1 in prose
            and "|" in lines[index]
            and _table_delimiter_row(lines[index + 1])
        ):
            index += 1
            continue
        end = index + 2
        while end < len(lines) and end in prose and lines[end].strip():
            end += 1
        tags: list[str] = []
        first: int | None = None
        for row in range(index, end):
            line = lines[row]
            for start, stop in reversed(inline_code_spans(line)):
                line = line[:start] + line[stop:]
            for match in _CELL_BLOCK_TAG_RE.finditer(line):
                first = row if first is None else first
                if match.group(1).casefold() not in tags:
                    tags.append(match.group(1).casefold())
        if first is not None:
            print(
                f"WARNING: HTML {', '.join(f'<{tag}>' for tag in tags)} in a Markdown table "
                f"cell in {source_path} (line {first + 1}; md-to-html drops the rest of such "
                "a cell -- write the cell's lines with <br>, or make the table HTML)",
                file=sys.stderr,
            )
        index = end
    return text


# A fence whose info string has more words after its first, such as ```python title="x.py".
# The first word is its language, unless it begins with "{", Pandoc's attribute syntax.
_LONG_INFO_STRING_RE = re.compile(r"(?:`{3,}|~{3,})[ \t]*[^\s{]\S*[ \t]+\S")
_LONG_INFO_FENCE_RE = re.compile(
    r"(?P<container>(?:[ \t]*(?:>|[-+*]|\d{1,9}[.)]))*[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*"
    r"(?P<info>(?P<language>[^\s{]\S*)[ \t]+\S.*)"
)
# Lines as CommonMark counts them, each with its line ending.
_LINES_RE = re.compile(r"[^\r\n]*(?:\r\n?|\n)|[^\r\n]+$")


def _parsed_blocks(markdown: str, reader: str) -> list[_pandoc.JSONValue]:
    try:
        parsed = _pandoc.parse_markdown(markdown, reader=reader, description="preparing Markdown")
        return parsed.blocks
    except _pandoc.PandocError as error:
        raise MarkdownError(str(error)) from error


def _info_string_lines(node: object) -> set[int]:
    """The lines on which the fences with an info string in Pandoc JSON *node*, read with
    sourcepos, start: those of the code blocks with a class."""
    lines: set[int] = set()
    if isinstance(node, list):
        for child in node:
            lines |= _info_string_lines(child)
    elif isinstance(node, dict) and node.get("t") != "CodeBlock":
        for child in node.values():
            lines |= _info_string_lines(child)
    elif isinstance(node, dict):
        match node.get("c"):
            case [[_, [_, *_], [*attributes]], _]:
                for attribute in attributes:
                    match attribute:
                        case ["data-pos", str(position)] if line := re.match(r"(\d+):", position):
                            lines.add(int(line.group(1)) - 1)
    return lines


def trim_fence_info_strings(text: str, source_path: str | None, stats: Counter[str]) -> str:
    """Read a fence's info string as GitHub does: by its first word, the language.

    Pandoc's Markdown reader reads a fence with more words, such as ```python title="x.py" or
    ```js {1,3}, as the start of inline code, which runs the block's lines together. Such a
    fence keeps only its language. A fence that Pandoc reads, such as
    ```python {.numberLines}, is left as it is, and so is one shown inside a code block.
    """
    if not _LONG_INFO_STRING_RE.search(text):
        return text
    lines = _LINES_RE.findall(text)
    # GitHub's reading of the document says which lines open code blocks.
    openers = _info_string_lines(_parsed_blocks(text, "gfm-yaml_metadata_block+sourcepos"))
    fences = [
        (index, fence)
        for index in sorted(openers)
        if (fence := _LONG_INFO_FENCE_RE.fullmatch(lines[index].rstrip("\r\n")))
    ]
    # One parse says which of them Pandoc reads; each is in a div of its own.
    probe = "".join(
        f"::: probe\n{fence['fence']}{fence['info']}\nx\n{fence['fence']}\n\n:::\n\n"
        for _, fence in fences
    )
    divs = _parsed_blocks(probe, _RENDERER_READER)
    for (index, fence), div in zip(fences, divs, strict=True):
        match div.get("c") if isinstance(div, dict) else None:
            case [_, [dict() as first, *_]] if first.get("t") == "CodeBlock":
                continue
        ending = lines[index][len(lines[index].rstrip("\r\n")) :]
        lines[index] = f"{fence['container']}{fence['fence']}{fence['language']}{ending}"
        stats["fence info strings trimmed"] += 1
    return "".join(lines)


def strip_ado_macros(text: str, source_path: str | None, stats: Counter[str]) -> str:
    """Remove Azure DevOps wiki macro lines such as [[_TOC_]] and [[_TOSP_]]."""
    text, removed = re.subn(r"^\[\[_(?:TOC|TOSP)_\]\]\s*$", "", text, flags=re.MULTILINE)
    if removed:
        stats["macros removed"] += removed
    # Removing a macro line leaves an empty line between the surrounding blanks;
    # collapse the run so we don't introduce MD012 (multiple consecutive blanks).
    return re.sub(r"\n{3,}", "\n\n", text)


def escape_bit_ranges(text: str, source_path: str | None, stats: Counter[str]) -> str:
    r"""Defuse accidental link reference definitions formed by bit notation.

    Hardware docs use ``[N]`` / ``[N:M]`` notation heavily (e.g. ``[31:0]``).
    Pandoc turns *every* ``[N]`` into a reference link if a matching link
    reference definition exists anywhere in the document, and such a definition
    is easily formed by accident: a bullet written ``* [1:0]: FIELD[1:0]`` (a
    literal bit-field description) parses as a definition of label ``1:0`` and
    then linkifies every ``[1:0]`` in the whole document (rendering each as a
    blue link to ``FIELD[1:0]``).

    A link reference definition must *begin a block*, so only the ``[N]`` that
    starts a line, list item, or blockquote and is immediately followed by ``:``
    can create one. Pandoc also reads the text that starts a block-level HTML
    element as Markdown, so ``<td>[1:0]: Mode</td>`` in a raw HTML table defines
    one too, although GFM shows that cell as written. Escaping just those brackets
    defuses the definition:
    ``\[1:0\]`` renders identically to ``[1:0]`` but no longer registers a
    definition, so the many ``[N]`` *uses* -- including any inside code -- render
    as the literal bit notation the author intended and are left untouched.
    """

    def _sub(m: re.Match[str]) -> str:
        stats["bit-range definitions defused"] += 1
        return f"{m.group(1)}\\[{m.group(2)}\\]"

    def _escape_prose(prose: str) -> str:
        # ``[N]:`` at a block start: optional indent, then any blockquote/list
        # markers, then the token immediately before a colon.
        prose = re.sub(
            r"(?m)^([ \t]*(?:(?:>|[-*+]|\d+[.)])[ \t]*)*)\[(\d+(?::\d+)?)\](?=:)",
            _sub,
            prose,
        )
        # ...or right after a block-level HTML start tag whose content Pandoc
        # parses as Markdown; raw-text elements such as <pre> are left alone.
        return re.sub(
            r"(<(?:td|th|li|p|div|dd|dt|caption|blockquote|summary|details|figcaption|figure"
            r"|section|aside|article|header|footer|center)\b[^<>]*>[ \t]*)"
            r"\[(\d+(?::\d+)?)\](?=:)",
            _sub,
            prose,
            flags=re.IGNORECASE,
        )

    return transform_outside_code(text, _escape_prose)


def warn_broken_images(
    document: MarkdownDocument, source_path: str | None, stats: Counter[str]
) -> None:
    """Warn about broken image references (a read-only check; text is unchanged).

    Covers two image failure modes, reporting each once on stderr so they surface
    without failing the build:

    * **Missing local files** -- inline/reference images and ``<img src="path">``
      targets whose decoded file path cannot be found. Remote URLs, ``data:``
      URIs and fragment-only targets are ignored.
    * **Empty placeholder data URIs** -- ``![alt](data:image/...)`` whose base64
      payload is empty or truncated (e.g. ``data:image/x-emf;base64...``, which
      MarkItDown writes in place of an image), which render blank.

    Runs before :func:`resolve_relative_paths`, so local paths are still
    relative to *source_path*'s directory.
    """
    source_dir = Path(source_path).parent if source_path else Path()
    warned_files: set[str] = set()
    warned_placeholders: set[str] = set()

    def _check_missing(raw: str) -> None:
        if source_path is None:
            return
        url = urlsplit(raw)
        if url.scheme or url.netloc or not url.path:
            return
        path = unquote(url.path)
        resolved = os.path.normpath(source_dir / path)
        if resolved not in warned_files and not Path(resolved).is_file():
            warned_files.add(resolved)
            print(
                f"WARNING: missing image '{path}' referenced in {source_path}",
                file=sys.stderr,
            )

    def _check_placeholder(uri: str) -> None:
        _head, sep, data = uri.partition(",")
        if sep and re.fullmatch(r"[A-Za-z0-9+/=\s]{8,}", data.strip()):
            return  # has a real base64 payload
        fmt_match = re.match(r"data:image/([a-z0-9.+-]+)", uri, re.IGNORECASE)
        fmt = fmt_match.group(1).lower() if fmt_match else "?"
        if uri in warned_placeholders:
            return
        warned_placeholders.add(uri)
        print(
            f"WARNING: placeholder image with no data in {source_path}: "
            f"empty/truncated 'image/{fmt}' data URI",
            file=sys.stderr,
        )

    def _check(target: str, image: bool) -> str:
        if image:
            if target.startswith("data:image/"):
                _check_placeholder(target)
            else:
                _check_missing(target)
        return target

    document.transform_targets(_check)


def warn_missing_anchors(
    document: MarkdownDocument, source_path: str | None, stats: Counter[str]
) -> None:
    """Warn about same-file ``[text](#anchor)`` links that match no heading.

    Computes the file's own heading anchors through Pandoc's typed AST using
    the same reader as the renderer, then reports any in-page ``#fragment`` link
    that does not resolve to one.
    """
    anchors = {heading.identifier for heading in document.headings}
    warned: set[str] = set()

    def _check(target: str, image: bool) -> str:
        if not image and target.startswith("#"):
            decoded = unquote(target[1:])
            if decoded and decoded not in anchors and decoded not in warned:
                warned.add(decoded)
                print(
                    f"WARNING: broken anchor '{target}' in {source_path} (no matching heading)",
                    file=sys.stderr,
                )
        return target

    document.transform_targets(_check)


def warn_undefined_references(text: str, source_path: str | None, stats: Counter[str]) -> str:
    """Warn about reference-style links/images with undefined labels (a read-only check).

    Recovers the useful part of markdownlint's MD052 (disabled for these docs)
    without its bit-notation false positives. Flags the reference forms whose
    ``][`` structure is an unambiguous reference intent -- full ``[text][label]``,
    collapsed ``[label][]``, and reference images ``![text][label]`` -- when
    *label* has no matching ``[label]: url`` definition, so the token renders as
    literal text. Each undefined label is reported once on stderr.

    Numeric and bit-range labels (``[1][15:0]``, ``[7:0]``) are skipped: they are
    hardware bit-slice notation, not references, and are why MD052 was disabled.
    Shortcut references ``[label]`` (bare single brackets) are intentionally not
    flagged -- they collide with the many non-link uses of ``[...]`` in these
    docs. Inline links ``[text](url)`` (``](`` not ``][``) are never matched.
    """

    def _norm(label: str) -> str:
        # Reference labels are case-insensitive and whitespace-normalised.
        return re.sub(r"\s+", " ", label.strip()).casefold()

    # Collect defined link reference labels: ``[label]: url``.
    defined_labels: set[str] = set()

    def _collect_definitions(prose: str) -> str:
        defined_labels.update(
            _norm(match.group(1))
            for match in re.finditer(r"(?m)^ {0,3}\[((?:\\.|[^\]\\])+)\]:\s+\S", prose)
        )
        return prose

    transform_outside_code(text, _collect_definitions)

    warned: set[str] = set()

    def _check_prose(prose: str) -> str:
        # Full/collapsed reference links and reference images; the ``][`` is what
        # distinguishes these from inline links ``[text](url)``.
        # A backslash only starts an escape: when it could also match as plain text, every
        # escape after a "[" doubled the work of a failed match.
        pattern = r"(?<!\\)(!?)\[((?:\\[\s\S]|[^\[\]\\]|\[[^\]]*\])*)\]\[((?:\\.|[^\]\\])*)\]"
        for match in re.finditer(pattern, prose):
            first, second = match.group(2), match.group(3)
            # Collapsed form ``[label][]`` uses the first bracket as the label.
            label = second if second.strip() else first
            norm = _norm(label)
            # Skip empty, bit-index/range, and defined labels.
            if not norm or re.fullmatch(r"\d+(?::\d+)?", norm):
                continue
            if norm in defined_labels or norm in warned:
                continue
            warned.add(norm)
            kind = "image" if match.group(1) else "link"
            print(
                f"WARNING: undefined reference {kind} '[{label}]' in {source_path} "
                f"(no matching '[{label}]:' definition; renders as literal text -- "
                f"use an inline link [text](url))",
                file=sys.stderr,
            )
        return prose

    transform_outside_code(text, _check_prose)
    return text


def warn_crossfile_md_links(
    document: MarkdownDocument, source_path: str | None, stats: Counter[str]
) -> None:
    """Warn about links to other ``.md`` files (a single-file-mode check).

    Only directory-collection mode rewrites cross-file ``.md`` links to internal
    anchors (see :func:`_file_link_resolver`). In single-file mode nothing
    rewrites them, so a ``[text](./other.md)`` link survives into the output and
    renders as a dead link to a ``.md`` file pandoc never produced. This
    read-only check (text is unchanged) reports each such link once on stderr so
    they surface without failing the build.

    It is invoked only for single-file input; directory builds already resolve
    and report these links in :func:`_file_link_resolver`, so running it there
    would flag every (valid, about-to-be-rewritten) cross-file link as noise. A
    target whose file is missing on disk (resolved relative to *source_path*'s
    directory) is reported as broken; an existing target is reported as
    unresolvable in single-file output. Remote URLs and fragment-only
    links are ignored, matching the filters in :func:`_file_link_resolver`.

    """
    if source_path is None:
        return

    source_dir = Path(source_path).parent
    warned: set[str] = set()

    def _check(target: str, image: bool) -> str:
        url = urlsplit(target)
        if image or url.scheme or url.netloc or not url.path.lower().endswith(".md"):
            return target
        if target in warned:
            return target
        warned.add(target)
        if (source_dir / unquote(url.path)).is_file():
            print(
                f"WARNING: cross-file link to '{target}' will not resolve in "
                "single-file output; build the containing directory instead",
                file=sys.stderr,
            )
        else:
            print(
                f"WARNING: broken cross-file link to '{target}' (target file not found)",
                file=sys.stderr,
            )
        return target

    document.transform_targets(_check)


def _pandoc_headings(markdown: str, source_path: str | None) -> list["Heading"]:
    """Return actual renderer heading identities for *markdown*."""
    from md_doc_converter.markdown.headings import HeadingError, headings

    try:
        return headings(markdown, reader=_RENDERER_READER)
    except HeadingError as exc:
        location = f" in {source_path}" if source_path else ""
        raise HeadingError(f"failed to collect heading identifiers{location}: {exc}") from exc


@dataclass(frozen=True)
class _CollectedMarkdown:
    path: str
    content: str
    headings: list["Heading"]
    document: MarkdownDocument


# Ordered list of transforms applied to the markdown text.
# Append new transform functions here to extend the pipeline.
TEXT_TRANSFORMS = [
    warn_block_html_in_table_cells,
    trim_fence_info_strings,
    strip_ado_macros,
    escape_bit_ranges,
    warn_undefined_references,
]
TARGET_TRANSFORMS = [warn_broken_images, resolve_relative_paths, warn_missing_anchors]


def _prepare_document(
    text: str,
    source_path: str | None = None,
    stats: dict[str, Counter[str]] | None = None,
    *,
    single_file: bool = False,
    embed: Callable[[MarkdownDocument], None] | None = None,
) -> MarkdownDocument:
    """Run text cleanup and target transforms with one parse per source file.

    *stats*, when provided, is a mapping (created by the caller so it can be
    shared across many files) from transform name to a Counter of the changes
    that transform made; :func:`print_stats` summarises it at end of run.
    """
    if stats is None:
        stats = defaultdict(Counter)
    for transform in TEXT_TRANSFORMS:
        text = transform(text, source_path, stats[transform.__name__])
    document = MarkdownDocument(text, source_path=source_path or "")
    if single_file:
        warn_crossfile_md_links(document, source_path, stats["warn_crossfile_md_links"])
    for target_transform in TARGET_TRANSFORMS:
        target_transform(document, source_path, stats[target_transform.__name__])
    if embed is not None:
        embed(document)
    if not single_file and _defines_references(text):
        # A folder's files are joined as Markdown, where a definition would reach every file.
        document.render(normalize_references=True)
    return document


def _defines_references(text: str) -> bool:
    """Whether *text* defines a link reference or a footnote outside code.

    Definitions are file-scoped, even when their labels are unused locally. The parser
    resolves full, collapsed and shortcut references, not this check.
    """
    references = False

    def _detect_references(prose: str) -> str:
        nonlocal references
        references = references or re.search(r"(?<!\\)(?:\\\\)*\]:", prose) is not None
        return prose

    transform_outside_code(text, _detect_references)
    return references


def preprocess(
    text: str,
    source_path: str | None = None,
    stats: dict[str, Counter[str]] | None = None,
    *,
    single_file: bool = False,
    embed: Callable[[MarkdownDocument], None] | None = None,
) -> dict[str, _pandoc.JSONValue]:
    """The prepared document, as Pandoc JSON; its source is not changed."""
    return _prepare_document(text, source_path, stats, single_file=single_file, embed=embed).ast


def print_stats(stats: dict[str, Counter[str]]) -> None:
    """Print an aggregate summary of transform changes to stderr.

    Only transforms that changed something are listed, in pipeline order.
    """
    lines: list[str] = []
    for transform in (*TEXT_TRANSFORMS, *TARGET_TRANSFORMS):
        counter = stats.get(transform.__name__)
        if not counter:
            continue
        detail = ", ".join(f"{key}: {count}" for key, count in counter.items())
        lines.append(f"  {transform.__name__}: {detail}")
    if lines:
        print("Transform stats:", file=sys.stderr)
        for line in lines:
            print(line, file=sys.stderr)


def in_git_work_tree(directory: Path) -> bool:
    """Return True if git is available and *directory* is inside a work tree."""
    if shutil.which("git") is None:
        return False
    try:
        proc = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _git_ignored(names: list[str], directory: Path) -> set[str]:
    """Return the subset of *names* (entries of *directory*) that git ignores.

    Runs ``git check-ignore`` from *directory*.  It honours .gitignore,
    .git/info/exclude and the global excludes file, and (by consulting the
    index) never reports tracked files, so a tracked file matching an ignore
    rule is still kept.  Returns an empty set on any git error.
    """
    if not names:
        return set()
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            input="\0".join(names),
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    # 0 = one or more paths ignored, 1 = none ignored; anything else is an error.
    if proc.returncode not in (0, 1):
        return set()
    return {name for name in proc.stdout.split("\0") if name}


def collect_directory(
    root: Path,
    stats: dict[str, Counter[str]],
    respect_gitignore: bool = False,
    *,
    embed: Callable[[MarkdownDocument], None] | None = None,
) -> str:
    """Collect all .md files in a directory tree into one document.

    If a ``.order`` file exists in a directory, its lines determine the
    order of entries (each line is a stem — either a ``.md`` file or a
    subdirectory).  Entries not listed in ``.order`` are appended in
    sorted order.  Directories without a ``.order`` file are sorted
    alphabetically.

    Each file's image paths are resolved relative to its own location
    before concatenation.  Cross-file ``.md`` links are resolved to
    internal heading anchors when the target file is part of the
    collection.
    """
    if respect_gitignore and not in_git_work_tree(root):
        print(
            f"WARNING: --respect-gitignore had no effect: '{root}' is not "
            "inside a git working tree (or git is unavailable)",
            file=sys.stderr,
        )
        respect_gitignore = False

    # Cross-file link resolution state, populated after collection by parsing
    # both each file and the combined document through Pandoc. This keeps local
    # and combined anchors aligned to the actual renderer's GFM-compatible IDs,
    # including Unicode, punctuation, explicit attributes and duplicate suffixes.
    file_slug_map: dict[str, str] = {}
    file_anchor_map: dict[str, dict[str, str]] = {}

    def _is_ignored(file_path: Path) -> bool:
        if not respect_gitignore:
            return False
        return file_path.name in _git_ignored([file_path.name], file_path.parent)

    def _ordered_entries(directory: Path) -> list[str]:
        """Return directory entries ordered by .order file if present."""
        order_file = directory / ".order"
        entries = [entry.name for entry in directory.iterdir()]

        if respect_gitignore:
            ignored = _git_ignored(entries, directory)
            entries = [e for e in entries if e not in ignored]

        if order_file.is_file():
            lines = order_file.read_text(encoding="utf-8").splitlines()
            order = [line.strip() for line in lines if line.strip()]
            # Build lookup: stem -> actual entry name. A directory owns its stem
            # so a same-stem title page (topic.md beside topic/) stays with it.
            stem_map: dict[str, str] = {}
            for e in entries:
                stem = e.removesuffix(".md") if e.lower().endswith(".md") else e
                if stem not in stem_map or (directory / e).is_dir():
                    stem_map[stem] = e
            ordered = []
            seen = set()
            for stem in order:
                if stem in stem_map:
                    ordered.append(stem_map[stem])
                    seen.add(stem_map[stem])
            # Append remaining entries not in .order
            ordered.extend(e for e in sorted(entries) if e not in seen)
            return ordered

        return sorted(entries)

    def _read_md(file_path: Path) -> _CollectedMarkdown:
        """Read a markdown file, run all transforms, and record its heading anchors."""
        print(f"  {file_path}", file=sys.stderr)
        content = file_path.read_text(encoding="utf-8")
        document = _prepare_document(content, source_path=str(file_path), stats=stats, embed=embed)
        normalized = os.path.normpath(file_path)
        return _CollectedMarkdown(normalized, document.render(), document.headings, document)

    def _collect(
        directory: Path, title_content: _CollectedMarkdown | None = None
    ) -> list[_CollectedMarkdown]:
        """Recursively collect markdown content from *directory*.

        If *title_content* is provided, it is used as the title page.
        Otherwise, a directory without a ``.order`` file uses its first
        file as the title page; a ``.order`` file keeps its listed order.
        """
        explicit_order = (directory / ".order").is_file()
        parts: list[_CollectedMarkdown] = []

        for entry in _ordered_entries(directory):
            if entry.startswith("."):
                continue
            full_path = directory / entry
            if full_path.is_dir():
                sibling_md = directory / f"{entry}.md"
                sub_title: _CollectedMarkdown | None = None
                if sibling_md.is_file() and not _is_ignored(sibling_md):
                    sub_title = _read_md(sibling_md)
                parts.extend(_collect(full_path, sub_title))
            elif full_path.is_file() and entry.lower().endswith(".md"):
                stem = entry.removesuffix(".md") if entry.lower().endswith(".md") else entry
                if (directory / stem).is_dir():
                    continue
                if title_content is None and not (explicit_order and parts):
                    title_content = _read_md(full_path)
                else:
                    parts.append(_read_md(full_path))

        all_parts: list[_CollectedMarkdown] = []
        if title_content:
            all_parts.append(title_content)
        all_parts.extend(parts)
        return all_parts

    # Top-level: check for sibling .md to use as title page
    sibling_md = root.parent / f"{root.name}.md"
    title_content: _CollectedMarkdown | None = None
    if root.name and sibling_md.is_file() and not _is_ignored(sibling_md):
        title_content = _read_md(sibling_md)

    collected = _collect(root, title_content)
    result = "\n\n".join(item.content for item in collected if item.content)
    combined = _pandoc_headings(result, str(root))
    expected = sum(len(item.headings) for item in collected)
    if len(combined) != expected:
        raise RuntimeError(
            "combined markdown heading count changed during collection "
            f"({len(combined)} parsed, expected {expected})"
        )

    index = 0
    for item in collected:
        item_combined = combined[index : index + len(item.headings)]
        index += len(item.headings)
        file_anchor_map[item.path] = {
            local.identifier: merged.identifier
            for local, merged in zip(item.headings, item_combined, strict=True)
        }
        if item_combined:
            file_slug_map[item.path] = item_combined[0].identifier

    resolve_file = _file_link_resolver(file_slug_map, file_anchor_map)

    def _remap_links(item: _CollectedMarkdown) -> str:
        anchors = file_anchor_map[item.path]

        def _remap(target: str, image: bool) -> str:
            if image:
                return target
            if not target.startswith("#"):
                return resolve_file(target)
            combined_anchor = anchors.get(unquote(target[1:]))
            return f"#{quote(combined_anchor, safe='')}" if combined_anchor is not None else target

        item.document.transform_targets(_remap)
        return item.document.render()

    return "\n\n".join(_remap_links(item) for item in collected if item.content)


def _file_link_resolver(
    file_slug_map: dict[str, str],
    file_anchor_map: dict[str, dict[str, str]],
) -> Callable[[str], str]:
    """Build a target mapper from collected ``.md`` paths to internal heading links.

    When multiple files are combined into a single document, cross-file
    links (e.g. ``[text](./Other-File.md)`` or ``[text](./Other.md#section)``)
    should point to the corresponding heading anchor inside the combined
    document instead of an external file.

    *file_slug_map* maps normalised file paths to the slug of each file's first
    heading (for bare links).  *file_anchor_map* maps each normalised file path
    to ``{file-local slug: combined-document slug}`` for every heading, so a
    ``#fragment`` link is remapped to the anchor the target heading actually
    receives once duplicate slugs across the merged document are suffixed
    (``-1``, ``-2``, ...).

    A ``.md`` link whose target file is not among the collected files, whose
    target has no headings, or whose ``#fragment`` does not match any heading in
    the collected target file is left unchanged and reported once on stderr, so
    broken cross-file links surface without silently guessing a destination.

    """
    warned: set[str] = set()

    def _replace(path: str) -> str:
        url = urlsplit(path)
        file_path, fragment = url.path, url.fragment

        # Only handle local .md files (skip external URLs such as https://.../x.md)
        if url.scheme or url.netloc or not file_path.lower().endswith(".md"):
            return path
        if url.query:
            if path not in warned:
                warned.add(path)
                print(
                    f"WARNING: unresolved cross-file link to '{path}' "
                    "(query parameters cannot be represented by an internal heading link)",
                    file=sys.stderr,
                )
            return path

        normalized = os.path.normpath(unquote(file_path))
        if normalized not in file_anchor_map:
            if normalized not in warned:
                warned.add(normalized)
                print(
                    f"WARNING: unresolved cross-file link to '{file_path}' "
                    "(target not found among collected files)",
                    file=sys.stderr,
                )
            return path

        if fragment:
            # Remap the author's file-local anchor to the combined-document slug.
            combined = file_anchor_map[normalized].get(unquote(fragment))
            if combined is None:
                key = f"{normalized}#{fragment}"
                if key not in warned:
                    warned.add(key)
                    print(
                        f"WARNING: unresolved cross-file link to "
                        f"'{file_path}#{fragment}' (heading not found in target file)",
                        file=sys.stderr,
                    )
                return path
            return f"#{quote(combined, safe='')}"
        # Link to the file's first heading
        slug = file_slug_map.get(normalized)
        if slug is None:
            if normalized not in warned:
                warned.add(normalized)
                print(
                    f"WARNING: unresolved cross-file link to '{file_path}' "
                    "(target file has no headings)",
                    file=sys.stderr,
                )
            return path
        return f"#{quote(slug, safe='')}"

    return _replace


@dataclass(frozen=True)
class PreparedDocument:
    """The document ready for Pandoc, as Pandoc JSON; the image targets in it that were not
    embedded; and the data of those that were."""

    document: dict[str, _pandoc.JSONValue]
    # Each was reported with its reason, so Pandoc, which cannot read them, need not repeat it.
    unembedded: frozenset[str] = frozenset()
    # The base64 data of each embedded image, by the placeholder that stands for it in
    # *document*.
    images: Mapping[str, str] = field(default_factory=dict)

    def embed_images(self, text: str) -> str:
        """*text*, such as the HTML that Pandoc writes, with each image's data in its place."""
        return embed_images(text, self.images)


# Stands for an embedded image's data in the document that Pandoc reads; see ImageEmbedder.
# Pandoc drops an image whose data URI is not base64, so placeholders are: 32 base64 letters.
IMAGE_PLACEHOLDER = re.compile(r"MdcImage[0-9a-f]{16}[0-9]{8}")


def embed_images(text: str, images: Mapping[str, str]) -> str:
    """*text* with each placeholder in *images* replaced by the image data it stands for."""
    if not images:
        return text
    return IMAGE_PLACEHOLDER.sub(lambda match: images.get(match.group(), match.group()), text)


def _git_toplevel(directory: Path) -> Path | None:
    """The root of the Git work tree that holds *directory*, if Git is available."""
    if shutil.which("git") is None:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    top = proc.stdout.strip()
    return Path(top).resolve() if proc.returncode == 0 and top else None


def image_root(source: Path) -> Path:
    """The folder whose image files rendering *source* may embed.

    That is the Git work tree that holds *source*, or else the folder that holds it: a file's
    folder, or a folder's parent, which holds the folder's title page and the images that
    docx-to-md writes beside it.
    """
    top = _git_toplevel(source if source.is_dir() else source.parent)
    return top if top is not None else source.parent.resolve()


class ImageEmbedder:
    """Embeds the image files that documents show as data URIs, if they are inside *root*.

    Rendering runs Pandoc in its sandbox, which reads no file and fetches no URL that a document
    names, so a document from elsewhere cannot copy files from outside *root*, such as ~/.ssh,
    into the HTML or PDF, nor tell its author where it was rendered. With *download*, images
    shown by http(s) URL are downloaded and embedded too. Any other image stays a reference,
    reported once: the HTML loads it when it is opened, and the PDF is printed without it.

    Pandoc reads large data URIs slowly and in a lot of memory (over 5 GB for 30 MB of images),
    so each data URI holds a short placeholder instead of the image's data, one per distinct
    image, unique to this embedder. *images* maps each placeholder to its data, which
    embed_images() puts in the HTML that Pandoc writes.
    """

    def __init__(self, root: Path, *, download: bool = False) -> None:
        self.root = root.resolve()
        self.download = download
        self.unembedded: set[str] = set()
        self.images: dict[str, str] = {}
        self._downloads: dict[str, str] = {}  # Data URIs, by URL without its fragment.
        self._placeholders: dict[str, str] = {}  # By the data they stand for.
        self._prefix = f"MdcImage{secrets.token_hex(8)}"

    def __call__(self, document: MarkdownDocument) -> None:
        document.transform_targets(self._embed)

    def _data_uri(self, kind: str, data: bytes) -> str:
        """A data URI of type *kind* whose placeholder stands for *data*."""
        encoded = base64.b64encode(data).decode("ascii")
        placeholder = self._placeholders.get(encoded)
        if placeholder is None:
            placeholder = f"{self._prefix}{len(self._placeholders):08d}"
            self._placeholders[encoded] = placeholder
            self.images[placeholder] = encoded
        return f"data:{kind};base64,{placeholder}"

    def _left(self, target: str, reason: str | None) -> str:
        """Leave *target* as it is, reporting why, once, unless another check already did."""
        if target not in self.unembedded and reason is not None:
            print(f"WARNING: image {target} is not embedded: {reason}", file=sys.stderr)
        self.unembedded.add(target)
        return target

    def _embed(self, target: str, image: bool) -> str:
        url = urlsplit(target)
        scheme = url.scheme.casefold()
        if not image or scheme == "data" or not (url.path or url.netloc):
            return target
        if url.netloc or scheme not in {"", "file"}:
            if scheme not in {"http", "https"}:
                return self._left(target, "it is neither a local file nor an http(s) URL")
            if not self.download:
                return self._left(target, "rendering downloads images only with --download-images")
            return self._download(target)
        try:
            path = Path(unquote(url.path)).resolve(strict=True)
        except (OSError, RuntimeError):
            return self._left(target, None)  # warn_broken_images names a missing image.
        if not path.is_relative_to(self.root):
            return self._left(target, f"it is outside {self.root}")
        if not path.is_file():
            return self._left(target, "it is not a file")
        kind = IMAGE_TYPES.get(path.suffix.casefold())
        if kind is None:
            return self._left(target, "it is not an image")
        fragment = f"#{url.fragment}" if url.fragment else ""
        return f"{self._data_uri(kind, path.read_bytes())}{fragment}"

    def _download(self, target: str) -> str:
        """Download the image at http(s) URL *target* as a data URI, once per URL."""
        if target in self.unembedded:
            return target
        address, fragment = urldefrag(target)
        if address not in self._downloads:
            try:
                data, kind = download(address)
            except DownloadError as error:
                return self._left(target, str(error))
            if not kind.startswith("image/"):
                return self._left(target, f"the server sent {kind}, not an image")
            self._downloads[address] = self._data_uri(kind, data)
        uri = self._downloads[address]
        return f"{uri}#{fragment}" if fragment else uri


def prepare_markdown(
    source: Path, *, respect_gitignore: bool = False, download_images: bool = False
) -> PreparedDocument:
    """Prepare a Markdown file, or every Markdown file in a folder, for Pandoc.

    Report what changed on stderr. *respect_gitignore* skips what Git ignores in a folder.
    The images in image_root(source) are embedded, and with *download_images*, those shown by
    http(s) URL; see ImageEmbedder.
    """
    # Per-transform change counts, shared across every file processed.
    stats: dict[str, Counter[str]] = defaultdict(Counter)
    embed = ImageEmbedder(image_root(source), download=download_images)
    if source.is_dir():
        # Each file is preprocessed during collection.
        combined = collect_directory(
            source, stats, respect_gitignore=respect_gitignore, embed=embed
        )
        print_stats(stats)
        document = MarkdownDocument(combined).ast
    else:
        text = source.read_text(encoding="utf-8")
        document = preprocess(
            text, source_path=str(source), stats=stats, single_file=True, embed=embed
        )
        print_stats(stats)
    return PreparedDocument(document, frozenset(embed.unembedded), dict(embed.images))
