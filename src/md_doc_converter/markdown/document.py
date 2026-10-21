"""Parsed link handling for private renderer inputs, using the renderer's reader."""

from __future__ import annotations

import copy
import hashlib
import html
import json
import re
from collections.abc import Callable
from html.parser import HTMLParser

from typing_extensions import override

from md_doc_converter import pandoc as _pandoc
from md_doc_converter.markdown.headings import Heading, collect_headings
from md_doc_converter.resources import asset_path

READER = "markdown-implicit_figures-yaml_metadata_block+gfm_auto_identifiers"
TargetTransform = Callable[[str, bool], str]


class MarkdownError(RuntimeError):
    pass


class _HTMLTargets(HTMLParser):
    """Edit only URL attribute values, retaining structural HTML verbatim."""

    def __init__(self, text: str, transform: TargetTransform) -> None:
        super().__init__(convert_charrefs=False)
        self.text = text
        self.transform = transform
        self.edits: list[tuple[int, int, str]] = []
        self.literal_tags: list[str] = []
        self.line_offsets = [0]
        for line in text.splitlines(keepends=True):
            self.line_offsets.append(self.line_offsets[-1] + len(line))

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"pre", "code", "script", "style", "textarea", "xmp"}:
            self.literal_tags.append(tag)
        if self.literal_tags or tag not in {"a", "img"}:
            return
        raw = self.get_starttag_text()
        if raw is None:
            return
        line, column = self.getpos()
        start = self.line_offsets[line - 1] + column
        attribute = "src" if tag == "img" else "href"
        for match in re.finditer(r"""([^\s=<>/]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", raw):
            if match.group(1).lower() != attribute:
                continue
            value = match.group(2)
            target = html.unescape(value[1:-1] if value.startswith(("'", '"')) else value)
            replacement = self.transform(target, tag == "img")
            if replacement != target:
                self.edits.append(
                    (
                        start + match.start(2),
                        start + match.end(2),
                        f'"{html.escape(replacement, quote=True)}"',
                    )
                )

    @override
    def handle_endtag(self, tag: str) -> None:
        if self.literal_tags and self.literal_tags[-1] == tag:
            self.literal_tags.pop()

    def rewritten(self) -> str:
        self.feed(self.text)
        self.close()
        result = self.text
        for start, end, replacement in reversed(self.edits):
            result = result[:start] + replacement + result[end:]
        return result


def strip_column_widths(value: object) -> None:
    """Give every table column in Pandoc JSON *value* the default width.

    Pandoc's reader gives a pipe table column widths when one of its lines is longer than 72
    characters. Rendering never uses them (see assets/strip-colwidths.lua), but the Markdown
    writer can keep a table with widths only as a multiline table, which reads back with
    every column left-aligned.
    """
    if isinstance(value, list):
        for item in value:
            strip_column_widths(item)
    elif isinstance(value, dict):
        content = value.get("c")
        if value.get("t") == "Table" and isinstance(content, list) and len(content) == 6:
            for spec in content[2] if isinstance(content[2], list) else []:
                if isinstance(spec, list) and len(spec) == 2:
                    spec[1] = {"t": "ColWidthDefault"}
        for child in value.values():
            strip_column_widths(child)


def _transform_targets(value: object, transform: TargetTransform, *, raw_html: bool = True) -> bool:
    changed = False
    if isinstance(value, list):
        for item in value:
            changed = _transform_targets(item, transform, raw_html=raw_html) or changed
    elif isinstance(value, dict):
        tag, content = value.get("t"), value.get("c")
        if tag in {"Code", "CodeBlock"}:
            return False
        if tag in {"Link", "Image"}:
            if not isinstance(content, list) or len(content) != 3:
                raise MarkdownError(f"pandoc JSON contained a malformed {tag}")
            target = content[2]
            if not isinstance(target, list) or len(target) != 2 or not isinstance(target[0], str):
                raise MarkdownError(f"pandoc JSON contained a malformed {tag} target")
            replacement = transform(target[0], tag == "Image")
            changed = replacement != target[0]
            target[0] = replacement
        elif raw_html and tag in {"RawInline", "RawBlock"}:
            if (
                isinstance(content, list)
                and len(content) == 2
                and content[0] == "html"
                and isinstance(content[1], str)
            ):
                replacement = _HTMLTargets(content[1], transform).rewritten()
                changed = replacement != content[1]
                content[1] = replacement
        for child in value.values():
            changed = _transform_targets(child, transform, raw_html=raw_html) or changed
    return changed


class MarkdownDocument:
    """Parse once, transform typed targets, and serialize only changed inputs."""

    def __init__(self, markdown: str, *, source_path: str = "", pandoc: str = "pandoc") -> None:
        self.pandoc = pandoc
        self._markdown = markdown
        self.changed = False
        self.note_prefix = (
            "md-doc-converter-" + hashlib.sha256(source_path.encode()).hexdigest() + "-"
        )
        try:
            parsed = _pandoc.parse_markdown(
                markdown,
                reader=READER,
                pandoc=pandoc,
                description="preparing Markdown",
                emit_warnings=True,
            )
        except _pandoc.PandocError as error:
            raise MarkdownError(str(error)) from error
        self.ast = parsed.ast

    def _run(self, text: str, *arguments: str) -> str:
        try:
            return _pandoc.run(
                text,
                *arguments,
                pandoc=self.pandoc,
                description="preparing Markdown",
                emit_warnings=True,
            )
        except _pandoc.PandocError as error:
            raise MarkdownError(str(error)) from error

    @property
    def headings(self) -> list[Heading]:
        blocks = self.ast["blocks"]
        if not isinstance(blocks, list):
            raise MarkdownError("pandoc JSON did not include a blocks list")
        return collect_headings(blocks)

    def transform_targets(self, transform: TargetTransform) -> None:
        self.changed = _transform_targets(self.ast, transform) or self.changed

    def render(self, *, normalize_references: bool = False) -> str:
        if not self.changed and not normalize_references:
            return self._markdown
        ast = copy.deepcopy(self.ast)
        strip_column_widths(ast)
        # Pandoc's Markdown writer leaves entity-like URL text unescaped.
        _transform_targets(
            ast, lambda target, _image: html.escape(target, quote=False), raw_html=False
        )
        self._markdown = self._run(
            json.dumps(ast),
            "--from=json",
            f"--to={READER}-simple_tables-raw_attribute",
            "--standalone",
            "--lua-filter",
            str(asset_path("reference-notes.lua")),
            "--metadata",
            f"md-doc-converter-note-prefix:{self.note_prefix}",
            "--wrap=none",
            "--markdown-headings=atx",
        )
        self.changed = False
        return self._markdown
