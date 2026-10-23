"""Explicit Pandoc invocation and typed Markdown documents shared by the tools."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypeAlias, TypeVar

JSONValue: TypeAlias = bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"] | None
Result = TypeVar("Result")


class PandocError(RuntimeError):
    pass


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
    emit_warnings: bool = False,
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
    if emit_warnings and result.stderr:
        sys.stderr.write(result.stderr)
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
    emit_warnings: bool = False,
) -> ParsedMarkdown:
    payload = run(
        markdown,
        f"--from={reader}",
        "--to=json",
        pandoc=pandoc,
        description=description,
        emit_warnings=emit_warnings,
    )
    return ParsedMarkdown(markdown, reader, decode_document(payload, pandoc=pandoc))
