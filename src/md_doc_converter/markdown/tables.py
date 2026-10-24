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


def literal_cell(text: str) -> str:
    """Keep cell contents literal while retaining compact, readable Markdown."""

    def _escape_run(match: re.Match[str]) -> str:
        return "".join("\\" + character for character in match[0])

    def _escape_closing_underscore(match: re.Match[str]) -> str:
        following = match.string[match.end() : match.end() + 1]
        return "_" if _letter_or_digit(following) else "\\_"

    def _escape_citation(match: re.Match[str]) -> str:
        preceding = match.string[match.start() - 1 : match.start()]
        return "@" if _letter_or_digit(preceding) else "\\@"

    text = text.strip().replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = html.escape(text, quote=False)
    text = re.sub(r"""([\\`*|\[\]~^$'"])""", r"\\\1", text)
    text = re.sub(r"-{2,}|\.{3,}", _escape_run, text)
    # Escape possible closing delimiters, not leading/intra-word signal underscores.
    text = re.sub(r"_", _escape_closing_underscore, text)
    # A citation in Pandoc's Markdown, @key, unless a letter or digit precedes it, as in an
    # e-mail address.
    text = re.sub(r"@", _escape_citation, text)
    # A GFM emoji shortcode, such as :smile:.
    text = re.sub(r":(?=[\w+-]+:)", lambda match: "\\" + match[0], text)
    return text.replace("\n", "<br>")


def table_row(cells: list[str]) -> str:
    """Render escaped cells with one space on either side of nonempty values."""
    return "|" + "|".join(f" {cell} " if cell else " " for cell in cells) + "|"


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
