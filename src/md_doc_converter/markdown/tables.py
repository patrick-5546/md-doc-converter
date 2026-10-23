"""Literal text serialization for converter-generated Markdown tables."""

import html
import re


def literal_cell(text: str) -> str:
    """Keep cell contents literal while retaining compact, readable Markdown."""

    def _escape_run(match: re.Match[str]) -> str:
        return "".join("\\" + character for character in match[0])

    text = text.strip().replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = html.escape(text, quote=False)
    text = re.sub(r"""([\\`*|\[\]~^$'"])""", r"\\\1", text)
    text = re.sub(r"-{2,}|\.{3,}", _escape_run, text)
    # Escape possible closing delimiters, not leading/intra-word signal underscores.
    text = re.sub(r"_(?![^\W_])", r"\\_", text)
    # A citation in Pandoc's Markdown, @key, unless a letter or digit precedes it, as in an
    # e-mail address; and a GFM emoji shortcode, such as :smile:.
    text = re.sub(r"(?<![^\W_])@|:(?=[\w+-]+:)", lambda match: "\\" + match[0], text)
    return text.replace("\n", "<br>")


def table_row(cells: list[str]) -> str:
    """Render escaped cells with one space on either side of nonempty values."""
    return "|" + "|".join(f" {cell} " if cell else " " for cell in cells) + "|"
