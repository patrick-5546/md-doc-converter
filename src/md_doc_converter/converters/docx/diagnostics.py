"""Conversion warnings, the transform report, and retained diagnostic reports.

Each warning has a kind, generic text that the summary counts, and a part of the summary: "to
check in Word" for what the document's author can fix, "expected in Markdown" for what
Markdown shows differently; a detail, which only --verbose shows, is something that worked out.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from md_doc_converter.console import Console, active
from md_doc_converter.converters.docx.models import (
    ConversionError,
    ConversionPaths,
    DocumentNavigation,
    HeadingChange,
    PackageError,
)

TRANSFORMS = (
    "fields",
    "hidden-text",
    "spaces",
    "symbols",
    "underline",
    "images",
    "image-conversion",
    "links",
    "bookmarks",
    "headings",
    "lists",
    "tables",
    "code",
    "cleanup",
    "duplicate-headings",
    "pages",
    "title",
    "checks",
)


FATAL_ERRORS = (PackageError, OSError, MemoryError)


_REASON_LINES = 8


_DETAIL_LIMIT = 300

# Where a warning belongs in the summary; a detail is shown only with --verbose.
Section = Literal["word", "markdown", "detail"]
_SECTIONS = {"word": "to check in Word", "markdown": "expected in Markdown"}


def warning(message: str, findings: list[str], *, kind: str = "") -> None:
    findings.append(message)
    active().warning(message, kind=kind)


def describe(error: BaseException) -> str:
    """Name unexpected failures by type; converter errors already explain themselves."""
    text = re.sub(r"\s+", " ", str(error)).strip()
    if not isinstance(error, ConversionError):
        text = f"{type(error).__name__}: {text}" if text else type(error).__name__
    return text if len(text) <= _DETAIL_LIMIT else text[: _DETAIL_LIMIT - 3] + "..."


def category(message: str) -> str:
    """A generic reason for the summary: no quoted document text or numbers."""
    text = re.sub(r"(?<![\w'])'[^'\n]*'(?![\w'])|\"[^\"\n]*\"", "'...'", message)
    text = re.sub(r"\d+", "N", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class TransformRecord:
    applied: Counter[str] = field(default_factory=Counter[str])
    skipped: Counter[str] = field(default_factory=Counter[str])
    failure: str | None = None
    unavailable: str | None = None
    requested: bool = True
    complete: bool = False

    def status(self) -> str:
        if not self.requested:
            return "not requested"
        done = self.complete or bool(self.applied)
        problems = bool(self.skipped) or self.failure is not None
        if done:
            return "partial" if problems else "applied"
        if problems or self.unavailable is not None:
            return "not applied"
        return "not needed"


@dataclass
class TransformReport:
    """Record which transforms were applied, which items they skipped, and why."""

    findings: list[str] = field(default_factory=list[str])
    records: dict[str, TransformRecord] = field(
        default_factory=lambda: {name: TransformRecord() for name in TRANSFORMS}
    )
    console: Console = field(default_factory=lambda: Console("docx-to-md"))
    # The warnings of each kind, and the part of the summary that each kind belongs to.
    kinds: Counter[str] = field(default_factory=Counter[str])
    sections: dict[str, Section] = field(default_factory=dict[str, Section])

    def warn(self, message: str, *, kind: str = "", section: Section = "word") -> None:
        """Report *message*; the summary counts it under *kind*, in its *section*."""
        self.findings.append(message)
        if section == "detail":
            self.console.detail(message)
            return
        if kind:
            self.kinds[kind] += 1
            self.sections.setdefault(kind, section)
        self.console.warning(message, kind=kind)

    def applied(self, name: str, detail: str = "", count: int = 1) -> None:
        if count:
            self.records[name].applied[detail] += count

    def complete(self, name: str) -> None:
        self.records[name].complete = True

    def skipped(
        self, name: str, reason: str, detail: str | None = None, *, section: Section = "word"
    ) -> None:
        """Count a skipped item under a generic reason, its kind; the warning names the item."""
        self.records[name].skipped[reason] += 1
        self.warn(f"{name}: {detail or reason}", kind=reason, section=section)

    def skipped_error(
        self, name: str, error: BaseException, where: str, consequence: str = ""
    ) -> None:
        """Skip one item because of *error*; the summary shows only its generic category."""
        text = describe(error)
        self.skipped(name, category(text) + consequence, f"{where}: {text}{consequence}")

    def failed(self, name: str, error: BaseException) -> None:
        text = describe(error)
        record = self.records[name]
        record.failure = record.failure or text
        self.warn(
            f"{name}: failed and was not applied: {text}",
            kind=f"{name}: failed and was not applied",
        )

    def not_requested(self, name: str, hint: str) -> None:
        record = self.records[name]
        record.requested = False
        record.unavailable = hint

    def status(self, name: str) -> str:
        return self.records[name].status()

    def issues(self) -> list[str]:
        """The summary of the warnings: how many of each kind, in two parts.

        Like `summary`, it quotes nothing from the document, so that it can be shared.
        """
        lines: list[str] = []
        width = len(str(max(self.kinds.values(), default=0)))
        for section, title in _SECTIONS.items():
            counts = sorted(
                (
                    (count, kind)
                    for kind, count in self.kinds.items()
                    if self.sections[kind] == section
                ),
                key=lambda item: (-item[0], item[1]),
            )
            if counts:
                lines.append(f"docx-to-md: {title}:")
                lines.extend(f"  {count:>{width}}  {kind}" for count, kind in counts)
        return lines

    def summary(self) -> list[str]:
        """Each transform's status: counts and generic reasons only; the warnings name items."""
        lines = ["docx-to-md: transforms"]
        for name in TRANSFORMS:
            record = self.records[name]
            lines.append(f"  {name}: {self._status_text(name, record)}")
            label = "issue" if name == "checks" else "not applied"
            reasons = record.skipped.most_common()
            for reason, count in reasons[:_REASON_LINES]:
                lines.append(f"    {label} ({count}): {reason}")
            if len(reasons) > _REASON_LINES:
                lines.append(
                    f"    ... {len(reasons) - _REASON_LINES} more reason(s); see the warnings"
                )
            if record.failure is not None:
                lines.append(f"    failed: {record.failure}")
        return lines

    @staticmethod
    def _counts(counter: Counter[str]) -> str:
        if list(counter) == [""]:
            return str(counter[""])
        return ", ".join(f"{count} {detail}".rstrip() for detail, count in sorted(counter.items()))

    def _status_text(self, name: str, record: TransformRecord) -> str:
        status = record.status()
        skipped = sum(record.skipped.values())
        if name == "checks" and status in {"applied", "partial"}:
            return f"{skipped} issue(s)" if skipped else "passed"
        if status == "applied":
            return f"applied ({self._counts(record.applied)})" if record.applied else "applied"
        if status == "partial":
            # The plain count comes first, so that "applied" follows it rather than the details.
            applied = record.applied.copy()
            count = applied.pop("", None)
            parts = [f"{count} applied"] if count is not None else []
            if applied:
                details = self._counts(applied)
                parts.append(details if count is not None else details + " applied")
            return f"partial ({', '.join([*parts, f'{skipped} not applied'])})"
        if status == "not applied":
            if record.failure is None and record.unavailable is not None:
                return f"not applied: {record.unavailable}"
            return f"not applied ({skipped})" if skipped else "not applied"
        if status == "not requested":
            return f"not requested ({record.unavailable})" if record.unavailable else status
        return status

    def as_json(self) -> dict[str, dict[str, object]]:
        return {
            name: {
                "status": record.status(),
                "applied": dict(record.applied),
                "not_applied": dict(record.skipped),
                "failure": record.failure,
                "reason": record.unavailable,
            }
            for name, record in self.records.items()
        }


@contextmanager
def transform(report: TransformReport, name: str) -> Generator[None]:
    """Report a failed transform; the conversion goes on without it."""
    try:
        yield
    except FATAL_ERRORS:
        raise
    except Exception as error:  # noqa: BLE001 - a layer must never stop the conversion
        report.failed(name, error)


def write_diagnostics(
    work: Path,
    paths: ConversionPaths,
    findings: list[str],
    stats: dict[str, int],
    changes: list[HeadingChange],
    status: str,
    error: str | None = None,
    *,
    source: DocumentNavigation | None = None,
    report: TransformReport | None = None,
) -> None:
    if paths.diagnostics is None:
        return
    document = {
        "status": status,
        "input": str(paths.source_entry),
        "output": str(paths.output),
        "image_directory": str(paths.images),
        "page_directory": str(paths.pages) if paths.split_depth else None,
        "document_view": "accepted/final; review history is not archived",
        "warnings": findings,
        "statistics": stats,
        "transforms": report.as_json() if report is not None else None,
        "qualified_headings": [
            {
                "line": change.line_number,
                "original": change.original_title,
                "qualified": change.final_title,
                "ancestors": list(change.ancestors),
            }
            for change in changes
        ],
        "bookmark_targets": {
            name: {
                "page": target.page.as_posix(),
                "fragment": target.fragment,
                "kind": (
                    "heading"
                    if source.bookmark_sections[name].exact
                    else ("section" if target.fragment else "document_top")
                ),
            }
            for name, target in source.bookmark_targets.items()
            if name in source.bookmarks
        }
        if source is not None
        else dict[str, dict[str, str]](),
        "error": error,
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=".diagnostics-",
        suffix=".json",
        dir=work,
        delete=False,
    ) as stream:
        stream.write(json.dumps(document, indent=2, ensure_ascii=True) + "\n")
        prepared = Path(stream.name)
    prepared.replace(work / "diagnostics.json")
