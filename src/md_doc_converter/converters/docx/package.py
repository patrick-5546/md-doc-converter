"""Read a Word package safely, every part once; the source document is never changed."""

from __future__ import annotations

import re
import stat
import zipfile
import zlib
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx.models import (
    ConversionPaths,
    PackageError,
    WordPackage,
    relationships_part_of,
)
from md_doc_converter.converters.docx.ooxml import PACKAGE_REL, WORD
from md_doc_converter.converters.packages import is_compound_file, too_large

# Strict Office Open XML names the elements and relationships of the Transitional form, which
# Word writes by default, in its own namespaces; Transitional allows every Strict value.
_STRICT = re.compile(
    rb"http://purl\.oclc\.org/ooxml/(wordprocessingml|drawingml|officeDocument|schemaLibrary)/"
)


def _transitional(parts: dict[str, bytes]) -> dict[str, bytes]:
    """The parts of a Strict package in the Transitional form, which the conversion reads."""
    return {
        name: _STRICT.sub(rb"http://schemas.openxmlformats.org/\1/2006/", data)
        if name.endswith((".xml", ".rels"))
        else data
        for name, data in parts.items()
    }


def _part_of(parts: dict[str, bytes], candidates: list[str], default: str) -> str:
    """The first candidate part in the package, else *default*."""
    return next((name for name in candidates if name in parts), default)


def read_package(paths: ConversionPaths) -> WordPackage:
    """Read every part once; an unreadable or unsafe package is the only source error."""
    if is_compound_file(paths.source):
        raise PackageError(
            f"Cannot read {paths.source}: it is encrypted, or in the Word 97-2003 format; "
            "convert an unencrypted .docx copy"
        )
    try:
        with zipfile.ZipFile(paths.source) as archive:
            entries = archive.infolist()
            problem = too_large(entries)
            if problem is not None:
                raise PackageError(f"DOCX package {problem}")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise PackageError("DOCX contains ambiguous duplicate package member names")
            for entry in entries:
                name = PurePosixPath(entry.filename)
                if entry.flag_bits & 1:
                    raise PackageError("Encrypted DOCX package members are not supported")
                if name.is_absolute() or ".." in name.parts or "\\" in entry.filename:
                    raise PackageError(f"Unsafe DOCX package member: {entry.filename!r}")
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise PackageError(f"Symlink in DOCX package: {entry.filename!r}")
            parts = _transitional({entry.filename: archive.read(entry) for entry in entries})
        # The package relationships name the main document part, which Word calls
        # word/document.xml; the parts it uses are found through its own relationships.
        package_relationships = (
            ET.fromstring(parts["_rels/.rels"])
            if "_rels/.rels" in parts
            else ET.Element(PACKAGE_REL + "Relationships")
        )
        document = _part_of(
            parts,
            _ooxml.related_parts(package_relationships, "", "officeDocument"),
            "word/document.xml",
        )
        root = ET.fromstring(parts[document])
        relationships_part = relationships_part_of(document)
        relationships = (
            ET.fromstring(parts[relationships_part])
            if relationships_part in parts
            else ET.Element(PACKAGE_REL + "Relationships")
        )
        styles_part, numbering_part = (
            _part_of(
                parts,
                _ooxml.related_parts(relationships, document, kind),
                f"word/{kind}.xml",
            )
            for kind in ("styles", "numbering")
        )
        styles = (
            ET.fromstring(parts[styles_part])
            if styles_part in parts
            else ET.Element(WORD + "styles")
        )
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        ET.ParseError,
        KeyError,
        NotImplementedError,
        EOFError,
        zlib.error,
    ) as error:
        raise PackageError(f"Cannot read DOCX package {paths.source}: {error}") from error
    return WordPackage(
        entries, parts, root, relationships, styles, document, styles_part, numbering_part
    )
