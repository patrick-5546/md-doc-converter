"""Explicit Pandoc invocation and typed Markdown documents shared by the tools."""

from __future__ import annotations

import copy
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypeAlias, TypeVar

JSONValue: TypeAlias = bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"] | None
Result = TypeVar("Result")


class PandocError(RuntimeError):
    pass


# Where Pandoc's message about Markdown that it read places the problem.
LOCATION = re.compile(r" (?:at|on) line (\d+)(?: column \d+)?")
_LEVEL = re.compile(r"^\[(?:WARNING|INFO|ERROR)\] ")
# The specifics of a message: quoted text and words with digits, dots or slashes.
_SPECIFICS = re.compile(r"'[^']*'|\"[^\"]*\"|\S*[\d./\\]\S*")


def messages(stderr: str) -> list[str]:
    """Pandoc's messages in *stderr*, each on one line, without their [WARNING] tags."""
    found: list[str] = []
    for line in stderr.splitlines():
        if not line.strip():
            continue
        if line[:1].isspace() and found:
            found[-1] += " " + line.strip()
        else:
            found.append(_LEVEL.sub("", line).strip())
    return found


def message_kind(message: str) -> str:
    """*message* without its specifics, which messages of one kind share."""
    return re.sub(r"\s+", " ", _SPECIFICS.sub("...", message)).strip()


def run_all(calls: Sequence[Callable[[], Result]]) -> list[Result]:
    """The results of *calls*, made at once: each runs Pandoc, in a process of its own, so the
    runs overlap. The first call's exception, in order, is raised."""
    if len(calls) < 2:
        return [call() for call in calls]
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(call) for call in calls]
        return [future.result() for future in futures]


def run(
    text: str,
    *arguments: str,
    pandoc: str = "pandoc",
    description: str,
    warnings: Callable[[str], None] | None = None,
) -> str:
    try:
        result = subprocess.run(
            [pandoc, *arguments], input=text, capture_output=True, text=True, check=False
        )
    except OSError as error:
        raise PandocError(f"failed to run {pandoc!r}: {error}") from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise PandocError(f"{pandoc!r} failed while {description}: {detail}")
    if warnings is not None and result.stderr:
        warnings(result.stderr)
    return result.stdout


def decode_document(payload: str, *, pandoc: str = "pandoc") -> dict[str, JSONValue]:
    try:
        ast: JSONValue = json.loads(payload)
    except json.JSONDecodeError as error:
        raise PandocError(f"{pandoc!r} produced invalid Markdown JSON: {error}") from error
    if not isinstance(ast, dict) or not isinstance(ast.get("blocks"), list):
        raise PandocError(f"{pandoc!r} JSON did not include a blocks list")
    return ast


@dataclass(frozen=True)
class ParsedMarkdown:
    """Markdown parsed by *reader*, kept with its text and reader.

    Word conversion reuses an unchanged page's parse to find its headings and to rewrite its
    links; `copy_ast` gives a copy to rewrite, and refuses other text or another reader. There
    is no parse cache: a check after a text rewrite parses the changed text itself, and
    `run_all` runs a check's parses, such as of a page before and after a rewrite, at once.
    """

    markdown: str
    reader: str
    ast: dict[str, JSONValue]

    @property
    def blocks(self) -> list[JSONValue]:
        blocks = self.ast["blocks"]
        if not isinstance(blocks, list):
            raise PandocError("Pandoc document blocks were replaced with invalid data")
        return blocks

    def copy_ast(self, markdown: str, reader: str) -> dict[str, JSONValue]:
        if markdown != self.markdown or reader != self.reader:
            raise PandocError("Cannot reuse parsed Markdown for different content or reader")
        return copy.deepcopy(self.ast)


def parse_markdown(
    markdown: str,
    *,
    reader: str,
    pandoc: str = "pandoc",
    description: str = "parsing Markdown",
    warnings: Callable[[str], None] | None = None,
) -> ParsedMarkdown:
    payload = run(
        markdown,
        f"--from={reader}",
        "--to=json",
        pandoc=pandoc,
        description=description,
        warnings=warnings,
    )
    return ParsedMarkdown(markdown, reader, decode_document(payload, pandoc=pandoc))
