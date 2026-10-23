"""Typed records and errors shared by the DOCX conversion stages."""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


class ConversionError(RuntimeError):
    """Conversion, validation, or recoverable publication failure."""


class PackageError(ConversionError):
    """The DOCX package cannot be read safely, so no baseline conversion is possible."""


def relationships_part_of(name: str) -> str:
    """The package part that holds the relationships of part *name*."""
    folder, file = posixpath.split(name)
    return posixpath.join(folder, "_rels", file + ".rels")


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
class SourceHeading:
    key: str
    level: int
    in_table: bool
    title: str = ""


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
class DocumentNavigation:
    """The headings, Word bookmarks and links of a converted document, which its pages link."""

    headings: list[SourceHeading] = field(default_factory=list[SourceHeading])
    link_targets: list[str] = field(default_factory=list[str])
    # By Word bookmark name or heading key: the heading that it marks, or of its section.
    bookmark_sections: dict[str, BookmarkSection] = field(
        default_factory=dict[str, BookmarkSection]
    )
    bookmark_targets: dict[str, SectionTarget] = field(default_factory=dict[str, SectionTarget])
    # The names of the Word bookmarks, which no page may declare as an HTML id.
    bookmarks: set[str] = field(default_factory=set[str])

    @property
    def anchors(self) -> dict[str, str]:
        return {name: name for name in self.bookmarks}


@dataclass
class WordPackage:
    """Safely read DOCX package parts; the source file itself is never modified."""

    entries: list[zipfile.ZipInfo]
    parts: dict[str, bytes]
    root: ET.Element
    relationships: ET.Element
    styles: ET.Element
    # The parts that the package relationships name; these defaults are where Word puts them.
    document_part: str = "word/document.xml"
    styles_part: str = "word/styles.xml"
    numbering_part: str = "word/numbering.xml"

    @property
    def relationships_part(self) -> str:
        return relationships_part_of(self.document_part)

    @property
    def media_folder(self) -> str:
        """Where Word keeps the document's images, such as word/media."""
        return posixpath.join(posixpath.dirname(self.document_part), "media")


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
