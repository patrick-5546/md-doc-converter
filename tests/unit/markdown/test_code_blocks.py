"""Code blocks in Markdown preprocessing: fences read as GitHub reads them."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from unittest import mock

import pytest

from md_doc_converter.markdown import preprocess
from md_doc_converter.markdown.document import READER, MarkdownError
from tests.support import documents

FENCES = (
    'Text.\n\n```python title="x.py"\nprint(1)\n```\n\n'
    "- Item:\n\n  ```sh  run it  \n  ls\n  ```\n\n"
    "> ```js {1,3}\n> a()\n> ```\n\n"
    "- ```c extra\n  x\n  ```\n\n"
    "~~~ruby some `words`\nputs 1\n~~~\n\n"
    "```python {.numberLines}\nprint(2)\n```\n\n"
    "```python\nprint(3)\n```\n\n"
    '````md\n```js title="shown"\n```\n````\n\n'
    "Use `` ```js two words `` inline.\n\n"
    "    ```py indented code\n"
)


@pytest.mark.parametrize("ending", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_a_fence_keeps_only_its_language_when_pandoc_cannot_read_the_rest(ending: str) -> None:
    source = FENCES.replace("\n", ending)
    stats: Counter[str] = Counter()

    result = preprocess.trim_fence_info_strings(source, "page.md", stats)

    assert result == (
        source.replace('```python title="x.py"', "```python")
        .replace("```sh  run it  ", "```sh")
        .replace("> ```js {1,3}", "> ```js")
        .replace("- ```c extra", "- ```c")
        .replace("~~~ruby some `words`", "~~~ruby")
    )
    assert stats == Counter({"fence info strings trimmed": 5})


def code_blocks(source: documents.Source, reader: str, pandoc: str) -> list[tuple[str, str]]:
    """The language, or "", and the code of each code block that *reader* reads in Markdown
    *source*, or in a prepared document."""
    if isinstance(source, str):
        result = subprocess.run(
            [pandoc, f"--from={reader}", "--to=json"],
            input=source,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    else:
        result = documents.convert(pandoc, source, "json")
    found: list[tuple[str, str]] = []

    def walk(node: object) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            if node.get("t") == "CodeBlock":
                (_identifier, classes, _attributes), code = node["c"]
                found.append((classes[0] if classes else "", code))
            for child in node.values():
                walk(child)

    walk(json.loads(result))
    return found


def test_prepared_code_blocks_are_the_ones_github_shows(pandoc: str) -> None:
    prepared = preprocess.preprocess(FENCES)

    assert code_blocks(prepared, READER, pandoc) == code_blocks(FENCES, "gfm", pandoc)


def test_a_first_word_in_pandocs_attribute_syntax_is_left_alone() -> None:
    # Trimmed, the first fence would open Pandoc's raw HTML block.
    source = "```{=html} extra\n<b>bold</b>\n```\n\n```python title\nx\n```\n"
    stats: Counter[str] = Counter()

    result = preprocess.trim_fence_info_strings(source, None, stats)

    assert result == source.replace("```python title", "```python")
    assert stats == Counter({"fence info strings trimmed": 1})


def test_a_document_without_such_fences_is_not_parsed_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", mock.Mock(side_effect=AssertionError("parsed")))
    source = "```python\nprint(1)\n```\n\n~~~\nplain\n~~~\n"

    assert preprocess.trim_fence_info_strings(source, None, Counter()) == source


def test_a_failed_parse_is_an_explicit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = subprocess.CompletedProcess(["pandoc"], 1, "", "bad reader")
    monkeypatch.setattr(subprocess, "run", mock.Mock(return_value=completed))

    with pytest.raises(MarkdownError, match="bad reader"):
        preprocess.trim_fence_info_strings('```python title="x"\nx\n```\n', None, Counter())
