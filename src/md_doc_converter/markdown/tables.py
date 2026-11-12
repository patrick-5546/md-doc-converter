"""Literal text serialization for converter-generated Markdown tables."""

import html
import re
import unicodedata


def _letter_or_digit(character: str) -> bool:
    """Whether Pandoc, too, reads *character*, if any, as a letter or digit.

    Pandoc's Unicode is older than Python's, and Pandoc reads a letter that it does not know
    as a symbol. A letter or digit of Unicode 3.2 that still is one is a letter or digit to
    both; a later one may be written with an escape that it does not need.
    """
    return (
        character != ""
        and unicodedata.category(character)[0] in "LN"
        and unicodedata.ucd_3_2_0.category(character)[0] in "LN"
    )


def _maybe_letter_or_digit(character: str) -> bool:
    """Whether Pandoc may read *character*, if any, as a letter or digit: whether it is one
    now or was one in Unicode 3.2."""
    return character != "" and (
        unicodedata.category(character)[0] in "LN"
        or unicodedata.ucd_3_2_0.category(character)[0] in "LN"
    )


def _example_label_end(text: str, start: int) -> int:
    """Where the label that starts at *start* of an example reference, @label, ends.

    Pandoc's Markdown reads words of letters and digits, joined by _ or -, as the label. It
    is not text to Pandoc, so an @ right after it can start a citation. A character that
    Pandoc may read as a letter or digit counts, so that the label is never shorter than
    Pandoc's.
    """
    end = start
    while end < len(text):
        if _maybe_letter_or_digit(text[end]):
            end += 1
        elif text[end] in "_-" and _maybe_letter_or_digit(text[end + 1 : end + 2]):
            end += 2
        else:
            break
    return end


def literal_cell(text: str) -> str:
    """Keep cell contents literal while retaining compact, readable Markdown."""
    # Where the label ends of the example reference that the last @ left as text starts.
    label_end = -1

    def _escape_run(match: re.Match[str]) -> str:
        return "".join("\\" + character for character in match[0])

    def _escape_closing_underscore(match: re.Match[str]) -> str:
        following = match.string[match.end() : match.end() + 1]
        return "_" if _letter_or_digit(following) else "\\_"

    def _escape_citation(match: re.Match[str]) -> str:
        nonlocal label_end
        at = match.start()
        if _letter_or_digit(match.string[at - 1 : at]) and at != label_end:
            label_end = _example_label_end(match.string, at + 1)
            return "@"
        label_end = -1
        return "\\@"

    text = text.strip().replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = html.escape(text, quote=False)
    text = re.sub(r"""([\\`*|\[\]~^$'"])""", r"\\\1", text)
    text = re.sub(r"-{2,}|\.{3,}", _escape_run, text)
    # Escape possible closing delimiters, not leading/intra-word signal underscores.
    text = re.sub(r"_", _escape_closing_underscore, text)
    # A citation in Pandoc's Markdown, @key, unless a word that Pandoc reads as text precedes
    # it, as in an e-mail address. An @ that stays text starts an example reference, @label,
    # so a word right after it is that reference's label, not text.
    text = re.sub(r"@", _escape_citation, text)
    # A GFM emoji shortcode, such as :smile:.
    text = re.sub(r":(?=[\w+-]+:)", lambda match: "\\" + match[0], text)
    return text.replace("\n", "<br>")


def table_row(cells: list[str]) -> str:
    """Render escaped cells with one space on either side of nonempty values."""
    return "|" + "|".join(f" {cell} " if cell else " " for cell in cells) + "|"


def trim_empty_edges(rows: list[list[str]]) -> list[list[str]]:
    """*rows* without the empty rows above and below the cells that hold text, and without the
    empty columns left and right of them. A cell is empty when it is ""; rows may differ in
    length."""
    filled = [row for row in rows if any(row)]
    if not filled:
        return []
    top = next(index for index, row in enumerate(rows) if any(row))
    bottom = len(rows) - next(index for index, row in enumerate(reversed(rows)) if any(row))
    left = min(next(index for index, cell in enumerate(row) if cell) for row in filled)
    right = max(max(index for index, cell in enumerate(row) if cell) for row in filled) + 1
    return [row[left:right] for row in rows[top:bottom]]


def table_page(rows: list[list[str]], *, stem: str, source: str, command: str) -> str:
    """The page *stem*.md that *command* writes from *source*, with *rows* as its table.

    The page has a heading from *stem*, with hyphens as spaces, a note that it is generated,
    and the table, whose first row is its header. Short rows are padded. A row wider than the
    header raises ValueError, naming the row by its number, rather than losing cells.
    """
    header, *data = rows
    lines = [
        f"# {literal_cell(stem.replace('-', ' '))}",
        "",
        (
            f"> Generated from {literal_cell(source)} using md-doc-converter ({command}) "
            "— do not manually modify"
        ),
        "",
        table_row([literal_cell(cell) for cell in header]),
        table_row(["---"] * len(header)),
    ]
    for number, row in enumerate(data, 2):
        if len(row) > len(header):
            raise ValueError(
                f"row {number} has {len(row)} cells but the header has {len(header)}; "
                "refusing to discard data"
            )
        cells = row + [""] * (len(header) - len(row))
        lines.append(table_row([literal_cell(cell) for cell in cells]))
    return "\n".join(lines) + "\n"
