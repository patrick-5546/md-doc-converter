"""Low-level Word XML fields, styles, relationships, and accepted-view normalization."""

from __future__ import annotations

import copy
import io
import posixpath
import re
import unicodedata
import urllib.parse
import uuid
from collections import Counter
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx.models import ConversionError

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


WORD = f"{{{WORD_NS}}}"


REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


DRAWING_WORD = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"


SVG_DRAWING = "{http://schemas.microsoft.com/office/drawing/2016/SVG/main}"


VML = "{urn:schemas-microsoft-com:vml}"


OFFICE = "{urn:schemas-microsoft-com:office:office}"


MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


IMAGE_CONTAINERS = frozenset({WORD + "drawing", WORD + "pict", WORD + "object"})


IMAGE_NODES = frozenset({DRAWING + "blip", VML + "imagedata"})


_TEXT_TAGS = frozenset(
    {WORD + "t", "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"}
)


TEXT_BOX_CONTENT = WORD + "txbxContent"


WORD_SHAPE = "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}"


GRAPHIC_KINDS = {
    "http://schemas.openxmlformats.org/drawingml/2006/chart": "chart",
    "http://schemas.microsoft.com/office/drawing/2014/chartex": "chart",
    "http://schemas.openxmlformats.org/drawingml/2006/diagram": "SmartArt",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup": "group of shapes",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas": "drawing canvas",
    "http://schemas.openxmlformats.org/drawingml/2006/lockedCanvas": "drawing canvas",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingInk": "ink",
    "http://schemas.openxmlformats.org/drawingml/2006/picture": "picture without image data",
}


XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def word_text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter(WORD + "t"))


def paragraph_text(paragraph: ET.Element) -> str:
    return word_text(paragraph).strip()


def word_flag(element: ET.Element | None) -> bool:
    return element is not None and element.get(WORD + "val", "1").casefold() not in {
        "0",
        "false",
        "off",
    }


def word_run(text: str) -> ET.Element:
    run = ET.Element(WORD + "r")
    ET.SubElement(run, WORD + "t", {XML_SPACE: "preserve"}).text = text
    return run


# The prefixes that Pandoc's DOCX reader expects for Word's namespaces.
_CONVENTIONAL_PREFIXES = {
    "w": WORD_NS,
    "r": REL[1:-1],
    "a": DRAWING[1:-1],
    "wp": DRAWING_WORD[1:-1],
    "v": VML[1:-1],
    "o": OFFICE[1:-1],
    "asvg": SVG_DRAWING[1:-1],
    "mc": MC[1:-1],
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}


def word_xml_bytes(root: ET.Element, original: bytes | None = None) -> bytes:
    """*root* as XML, declaring Word's conventional prefixes and those of *original*.

    Pandoc's DOCX reader requires conventional prefixes, and reads the notes, such as a
    hyperlink's r:id in a footnote, with the prefixes that the document part declares,
    whether the part uses them or not (https://github.com/jgm/pandoc/issues/11991).
    """
    # Registering a prefix replaces any namespace that had it, so the document's own come last.
    namespaces = dict(_CONVENTIONAL_PREFIXES)
    for prefix, namespace in namespaces.items():
        ET.register_namespace(prefix, namespace)
    if original is not None:
        for _event, declaration in ET.iterparse(io.BytesIO(original), events=("start-ns",)):
            prefix, namespace = declaration
            if prefix and not re.fullmatch(r"ns\d+", prefix):
                namespaces[prefix] = namespace
                ET.register_namespace(prefix, namespace)
    used = {
        name[1:].split("}", 1)[0]
        for node in root.iter()
        for name in (node.tag, *node.attrib)
        if isinstance(name, str) and name.startswith("{")
    }
    root = copy.copy(root)
    for prefix, namespace in namespaces.items():
        if namespace not in used:
            root.set("xmlns:" + prefix, namespace)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# Relationship types, such as .../officeDocument, in the Transitional and the Strict forms.
_RELATIONSHIP_TYPES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/",
)


def part_name(source: str, target: str) -> str:
    """The package part that a relationship of the *source* part targets.

    *source* is "" for the package's own relationships. A target is relative to the folder of
    *source*, or absolute from the package root (ECMA-376 Part 2, 9.3); one outside the package
    is refused.
    """
    decoded = urllib.parse.unquote(target)
    if "\\" in decoded:
        raise ConversionError(f"Unsafe package target: {target!r}")
    if decoded.startswith("/"):
        joined = decoded[1:]
    else:
        joined = posixpath.join(posixpath.dirname(source), decoded)
    name = posixpath.normpath(joined)
    if name.startswith(("../", "/")) or name in {"", ".", ".."}:
        raise ConversionError(f"Package target escapes the DOCX: {target!r}")
    return name


def related_parts(relationships: ET.Element, source: str, kind: str) -> list[str]:
    """The parts that the relationships of *source* of a type, such as "styles", target."""
    names: list[str] = []
    for relation in relationships.iter(PACKAGE_REL + "Relationship"):
        if relation.get("Type") not in {prefix + kind for prefix in _RELATIONSHIP_TYPES}:
            continue
        if relation.get("TargetMode", "").casefold() == "external":
            continue
        try:
            names.append(part_name(source, relation.get("Target", "")))
        except ConversionError:
            continue
    return names


def accept_view(parent: ET.Element) -> None:
    """Expose accepted content and cached index results to both converters.

    As in Word with every change accepted, a paragraph whose mark was deleted joins the
    paragraph after it, and a table whose rows were all deleted is gone.
    """
    joined = [
        paragraph
        for paragraph in parent.iter(WORD + "p")
        if any(
            paragraph.find(f"./{WORD}pPr/{WORD}rPr/{WORD}{name}") is not None
            for name in ("del", "moveFrom")
        )
    ]
    tables = [table for table in parent.iter(WORD + "tbl") if _has_rows(table)]
    _accept_changes(parent)
    _join_paragraphs(parent, joined)
    emptied = [table for table in tables if not _has_rows(table)]
    if not emptied:
        return
    parents = {child: owner for owner in parent.iter() for child in owner}
    for table in emptied:
        owner = parents.get(table)
        if owner is None:
            continue
        owner.remove(table)
        if owner.tag == WORD + "tc" and owner.find(WORD + "p") is None:
            owner.append(ET.Element(WORD + "p"))


def _has_rows(table: ET.Element) -> bool:
    """Whether *table* has a row of its own, perhaps inside a content control."""
    stack = list(table)
    while stack:
        node = stack.pop()
        if node.tag == WORD + "tr":
            return True
        if node.tag != WORD + "tbl":
            stack.extend(node)
    return False


_SHOWN_RUN_CONTENT = frozenset(
    WORD + name for name in ("t", "tab", "ptab", "br", "cr", "sym", "noBreakHyphen", "softHyphen")
)


class RunProperties:
    """Run properties as Word resolves them.

    A run's own properties decide first, then its character style, its paragraph's style,
    and the document defaults, each style with the styles it is based on.
    """

    def __init__(self, styles: ET.Element) -> None:
        self.styles = {
            node.get(WORD + "styleId", ""): node for node in styles.findall(WORD + "style")
        }
        self.default_style = next(
            (
                identity
                for identity, node in self.styles.items()
                if node.get(WORD + "type") == "paragraph"
                and node.get(WORD + "default") in {"1", "true", "on"}
            ),
            None,
        )
        self.defaults = styles.find(f"./{WORD}docDefaults/{WORD}rPrDefault/{WORD}rPr")

    def _chain(self, identity: str | None) -> Iterator[ET.Element]:
        seen: set[str] = set()
        while identity and identity not in seen and identity in self.styles:
            seen.add(identity)
            properties = self.styles[identity].find(WORD + "rPr")
            if properties is not None:
                yield properties
            based_on = self.styles[identity].find(WORD + "basedOn")
            identity = based_on.get(WORD + "val") if based_on is not None else None

    def settings(
        self, own: ET.Element | None, paragraph: ET.Element | None
    ) -> Iterator[ET.Element]:
        """The run properties that apply, nearest first; *own* is a run's or a paragraph
        mark's w:rPr."""
        if own is not None:
            yield own
            character = own.find(WORD + "rStyle")
            if character is not None:
                yield from self._chain(character.get(WORD + "val"))
        style = paragraph.find(f"./{WORD}pPr/{WORD}pStyle") if paragraph is not None else None
        yield from self._chain(style.get(WORD + "val") if style is not None else self.default_style)
        if self.defaults is not None:
            yield self.defaults

    def hidden(self, run: ET.Element, paragraph: ET.Element | None) -> bool:
        for properties in self.settings(run.find(WORD + "rPr"), paragraph):
            flag = properties.find(WORD + "vanish")
            if flag is not None:
                return word_flag(flag)
        return False

    def monospace(self, own: ET.Element | None, paragraph: ET.Element | None) -> bool:
        """Whether the Latin font is a monospace one; a theme font is not."""
        for properties in self.settings(own, paragraph):
            fonts = properties.find(WORD + "rFonts")
            if fonts is None:
                continue
            if fonts.get(WORD + "asciiTheme") or fonts.get(WORD + "hAnsiTheme"):
                return False
            name = fonts.get(WORD + "ascii") or fonts.get(WORD + "hAnsi")
            if name:
                return name.strip().casefold() in MONOSPACE_FONTS
        return False

    def underlined(self, own: ET.Element | None, paragraph: ET.Element | None) -> bool:
        """Whether the text is underlined, in any style; as for Pandoc, w:u needs a w:val."""
        for properties in self.settings(own, paragraph):
            underline = properties.find(WORD + "u")
            if underline is not None:
                return underline.get(WORD + "val", "none") not in {"none", "false", "0"}
        return False


def single_underlines(*roots: ET.Element) -> int:
    """Write every underline as a single underline; return how many were another kind.

    Pandoc (3.12.1) reads only a single underline as underline
    (https://github.com/jgm/pandoc/issues/11988); a double, thick, dotted or wavy one shows
    the same in Markdown, which has one kind.
    """
    changed = 0
    for root in roots:
        for underline in root.iter(WORD + "u"):
            value = underline.get(WORD + "val")
            if value is not None and value.casefold() not in {"none", "single", "0", "false"}:
                underline.set(WORD + "val", "single")
                changed += 1
    return changed


def drop_redundant_underline(root: ET.Element, styles: ET.Element) -> int:
    """Remove underline where Markdown would show it twice or as text; return how many runs.

    Links are underlined anyway; heading text becomes page titles, file names, and anchors;
    monospace text becomes code, which shows a tag as text. Elsewhere underline stays, and the
    converters write it as <u>, since Markdown has no syntax for it.
    """
    properties = RunProperties(styles)
    changed = 0
    for paragraph in root.iter(WORD + "p"):
        try:
            heading = heading_level(paragraph, properties.styles) is not None
        except ConversionError:
            heading = False
        linked = {
            run
            for link in scoped_iter(paragraph, WORD + "hyperlink")
            for run in scoped_iter(link, WORD + "r")
        }
        for run in scoped_iter(paragraph, WORD + "r"):
            own = run.find(WORD + "rPr")
            if not (heading or run in linked or properties.monospace(own, paragraph)):
                continue
            if not properties.underlined(own, paragraph):
                continue
            if own is None:
                own = ET.Element(WORD + "rPr")
                run.insert(0, own)
            underline = own.find(WORD + "u")
            if underline is None:
                underline = ET.SubElement(own, WORD + "u")
            underline.attrib.clear()
            underline.set(WORD + "val", "none")
            changed += 1
    return changed


# Unicode spaces other than the space, which Markdown editors do not show and text search does
# not match, and the zero-width space, which shows nothing.
UNICODE_SPACES = frozenset(
    "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"
)
ZERO_WIDTH_SPACE = "\u200b"
_LINE_ENDS = frozenset({WORD + "br", WORD + "cr"})
_LINE_SPACES = frozenset({WORD + "tab", WORD + "ptab"})
_LINE_CONTENT = frozenset({WORD + "sym", WORD + "noBreakHyphen", *IMAGE_CONTAINERS})


def _line_parts(paragraph: ET.Element) -> Iterator[ET.Element]:
    """The text, tabs, line ends, and other content of *paragraph*, in order, without the
    paragraphs of its text boxes, which are lines of their own."""
    stack = list(reversed(paragraph))
    while stack:
        node = stack.pop()
        if node.tag in {TEXT_BOX_CONTENT, WORD + "p"}:
            continue
        if node.tag == WORD + "t" or node.tag in _LINE_ENDS | _LINE_SPACES | _LINE_CONTENT:
            yield node
        else:
            stack.extend(reversed(node))


def _plain_paragraph_spaces(paragraph: ET.Element, *, indentable: bool) -> int:
    """Apply plain_spaces() to one paragraph."""
    changed = 0
    texts: dict[ET.Element, list[str]] = {}
    # The characters that start the current line, as (characters, index), and None for a tab.
    lead: list[tuple[list[str], int] | None] = []
    starting = True

    def settle(*, indent: bool) -> None:
        nonlocal changed
        characters = [entry for entry in lead if entry is not None]
        keep = (
            indent
            and len(lead) >= 2
            and any(chars[index] in UNICODE_SPACES for chars, index in characters)
        )
        for chars, index in characters:
            space = chars[index] in UNICODE_SPACES
            replacement = "\u00a0" if keep else " " if space else chars[index]
            changed += replacement != chars[index]
            chars[index] = replacement
        lead.clear()

    for part in _line_parts(paragraph):
        if part.tag in _LINE_ENDS:
            settle(indent=False)
            starting = True
        elif part.tag in _LINE_SPACES:
            if starting:
                lead.append(None)
        elif part.tag != WORD + "t":
            settle(indent=indentable and starting)
            starting = False
        else:
            chars = texts.setdefault(part, list(part.text or ""))
            for index, character in enumerate(chars):
                if character == ZERO_WIDTH_SPACE:
                    continue
                if starting and (character == " " or character in UNICODE_SPACES):
                    lead.append((chars, index))
                    continue
                if starting:
                    settle(indent=indentable)
                    starting = False
                if character in UNICODE_SPACES:
                    chars[index] = " "
                    changed += 1
    settle(indent=False)
    for node, chars in texts.items():
        changed += chars.count(ZERO_WIDTH_SPACE)
        text = "".join(chars).replace(ZERO_WIDTH_SPACE, "")
        if text != (node.text or ""):
            node.text = text
            node.set(XML_SPACE, "preserve")
    return changed


# Word's alt text and titles for images, which Markdown writes as image alt text.
_ALT_TEXT = {
    DRAWING_WORD + "docPr": ("descr", "title"),
    VML + "shape": ("alt", "title", OFFICE + "title"),
    VML + "imagedata": ("alt", "title", OFFICE + "title"),
}


def plain_spaces(root: ET.Element, styles: ET.Element) -> int:
    """Write Unicode spaces as spaces and drop zero-width spaces, in text and in image alt
    text; return how many changed.

    Markdown drops the spaces that start a paragraph, so the spaces that indent a line
    outside a heading, two or more before its text with at least one of them a Unicode
    space, become no-break spaces, which keep the indent that Word shows.
    """
    definitions = {node.get(WORD + "styleId", ""): node for node in styles.findall(WORD + "style")}
    changed = 0
    for paragraph in root.iter(WORD + "p"):
        try:
            indentable = heading_level(paragraph, definitions) is None
        except ConversionError:
            indentable = False
        changed += _plain_paragraph_spaces(paragraph, indentable=indentable)
    for node in root.iter():
        for key in _ALT_TEXT.get(node.tag, ()):
            value = node.get(key)
            if value is None:
                continue
            plain = "".join(
                " " if character in UNICODE_SPACES else character
                for character in value.replace(ZERO_WIDTH_SPACE, "")
            )
            if plain != value:
                changed += sum(
                    character in UNICODE_SPACES or character == ZERO_WIDTH_SPACE
                    for character in value
                )
                node.set(key, plain)
    return changed


NO_BREAK_HYPHEN = "\u2011"
LEFT_TO_RIGHT_MARK = "\u200e"
_RIGHT_TO_LEFT_SETTINGS = frozenset({WORD + "rtl", WORD + "bidi", WORD + "bidiVisual"})


def _right_to_left(*roots: ET.Element) -> bool:
    """Whether *roots* hold right-to-left text, or turn on a right-to-left setting."""
    for root in roots:
        for node in root.iter():
            if node.tag in _RIGHT_TO_LEFT_SETTINGS and word_flag(node):
                return True
            if node.tag in _TEXT_TAGS and any(
                ord(character) >= 0x590
                and unicodedata.bidirectional(character) in {"R", "AL", "AN"}
                for character in node.text or ""
            ):
                return True
    return False


def plain_hyphens_and_marks(root: ET.Element, styles: ET.Element) -> int:
    """Write Word's no-break hyphens as hyphens and, without right-to-left text, drop
    left-to-right marks, in text and in image alt text; return how many changed.

    Few fonts have the no-break hyphen, which text search does not match to a hyphen, and
    Word puts left-to-right marks, which show nothing, in cross-references and caption
    numbers; they change nothing in a document without right-to-left text or settings.
    """
    marks = "" if _right_to_left(root, styles) else LEFT_TO_RIGHT_MARK
    changed = 0

    def plain(text: str) -> str:
        nonlocal changed
        result = text.replace(NO_BREAK_HYPHEN, "-")
        if marks:
            result = result.replace(marks, "")
        changed += sum(text.count(character) for character in NO_BREAK_HYPHEN + marks)
        return result

    for run in root.iter(WORD + "r"):
        for index, child in enumerate(list(run)):
            if child.tag == WORD + "noBreakHyphen":
                hyphen = ET.Element(WORD + "t")
                hyphen.text = "-"
                run[index] = hyphen
                changed += 1
    for node in root.iter():
        if node.tag == WORD + "t" and node.text:
            node.text = plain(node.text)
        for key in _ALT_TEXT.get(node.tag, ()):
            value = node.get(key)
            if value:
                node.set(key, plain(value))
    return changed


def drop_hidden_text(root: ET.Element, styles: ET.Element) -> int:
    """Remove the text that Word's Hidden font effect hides; return how many runs had some.

    Word neither shows nor prints hidden text; RunProperties decides which runs are hidden.
    A hidden run keeps its field characters and pictures, so fields stay whole.
    """
    properties = RunProperties(styles)
    parents = {child: owner for owner in root.iter() for child in owner}
    dropped = 0
    for run in list(root.iter(WORD + "r")):
        paragraph = parents.get(run)
        while paragraph is not None and paragraph.tag != WORD + "p":
            paragraph = parents.get(paragraph)
        shown = [child for child in run if child.tag in _SHOWN_RUN_CONTENT]
        if not shown or not properties.hidden(run, paragraph):
            continue
        for child in shown:
            run.remove(child)
        dropped += 1
        owner = parents.get(run)
        if owner is not None and all(child.tag == WORD + "rPr" for child in run):
            owner.remove(run)
    return dropped


MONOSPACE_FONTS = frozenset(
    {
        "andale mono",
        "cascadia code",
        "cascadia mono",
        "consolas",
        "courier",
        "courier new",
        "dejavu sans mono",
        "fira code",
        "fira mono",
        "liberation mono",
        "lucida console",
        "lucida sans typewriter",
        "menlo",
        "monaco",
        "roboto mono",
        "source code pro",
        "ubuntu mono",
    }
)


# Word's AutoCorrect typography, which breaks code: curly quotes, dashes for "-" and "--",
# an ellipsis for "...", and no-break hyphens. No-break spaces are already spaces.
_CODE_TYPOGRAPHY = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u2013": "-",
    "\u2014": "--",
    "\u2026": "...",
    "\u2011": "-",
}
_STRAIGHTEN = str.maketrans(_CODE_TYPOGRAPHY)


def straighten_code(text: str) -> tuple[str, int]:
    """*text* with Word's typography undone, and how many characters changed."""
    return text.translate(_STRAIGHTEN), sum(text.count(character) for character in _CODE_TYPOGRAPHY)


def code_text(paragraph: ET.Element) -> str:
    """The text of a code paragraph: tabs as four spaces, line breaks as new lines, and the
    no-break spaces that indent it as spaces."""
    pieces: list[str] = []
    for run in scoped_iter(paragraph, WORD + "r"):
        for child in run:
            if child.tag == WORD + "t":
                pieces.append(child.text or "")
            elif child.tag in {WORD + "tab", WORD + "ptab"}:
                pieces.append("    ")
            elif child.tag in {WORD + "br", WORD + "cr"}:
                pieces.append("\n")
            elif child.tag == WORD + "noBreakHyphen":
                pieces.append("-")
    return "".join(" " if piece in UNICODE_SPACES else piece for piece in "".join(pieces))


def shows_content(element: ET.Element) -> bool:
    """Whether *element* shows text, a symbol, or a picture; text boxes are pictures here."""
    return any(
        (node.tag in _TEXT_TAGS and (node.text or "").strip())
        or node.tag == WORD + "sym"
        or node.tag in IMAGE_CONTAINERS
        for node in scoped_iter(element)
    )


def _join_paragraphs(root: ET.Element, joined: list[ET.Element]) -> None:
    """Join each paragraph whose mark was deleted to the next one, which keeps its properties.

    With no paragraph to join, an empty one stays without its list numbering, so it does
    not become an empty list item.
    """
    if not joined:
        return
    parents = {child: owner for owner in root.iter() for child in owner}
    for paragraph in reversed(joined):
        container = parents.get(paragraph)
        if container is None:
            continue
        siblings = list(container)
        position = siblings.index(paragraph)
        following = siblings[position + 1] if position + 1 < len(siblings) else None
        if following is not None and following.tag == WORD + "p":
            start = 1 if following.find(WORD + "pPr") is not None else 0
            content = [child for child in paragraph if child.tag != WORD + "pPr"]
            for offset, node in enumerate(content):
                following.insert(start + offset, node)
            container.remove(paragraph)
        elif not shows_content(paragraph):
            turn_off_numbering(paragraph)


def turn_off_numbering(paragraph: ET.Element) -> None:
    """Give *paragraph* numId 0, which also turns off numbering that a list style gives it."""
    properties = paragraph.find(WORD + "pPr")
    if properties is None:
        properties = ET.Element(WORD + "pPr")
        paragraph.insert(0, properties)
    for numbering in properties.findall(WORD + "numPr"):
        properties.remove(numbering)
    numbering = ET.Element(WORD + "numPr")
    ET.SubElement(numbering, WORD + "numId", {WORD + "val": "0"})
    properties.insert(1 if properties.find(WORD + "pStyle") is not None else 0, numbering)


def _accept_changes(parent: ET.Element) -> None:
    for child in list(parent):
        if child.tag == WORD + "tr" and child.find(f"./{WORD}trPr/{WORD}del") is not None:
            parent.remove(child)
            continue
        if child.tag in {WORD + "del", WORD + "moveFrom"} or (
            isinstance(child.tag, str)
            and child.tag.startswith(WORD)
            and child.tag.endswith("PrChange")
        ):
            parent.remove(child)
            continue
        if child.tag == MC + "AlternateContent":
            choices = list(child.findall(MC + "Choice"))
            fallback = child.find(MC + "Fallback")
            for choice in choices:
                _accept_changes(choice)
            # One image outside text boxes marks a picture that Pandoc reads; otherwise the
            # fallback is usually VML, whose text-box text Pandoc keeps.
            selected = next(
                (
                    choice
                    for choice in choices
                    if sum(node.tag in IMAGE_NODES for node in scoped_iter(choice)) == 1
                ),
                fallback if fallback is not None else next(iter(choices), None),
            )
            position = list(parent).index(child)
            parent.remove(child)
            if selected is None:
                continue
            _accept_changes(selected)
            for offset, item in enumerate(list(selected)):
                parent.insert(position + offset, item)
            continue
        _accept_changes(child)
        if child.tag in {WORD + "ins", WORD + "moveTo", WORD + "sdt", WORD + "customXml"}:
            container = child.find(WORD + "sdtContent") if child.tag == WORD + "sdt" else child
            position = list(parent).index(child)
            parent.remove(child)
            if container is None:
                continue
            for offset, item in enumerate(list(container)):
                parent.insert(position + offset, item)
    for child in list(parent):
        if child.tag == WORD + "fldSimple" and re.match(
            r"\s*TOC\b", child.get(WORD + "instr", ""), re.IGNORECASE
        ):
            position = list(parent).index(child)
            parent.remove(child)
            for offset, item in enumerate(list(child)):
                parent.insert(position + offset, item)


def _field_link(
    instruction: str, children: list[ET.Element], relationships: ET.Element
) -> ET.Element | None:
    if not re.match(r"^\s*HYPERLINK\b", instruction, re.IGNORECASE):
        return None
    local = re.search(r'\\l\s+(?:"([^"]*)"|(\S+))', instruction)
    external = re.match(r'^\s*HYPERLINK\s+(?:"([^"]*)"|([^\\\s]\S*))', instruction, re.IGNORECASE)
    link = ET.Element(WORD + "hyperlink")
    if local is not None and external is None:
        link.set(WORD + "anchor", local.group(1) or local.group(2))
    elif external is not None:
        target = external.group(1) or external.group(2)
        if local is not None:
            target += "#" + (local.group(1) or local.group(2))
        relation_id = "docxField" + uuid.uuid4().hex
        ET.SubElement(
            relationships,
            PACKAGE_REL + "Relationship",
            {
                "Id": relation_id,
                "Target": target,
                "TargetMode": "External",
                "Type": REL[1:-1] + "/hyperlink",
            },
        )
        link.set(REL + "id", relation_id)
    else:
        raise ConversionError(f"Cannot decode Word hyperlink field: {instruction!r}")
    # The ScreenTip, like a hyperlink element's w:tooltip, which Pandoc reads as the title.
    tip = re.search(r'\\o\s+"((?:\\.|[^"\\])*)"', instruction)
    if tip is not None:
        link.set(WORD + "tooltip", re.sub(r"\\(.)", r"\1", tip.group(1)))
    link.extend(children)
    return link


def _split_run_children(
    parent: ET.Element, boundaries: frozenset[str], *, group_content: bool = False
) -> None:
    """Isolate structural run children without losing content or run formatting."""
    for run in list(parent.findall(WORD + "r")):
        content = [child for child in run if child.tag != WORD + "rPr"]
        if len(content) < 2 or not any(child.tag in boundaries for child in content):
            continue
        position = list(parent).index(run)
        properties = run.find(WORD + "rPr")
        parent.remove(run)
        segments: list[list[ET.Element]] = []
        for child in content:
            if (
                not group_content
                or child.tag in boundaries
                or not segments
                or segments[-1][-1].tag in boundaries
            ):
                segments.append([child])
            else:
                segments[-1].append(child)
        for offset, segment in enumerate(segments):
            replacement = ET.Element(WORD + "r", run.attrib)
            if properties is not None:
                replacement.append(copy.deepcopy(properties))
            replacement.extend(segment)
            parent.insert(position + offset, replacement)


def _complex_fields(parent: ET.Element) -> list[tuple[int, int, str, list[ET.Element]]]:
    _split_run_children(parent, frozenset({WORD + "fldChar"}), group_content=True)
    children = list(parent)
    fields: list[tuple[int, int, str, list[ET.Element]]] = []
    start: int | None = None
    separate: int | None = None
    depth = 0
    instructions: list[str] = []
    for index, child in enumerate(children):
        if child.tag != WORD + "r":
            continue
        for marker in child.findall(WORD + "fldChar"):
            kind = marker.get(WORD + "fldCharType")
            if kind == "begin":
                if depth == 0:
                    start, separate = index, None
                    instructions = []
                depth += 1
            elif kind == "separate" and depth == 1:
                separate = index
            elif kind == "end" and depth:
                depth -= 1
                if depth == 0 and start is not None and separate is not None:
                    fields.append(
                        (start, index + 1, "".join(instructions), children[separate + 1 : index])
                    )
        if depth:
            instructions.extend(node.text or "" for node in child.findall(WORD + "instrText"))
    return fields


def _redundant_page_reference(
    parent: ET.Element, instruction: str, relationships: ET.Element
) -> bool:
    if parent.tag != WORD + "hyperlink":
        return False
    match = re.match(r'\s*PAGEREF\s+(?:"([^"]*)"|(\S+))', instruction, re.IGNORECASE)
    if match is None:
        return False
    anchor = parent.get(WORD + "anchor", "")
    relation_id = parent.get(REL + "id")
    if relation_id is not None:
        relation = next((node for node in relationships if node.get("Id") == relation_id), None)
        if relation is None:
            return False
        target = _reference.split_uri(relation.get("Target", ""))
        if target.path or target.scheme or target.netloc or target.query:
            return False
        anchor = anchor or urllib.parse.unquote(target.fragment)
    return bool(anchor) and anchor == (match.group(1) or match.group(2))


_ZERO_WIDTH = frozenset(
    WORD + name for name in ("bookmarkStart", "bookmarkEnd", "proofErr", "permStart", "permEnd")
)


def _trim_page_leader(parent: ET.Element, end: int) -> None:
    """Remove the tabs and spaces that lead up to ``parent[end]``, such as a TOC tab leader."""
    for child in reversed(list(parent)[:end]):
        if child.tag in _ZERO_WIDTH:
            continue
        if child.tag != WORD + "r":
            return
        for node in reversed([node for node in child if node.tag != WORD + "rPr"]):
            if node.tag == WORD + "lastRenderedPageBreak":
                continue
            if node.tag == WORD + "tab" or (
                node.tag == WORD + "t" and not (node.text or "").strip()
            ):
                child.remove(node)
                continue
            if node.tag == WORD + "t":
                node.text = (node.text or "").rstrip()
            return


def normalize_hyperlink_fields(
    root: ET.Element, relationships: ET.Element
) -> tuple[list[str], int]:
    """Decode hyperlink fields and drop the page numbers of table-of-contents links.

    A table-of-contents entry is a hyperlink to a bookmark that also holds a PAGEREF to the
    same bookmark. Its Word page number means nothing in Markdown, so the field is removed
    with the tab leader before it. A field that cannot be decoded is left as it is and
    reported. Return the problems and the number of page numbers dropped.
    """
    problems: list[str] = []
    dropped = 0
    for parent in root.iter():
        for simple in list(parent.findall(WORD + "fldSimple")):
            instruction = simple.get(WORD + "instr", "")
            position = list(parent).index(simple)
            try:
                if _redundant_page_reference(parent, instruction, relationships):
                    del parent[position]
                    _trim_page_leader(parent, position)
                    dropped += 1
                    continue
                replacement = _field_link(instruction, list(simple), relationships)
            except ConversionError as error:
                problems.append(str(error))
                continue
            if replacement is not None:
                parent[position : position + 1] = [replacement]
    for parent in root.iter():
        if parent.tag not in {WORD + "p", WORD + "hyperlink"}:
            continue
        replacements: list[tuple[int, int, list[ET.Element]]] = []
        for start, end, instruction, cached in _complex_fields(parent):
            try:
                if _redundant_page_reference(parent, instruction, relationships):
                    # The outer TOC link already supplies this destination. Keeping
                    # PAGEREF active makes Pandoc extend it over our alignment markers.
                    replacements.append((start, end, []))
                elif parent.tag == WORD + "p":
                    replacement = _field_link(instruction, cached, relationships)
                    if replacement is not None:
                        replacements.append((start, end, [replacement]))
            except ConversionError as error:
                problems.append(str(error))
        for start, end, replacement_nodes in reversed(replacements):
            parent[start:end] = replacement_nodes
            if not replacement_nodes:
                _trim_page_leader(parent, start)
                dropped += 1
    return problems, dropped


_REFERENCE_FIELD = re.compile(
    r'\s*(?:NOTE|PAGE)?REF\s+(?:"([^"]*)"|(\S+))(.*)', re.IGNORECASE | re.DOTALL
)


def _missing_reference(instruction: str, bookmarks: Collection[str]) -> str | None:
    match = _REFERENCE_FIELD.fullmatch(instruction)
    if match is None or not re.search(r"\\h\b", match.group(3)):
        return None
    name = match.group(1) or match.group(2) or ""
    return None if name in bookmarks else name


def unlink_missing_references(root: ET.Element, bookmarks: Collection[str]) -> list[str]:
    """Flatten hyperlinked cross-references to missing bookmarks to their cached text."""
    missing: list[str] = []
    simple = [
        (parent, field) for parent in root.iter() for field in parent.findall(WORD + "fldSimple")
    ]
    # Inner fields first, so an outer field moves already-flattened content.
    for parent, field in reversed(simple):
        name = _missing_reference(field.get(WORD + "instr", ""), bookmarks)
        if name is not None:
            position = list(parent).index(field)
            parent[position : position + 1] = list(field)
            missing.append(name)
    missing.reverse()
    for parent in list(root.iter()):
        if parent.tag not in {WORD + "p", WORD + "hyperlink"}:
            continue
        names: list[str] = []
        for start, end, instruction, cached in reversed(_complex_fields(parent)):
            name = _missing_reference(instruction, bookmarks)
            if name is not None:
                parent[start:end] = cached
                names.append(name)
        missing.extend(reversed(names))
    return missing


def linked_bookmarks(root: ET.Element) -> set[str]:
    r"""The bookmarks that Word links to: those of hyperlinks to a place in the document, such
    as table-of-contents entries, and of cross-reference fields with the \h switch."""
    names = {
        link.get(WORD + "anchor", "")
        for link in root.iter(WORD + "hyperlink")
        if link.get(REL + "id") is None
    }
    for parent in list(root.iter()):
        if parent.tag not in {WORD + "p", WORD + "hyperlink"}:
            continue
        for _start, _end, instruction, _cached in _complex_fields(parent):
            match = _REFERENCE_FIELD.fullmatch(instruction)
            if match is not None and re.search(r"\\h\b", match.group(3)):
                names.add(match.group(1) or match.group(2) or "")
    return names - {""}


# What Word shows for an unchecked and a checked check box form field.
CHECK_BOXES = ("\u2610", "\u2612")


def _checked(box: ET.Element) -> bool:
    """Whether the w:checkBox of a check box form field is checked, else its default."""
    for name in ("checked", "default"):
        setting = box.find(WORD + name)
        if setting is not None:
            return setting.get(WORD + "val", "1").casefold() not in {"0", "false", "off"}
    return False


def form_check_boxes(root: ET.Element) -> int:
    """Write each check box form field as the box that Word shows, checked or not.

    Such a field, FORMCHECKBOX, has no result for Pandoc to keep: Word draws the box
    (https://github.com/jgm/pandoc/issues/11990). Return how many there were.
    """
    count = 0
    for parent in list(root.iter()):
        if parent.find(f"./{WORD}r/{WORD}fldChar") is None:
            continue
        _split_run_children(parent, frozenset({WORD + "fldChar"}), group_content=True)
        children = list(parent)
        boxes: list[tuple[int, int, bool, ET.Element]] = []
        depth = 0
        start: tuple[int, ET.Element] | None = None
        for index, child in enumerate(children):
            if child.tag != WORD + "r":
                continue
            for marker in child.findall(WORD + "fldChar"):
                kind = marker.get(WORD + "fldCharType")
                if kind == "begin":
                    box = marker.find(f"./{WORD}ffData/{WORD}checkBox")
                    if depth == 0 and box is not None:
                        start = (index, box)
                    depth += 1
                elif kind == "end" and depth:
                    depth -= 1
                    if depth == 0 and start is not None:
                        boxes.append((start[0], index + 1, _checked(start[1]), children[start[0]]))
                        start = None
        for first, end, checked, begin in reversed(boxes):
            shown = ET.Element(WORD + "r")
            properties = begin.find(WORD + "rPr")
            if properties is not None:
                shown.append(copy.deepcopy(properties))
            ET.SubElement(shown, WORD + "t").text = CHECK_BOXES[checked]
            parent[first:end] = [shown]
            count += 1
    return count


def unlink_page_references(root: ET.Element) -> int:
    r"""Keep the page number that each PAGEREF field shows as text; return how many.

    A page number means nothing in Markdown, so it does not become a link, as Pandoc would
    make one with the \h switch. The table of contents loses its page numbers before this.
    """
    count = 0
    for parent in list(root.iter()):
        if parent.tag not in {WORD + "p", WORD + "hyperlink"}:
            continue
        for start, end, instruction, cached in reversed(_complex_fields(parent)):
            if re.match(r"\s*PAGEREF\b", instruction, re.IGNORECASE):
                parent[start:end] = cached
                count += 1
    return count


def _field_character(kind: str) -> ET.Element:
    run = ET.Element(WORD + "r")
    ET.SubElement(run, WORD + "fldChar", {WORD + "fldCharType": kind})
    return run


def expand_simple_fields(root: ET.Element) -> int:
    """Rewrite each simple field as the equivalent complex field; return how many.

    Pandoc 3.12.1 drops a w:fldSimple together with its cached result, such as a caption
    number, but keeps the cached result of a complex field
    (https://github.com/jgm/pandoc/issues/11944, fixed after 3.12.1).
    """
    simple = [
        (parent, field) for parent in root.iter() for field in parent.findall(WORD + "fldSimple")
    ]
    # Inner fields first, so an outer field moves already-expanded content.
    for parent, field in reversed(simple):
        begin = _field_character("begin")
        data = field.find(WORD + "fldData")
        if data is not None:
            field.remove(data)
            begin[0].append(data)
        instruction = ET.Element(WORD + "r")
        ET.SubElement(instruction, WORD + "instrText", {XML_SPACE: "preserve"}).text = field.get(
            WORD + "instr", ""
        )
        position = list(parent).index(field)
        parent[position : position + 1] = [
            begin,
            instruction,
            _field_character("separate"),
            *field,
            _field_character("end"),
        ]
    return len(simple)


@dataclass
class _OpenField:
    begin: ET.Element
    nested: bool
    instructions: list[ET.Element]
    separate: ET.Element | None = None


def _remove_field_mark(mark: ET.Element, parents: dict[ET.Element, ET.Element]) -> None:
    """Remove a field character or instruction, and its run if nothing else is left in it."""
    run = parents.get(mark)
    if run is None or not any(child is mark for child in run):
        return
    run.remove(mark)
    container = parents.get(run)
    if (
        run.tag == WORD + "r"
        and container is not None
        and all(child.tag == WORD + "rPr" for child in run)
        and any(child is run for child in container)
    ):
        container.remove(run)


def _shown(root: ET.Element) -> list[ET.Element]:
    """Document order without deleted content or the alternatives that converters ignore."""
    skipped = {WORD + "del", WORD + "moveFrom", MC + "Choice"}
    result: list[ET.Element] = []
    pending = [root]
    while pending:
        node = pending.pop()
        result.append(node)
        pending.extend(child for child in reversed(list(node)) if child.tag not in skipped)
    return result


def _keywordless(instruction: str) -> bool:
    """Whether a field's own code names no field, as when it is empty or only switches.

    Word calls such a field empty and shows nothing of it, not even its result.
    """
    return not instruction.strip() or instruction.lstrip().startswith("\\")


def _drop_field_result(
    separate: ET.Element, end: ET.Element, parents: dict[ET.Element, ET.Element]
) -> bool:
    """Remove what a field shows, between its separator and its end, keeping bookmarks and the
    other marks that show nothing; return False, removing nothing, unless both are in runs of
    one container, such as a paragraph."""
    first, last = parents.get(separate), parents.get(end)
    container = None if first is None else parents.get(first)
    if container is None or last is None or parents.get(last) is not container:
        return False
    _split_run_children(container, frozenset({WORD + "fldChar"}), group_content=True)
    children = list(container)
    for child in children:
        parents[child] = container
        for node in child:
            parents[node] = child
    start = next((index for index, child in enumerate(children) if parents[separate] is child), -1)
    stop = next((index for index, child in enumerate(children) if parents[end] is child), -1)
    if start < 0 or stop < start:
        return False
    for child in children[start + 1 : stop]:
        if child.tag not in _ZERO_WIDTH:
            position = list(container).index(child)
            container[position : position + 1] = [
                node for node in child.iter() if node.tag in _ZERO_WIDTH
            ]
    return True


def repair_fields(root: ET.Element) -> Counter[str]:
    """Reduce the Word fields that Pandoc cannot close to their cached text; count by kind.

    Pandoc ignores a field separator or end that does not match its open field. The field
    then stays open, and each later numbered paragraph's text moves into the next
    paragraph. Fields without an instruction, fields that never end, and fields spanning
    paragraphs, such as a table of contents, are unwrapped to their cached text; hyperlink
    fields keep their link. A field inside another field's instruction, which Word does not
    show, is removed, as are separators and ends outside any field. A field whose own code
    names no field, as when it is empty or only switches, shows nothing in Word, so its
    cached result goes too when it is in one paragraph.
    """
    parents = {child: parent for parent in root.iter() for child in parent}

    def paragraph(node: ET.Element) -> ET.Element | None:
        current = parents.get(node)
        while current is not None and current.tag != WORD + "p":
            current = parents.get(current)
        return current

    counts: Counter[str] = Counter()
    marks: list[ET.Element] = []
    nested: list[tuple[_OpenField, ET.Element]] = []
    hidden: list[tuple[_OpenField, ET.Element]] = []
    stack: list[_OpenField] = []

    def unwrap(current: _OpenField, end: ET.Element | None) -> None:
        marks.extend([current.begin, *current.instructions])
        marks.extend(node for node in (current.separate, end) if node is not None)
        counts["field(s) unwrapped"] += 1

    for node in _shown(root):
        if node.tag == WORD + "instrText":
            if stack and stack[-1].separate is None:
                stack[-1].instructions.append(node)
            continue
        if node.tag != WORD + "fldChar":
            continue
        kind = node.get(WORD + "fldCharType")
        if kind == "begin":
            stack.append(_OpenField(node, bool(stack) and stack[-1].separate is None, []))
        elif kind == "separate" and stack and stack[-1].separate is None:
            stack[-1].separate = node
        elif kind == "end" and stack:
            current = stack.pop()
            instruction = "".join(item.text or "" for item in current.instructions)
            if current.nested:
                nested.append((current, node))
            elif (
                _keywordless(instruction)
                and current.separate is not None
                and paragraph(current.separate) is paragraph(node)
            ):
                hidden.append((current, node))
            elif not instruction.strip() or (
                paragraph(current.begin) is not paragraph(node)
                and not re.match(r"\s*HYPERLINK\b", instruction, re.IGNORECASE)
            ):
                unwrap(current, node)
        elif kind in {"separate", "end"}:
            # Word and Pandoc ignore these.
            marks.append(node)
            counts["stray field mark(s) removed"] += 1
    for current in stack:
        unwrap(current, None)
    # Inner fields end first; removing them keeps an outer field's first and last runs.
    for current, end in nested:
        first, last = parents[current.begin], parents[end]
        container = parents.get(first)
        if container is None or container is not parents.get(last):
            unwrap(current, end)
            continue
        children = list(container)
        if first not in children or last not in children:
            unwrap(current, end)
            continue
        del container[children.index(first) : children.index(last) + 1]
        counts["field(s) in an instruction removed"] += 1
    for current, end in hidden:
        if current.separate is None or not _drop_field_result(current.separate, end, parents):
            unwrap(current, end)
            continue
        marks.extend([current.begin, *current.instructions, current.separate, end])
        counts["field result(s) that Word does not show removed"] += 1
    for mark in marks:
        _remove_field_mark(mark, parents)
    return counts


def split_drawing_runs(root: ET.Element) -> None:
    for parent in root.iter():
        _split_run_children(parent, IMAGE_CONTAINERS)


def scoped_iter(node: ET.Element, tag: str | None = None) -> Iterator[ET.Element]:
    """Pre-order elements outside text boxes, whose content the converters place differently."""
    stack = [node]
    while stack:
        current = stack.pop()
        if tag is None or current.tag == tag:
            yield current
        stack.extend(child for child in reversed(current) if child.tag != TEXT_BOX_CONTENT)


def unit_image_nodes(unit: ET.Element) -> list[ET.Element]:
    return [node for node in scoped_iter(unit) if node.tag in IMAGE_NODES]


def _outer_containers(root: ET.Element) -> Iterator[ET.Element]:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.tag in IMAGE_CONTAINERS:
            yield node
            continue
        stack.extend(child for child in reversed(node) if child.tag != TEXT_BOX_CONTENT)


def image_units(root: ET.Element) -> list[ET.Element]:
    """Outermost image containers outside text boxes that hold at least one image."""
    return [node for node in _outer_containers(root) if unit_image_nodes(node)]


def image_containers_without_images(root: ET.Element) -> list[ET.Element]:
    """Outermost drawings outside text boxes with no image both converters can place."""
    return [node for node in _outer_containers(root) if not unit_image_nodes(node)]


def container_kind(node: ET.Element) -> str:
    """Name a drawing without an image, for reports; never its document text."""
    if node.tag == WORD + "object":
        ole = next(node.iter(OFFICE + "OLEObject"), None)
        program = ole.get("ProgID", "") if ole is not None else ""
        return f"embedded object without preview ({program})" if program else "embedded object"
    if node.tag == WORD + "drawing":
        data = next(node.iter(DRAWING + "graphicData"), None)
        uri = data.get("uri", "") if data is not None else ""
        if uri.endswith("/wordprocessingShape"):
            return (
                "DrawingML text box"
                if next(node.iter(WORD_SHAPE + "txbx"), None) is not None
                else "shape"
            )
        if uri in GRAPHIC_KINDS:
            return GRAPHIC_KINDS[uri]
        return f"drawing ({uri.rstrip('/').rsplit('/', 1)[-1]})" if uri else "empty drawing"
    if any(item.get(OFFICE + "hr", "").casefold() in {"t", "true"} for item in node.iter()):
        return "horizontal line"
    if next(node.iter(VML + "textbox"), None) is not None:
        return "VML text box"
    if next(node.iter(WORD + "control"), None) is not None:
        return "form control"
    if next(node.iter(VML + "group"), None) is not None:
        return "group of shapes"
    return "shape" if any(item.tag.startswith(VML) for item in node) else "empty picture"


def heading_level(paragraph: ET.Element, styles: dict[str, ET.Element]) -> int | None:
    properties = paragraph.find(WORD + "pPr")
    seen: set[str] = set()
    while properties is not None:
        outline = properties.find(WORD + "outlineLvl")
        if outline is not None:
            value = outline.get(WORD + "val", "")
            if not value.isdigit() or int(value) > 9:
                raise ConversionError(f"Invalid Word outline level: {value!r}")
            level = int(value) + 1
            if level == 10:
                return None
            if level > 6:
                raise ConversionError(
                    f"Cannot faithfully express Word heading level {level} in Markdown"
                )
            return level
        style = properties.find(WORD + "pStyle")
        if style is None:
            return None
        name = style.get(WORD + "val", "")
        if name in seen:
            raise ConversionError(f"Cyclic Word style inheritance: {name}")
        seen.add(name)
        direct = re.fullmatch(r"heading\s*([1-9])", name, re.IGNORECASE)
        if direct:
            level = int(direct.group(1))
            if level > 6:
                raise ConversionError(
                    f"Cannot faithfully express Word heading level {level} in Markdown"
                )
            return level
        definition = styles.get(name)
        if definition is None:
            return None
        properties = copy.deepcopy(definition.find(WORD + "pPr"))
        based_on = definition.find(WORD + "basedOn")
        if based_on is not None:
            if properties is None:
                properties = ET.Element(WORD + "pPr")
            ET.SubElement(
                properties, WORD + "pStyle", {WORD + "val": based_on.get(WORD + "val", "")}
            )
    return None


def link_numbering_styles(numbering: ET.Element, styles: dict[str, ET.Element]) -> bool:
    """Point each list at the definition its Word list style links to; True if any changed.

    Pandoc ignores w:numStyleLink, so lists defined through a list style lose their levels.
    """
    abstracts = {
        node.get(WORD + "abstractNumId", ""): node
        for node in numbering.findall(WORD + "abstractNum")
    }
    instances = {node.get(WORD + "numId", ""): node for node in numbering.findall(WORD + "num")}

    def definition(identity: str) -> str | None:
        seen: set[str] = set()
        while identity not in seen:
            seen.add(identity)
            abstract = abstracts.get(identity)
            if abstract is None:
                return None
            link = abstract.find(WORD + "numStyleLink")
            if link is None:
                return identity
            style = styles.get(link.get(WORD + "val", ""))
            number = None if style is None else style.find(f"./{WORD}pPr/{WORD}numPr/{WORD}numId")
            instance = None if number is None else instances.get(number.get(WORD + "val", ""))
            target = None if instance is None else instance.find(WORD + "abstractNumId")
            if target is None:
                return None
            identity = target.get(WORD + "val", "")
        return None

    changed = False
    for instance in numbering.findall(WORD + "num"):
        reference = instance.find(WORD + "abstractNumId")
        if reference is None:
            continue
        identity = reference.get(WORD + "val", "")
        resolved = definition(identity)
        if resolved is not None and resolved != identity:
            reference.set(WORD + "val", resolved)
            changed = True
    return changed


def numbered_lists(numbering: ET.Element) -> frozenset[str]:
    """The numIds whose list definition has levels; Word numbers only their paragraphs."""
    abstracts = {
        node.get(WORD + "abstractNumId", ""): node
        for node in numbering.findall(WORD + "abstractNum")
    }
    result: set[str] = set()
    for instance in numbering.findall(WORD + "num"):
        reference = instance.find(WORD + "abstractNumId")
        abstract = None if reference is None else abstracts.get(reference.get(WORD + "val", ""))
        if (abstract is not None and abstract.find(WORD + "lvl") is not None) or instance.find(
            f"./{WORD}lvlOverride/{WORD}lvl"
        ) is not None:
            result.add(instance.get(WORD + "numId", ""))
    return frozenset(result)


def unlabeled_lists(numbering: ET.Element) -> frozenset[str]:
    """The numIds whose every level shows no number or bullet: an empty level text and no
    picture bullet, as a list that Word uses only to indent its paragraphs has."""
    abstracts = {
        node.get(WORD + "abstractNumId", ""): node
        for node in numbering.findall(WORD + "abstractNum")
    }
    result: set[str] = set()
    for instance in numbering.findall(WORD + "num"):
        reference = instance.find(WORD + "abstractNumId")
        abstract = None if reference is None else abstracts.get(reference.get(WORD + "val", ""))
        levels = {
            level.get(WORD + "ilvl", ""): level
            for level in ([] if abstract is None else abstract.findall(WORD + "lvl"))
        }
        for override in instance.findall(WORD + "lvlOverride"):
            level = override.find(WORD + "lvl")
            if level is not None:
                levels[override.get(WORD + "ilvl", "")] = level
        if levels and all(
            (text := level.find(WORD + "lvlText")) is not None
            and not text.get(WORD + "val")
            and level.find(WORD + "lvlPicBulletId") is None
            for level in levels.values()
        ):
            result.add(instance.get(WORD + "numId", ""))
    return frozenset(result)


def numbering_reference(paragraph: ET.Element, styles: dict[str, ET.Element]) -> str | None:
    """The numId that applies to *paragraph*, directly or through its style chain; 0 is none."""
    properties = paragraph.find(WORD + "pPr")
    seen: set[str] = set()
    while properties is not None:
        number = properties.find(f"./{WORD}numPr/{WORD}numId")
        if number is not None:
            value = number.get(WORD + "val", "")
            if not value.isdigit():
                raise ConversionError(f"Invalid Word numbering identity: {value!r}")
            return value
        style = properties.find(WORD + "pStyle")
        if style is None:
            return None
        name = style.get(WORD + "val", "")
        if name in seen:
            raise ConversionError(f"Cyclic Word numbering style: {name}")
        seen.add(name)
        definition = styles.get(name)
        if definition is None:
            return None
        properties = copy.deepcopy(definition.find(WORD + "pPr"))
        based_on = definition.find(WORD + "basedOn")
        if based_on is not None:
            if properties is None:
                properties = ET.Element(WORD + "pPr")
            ET.SubElement(
                properties,
                WORD + "pStyle",
                {
                    WORD + "val": based_on.get(WORD + "val", ""),
                },
            )
    return None


def is_list_item(
    paragraph: ET.Element, styles: dict[str, ET.Element], numbered: Collection[str]
) -> bool:
    """Whether Word numbers *paragraph*; *numbered* holds the numIds that define a list."""
    value = numbering_reference(paragraph, styles)
    return value is not None and int(value) != 0 and value in numbered


def pandoc_heading(paragraph: ET.Element, styles: dict[str, ET.Element]) -> bool:
    """Whether Pandoc reads *paragraph* as a heading: a style in its chain is named "heading N".

    Pandoc never makes such a paragraph a list item, whatever its numbering.
    """
    style = paragraph.find(f"./{WORD}pPr/{WORD}pStyle")
    name = None if style is None else style.get(WORD + "val")
    seen: set[str] = set()
    while name is not None and name not in seen:
        seen.add(name)
        definition = styles.get(name)
        if definition is None:
            return False
        label = definition.find(WORD + "name")
        shown = None if label is None else label.get(WORD + "val")
        match = re.fullmatch(r"heading ([0-9]+)", name if shown is None else shown, re.IGNORECASE)
        if match is not None and int(match.group(1)) > 0:
            return True
        parent = definition.find(WORD + "basedOn")
        name = None if parent is None else parent.get(WORD + "val")
    return False
