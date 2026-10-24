"""Pandoc runs over Markdown, read as the renderer reads it, or over a prepared document, which
the renderer hands Pandoc as JSON."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping

from md_doc_converter.markdown.document import READER

Source = str | Mapping[str, object]


def convert(pandoc: str, source: Source, target: str) -> str:
    """*source*, Markdown or a prepared document, in Pandoc's *target* format."""
    reader, text = (READER, source) if isinstance(source, str) else ("json", json.dumps(source))
    return subprocess.run(
        [pandoc, f"--from={reader}", f"--to={target}", "--wrap=none", "--syntax-highlighting=none"],
        input=text,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def markdown(pandoc: str, document: Mapping[str, object]) -> str:
    """A prepared *document* written as Markdown that the renderer reads back the same."""
    return convert(pandoc, document, f"{READER}-simple_tables-raw_attribute")
