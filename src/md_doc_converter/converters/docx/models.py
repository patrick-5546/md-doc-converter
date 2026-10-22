"""Typed records and errors shared by the DOCX conversion stages."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET


class ConversionError(RuntimeError):
    """Conversion, validation, or recoverable publication failure."""


class PackageError(ConversionError):
    """The DOCX package cannot be read safely, so no baseline conversion is possible."""


@dataclass(frozen=True)
class ConversionPaths:
    source: Path
    source_entry: Path
    output: Path
    images: Path
    pages: Path
    diagnostics: Path | None
    explicit_output: bool
    split_depth: int
    replace_generated: bool


@dataclass(frozen=True)
class ImageDescription:
    alt_text: str | None
    paragraph_index: int
    from_caption: bool = False
    from_heading: bool = False


@dataclass(frozen=True)
class SourceImage:
    token: str
    package_name: str
    published_name: PurePosixPath
    description: ImageDescription


@dataclass(frozen=True)
class SourceHeading:
    token: str
    level: int
    in_table: bool
    title: str = ""


@dataclass(frozen=True)
class SourceListItem:
    token: str
    in_table: bool
    # Numbered in Word's markup but not a list item, e.g. numId 0; Pandoc adds it to the
    # list item before it, if any.
    continuation: bool = False


@dataclass(frozen=True)
class SourceTable:
    token: str
    source: ET.Element
    nested: bool
    # The paragraphs of *source* that are Word list items.
    listed: frozenset[ET.Element] = frozenset()


@dataclass(frozen=True)
class TableRendering:
    text: str
    markdown: bool
    reason: str | None = None
    omitted: int = 0


@dataclass(frozen=True)
class BookmarkSection:
    heading: str | None
    exact: bool


@dataclass(frozen=True)
class SectionTarget:
    page: Path
    fragment: str = ""


@dataclass
class SourceDocument:
    root: ET.Element
    prefix: str
    tables: list[SourceTable] = field(default_factory=list)
    images: list[SourceImage] = field(default_factory=list)
    headings: list[SourceHeading] = field(default_factory=list)
    list_items: list[SourceListItem] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    link_targets: list[str] = field(default_factory=list)
    anchors: dict[str, str] = field(default_factory=dict)
    paragraph_tokens: list[str] = field(default_factory=list)
    empty_row_tokens: list[str] = field(default_factory=list)
    media: dict[str, bytes] = field(default_factory=dict)
    literal_tokens: dict[str, str] = field(default_factory=dict)
    markdown_tables: set[str] = field(default_factory=set)
    # Headings by outline level that Word also numbers; Pandoc and MarkItDown read list items.
    listed_headings: set[str] = field(default_factory=set)
    bookmark_sections: dict[str, BookmarkSection] = field(default_factory=dict)
    bookmark_targets: dict[str, SectionTarget] = field(default_factory=dict)
    title: str | None = None
    alt_targets: list[tuple[ET.Element, str, str]] = field(default_factory=list)
    data_images: dict[str, tuple[str, bytes]] = field(default_factory=dict)
    recovered_images: set[str] = field(default_factory=set)
    pict_units: list[ET.Element] = field(default_factory=list)
    published_urls: set[str] = field(default_factory=set)
    literal_count: int = 0
    # Code blocks by alignment token, with their Word text, and the character style that
    # marks inline code, if any; recovery replaces each block's region.
    code_blocks: dict[str, str] = field(default_factory=dict)
    code_style: ET.Element | None = None
    # How many Word line breaks recovery restored, and how many in headings became spaces.
    restored_breaks: int = 0
    heading_breaks: int = 0


@dataclass
class WordPackage:
    """Safely read DOCX package parts; the source file itself is never modified."""

    entries: list[zipfile.ZipInfo]
    parts: dict[str, bytes]
    root: ET.Element
    relationships: ET.Element
    styles: ET.Element


@dataclass
class WordCell:
    source: ET.Element | None
    colspan: int = 1
    rowspan: int = 1


@dataclass
class WordRow:
    header: bool
    cells: list[WordCell]


@dataclass
class WikiPage:
    title: str
    source_level: int
    parent: int | None
    relative_path: Path
    lines: list[str] = field(default_factory=list)
    heading_indices: list[int | None] = field(default_factory=list)


@dataclass(frozen=True)
class HeadingChange:
    line_number: int
    original_title: str
    final_title: str
    ancestors: tuple[str, ...]


@dataclass
class PreparedArtifact:
    root: Path
    destination: Path
    # None removes the destination: it is only backed up, and nothing replaces it.
    prepared: Path | None
    backup: Path
    backed_up: bool = False
    published: bool = False


@dataclass
class DocumentReferences:
    anchors: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    headings: list[int] = field(default_factory=list)
    tables: list[ET.Element] = field(default_factory=list)
    sourceless: list[str] = field(default_factory=list)
