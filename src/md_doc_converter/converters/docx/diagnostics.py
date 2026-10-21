"""Conversion warnings, the transform report, and retained diagnostic reports."""

from __future__ import annotations

import json
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Generator, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from md_doc_converter.converters.docx.models import (
    ConversionError,
    ConversionPaths,
    HeadingChange,
    PackageError,
    SourceDocument,
)

TRANSFORMS = (
    "alignment",
    "fields",
    "symbols",
    "hidden-text",
    "spaces",
    "literal-text",
    "line-breaks",
    "images",
    "image-conversion",
    "links",
    "bookmarks",
    "headings",
    "lists",
    "tables",
    "code",
    "floating-previews",
    "cleanup",
    "duplicate-headings",
    "pages",
    "title",
    "checks",
)


ALIGNED_TRANSFORMS = (
    "literal-text",
    "line-breaks",
    "images",
    "links",
    "bookmarks",
    "headings",
    "lists",
    "tables",
    "code",
    "floating-previews",
)


FATAL_ERRORS = (PackageError, OSError, MemoryError)


_MARKER_BODY = (
    r"(?:[CHILNT]\d+(?:BEGIN|END|ALT)?|A\d+ANCHOR|P\d+PARAGRAPH|E\d+EMPTYROW|D\d+DATA"
    r"|LINK\d+|LITERAL(?:LESS|GREATER|AMPERSAND|BACKTICK|BACKSLASH|HASH|OPENBRACKET"
    r"|CLOSEBRACKET|BREAK))?"
)


_REASON_LINES = 8


_DETAIL_LIMIT = 300


# --explain: the kinds of Word items that get features, and how many values a line shows.
_KINDS = {
    "heading": "headings",
    "list item": "list items",
    "link": "links",
    "bookmark": "bookmarks",
    "image": "images",
    "table": "tables",
}
_EXPLAIN_VALUES = 5


def warning(message: str, findings: list[str]) -> None:
    findings.append(message)
    print(f"docx-to-md: warning: {message}", file=sys.stderr)


def describe(error: BaseException) -> str:
    """Name unexpected failures by type; converter errors already explain themselves."""
    text = re.sub(r"\s+", " ", str(error)).strip()
    if not isinstance(error, ConversionError):
        text = f"{type(error).__name__}: {text}" if text else type(error).__name__
    return text if len(text) <= _DETAIL_LIMIT else text[: _DETAIL_LIMIT - 3] + "..."


def marker_pattern(prefix: str) -> re.Pattern[str]:
    """Exactly the alignment markers a conversion inserts, so adjacent text is never eaten."""
    return re.compile(re.escape(prefix) + _MARKER_BODY)


def category(message: str, prefix: str = "") -> str:
    """A generic reason for the summary: no quoted document text, numbers, or markers."""
    text = marker_pattern(prefix).sub("", message) if prefix else message
    text = re.sub(r"(?<![\w'])'[^'\n]*'(?![\w'])|\"[^\"\n]*\"", "'...'", text)
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
class ExplainEvent:
    """One skipped item or problem, as --explain sees it: the Word items and mismatch shape."""

    name: str
    reason: str
    items: tuple[str, ...] = ()
    shape: dict[str, str] = field(default_factory=dict[str, str])


@dataclass
class TransformReport:
    """Record which layers on the MarkItDown baseline were applied, skipped, and why."""

    findings: list[str] = field(default_factory=list[str])
    records: dict[str, TransformRecord] = field(
        default_factory=lambda: {name: TransformRecord() for name in TRANSFORMS}
    )
    baseline_images: int = 0
    buffered: bool = False
    pending: list[str] = field(default_factory=list[str])
    prefix: str = ""
    # --explain: content-free features of each Word item by marker, and each skip's items.
    explaining: bool = False
    items: dict[str, tuple[str, dict[str, str]]] = field(
        default_factory=dict[str, tuple[str, dict[str, str]]]
    )
    events: list[ExplainEvent] = field(default_factory=list[ExplainEvent])
    census: Counter[str] = field(default_factory=Counter[str])
    field_census: Counter[str] = field(default_factory=Counter[str])

    def warn(self, message: str) -> None:
        if self.prefix:
            message = marker_pattern(self.prefix).sub("<marker>", message)
        if self.buffered:
            self.pending.append(message)
        else:
            warning(message, self.findings)

    def applied(self, name: str, detail: str = "", count: int = 1) -> None:
        if count:
            self.records[name].applied[detail] += count

    def complete(self, name: str) -> None:
        self.records[name].complete = True

    def skipped(
        self,
        name: str,
        reason: str,
        detail: str | None = None,
        *,
        items: Iterable[str] = (),
        shape: Mapping[str, str] | None = None,
    ) -> None:
        """Count a skipped item under a generic reason; the warning names the item.

        *items* are the markers of the Word items involved and *shape* describes the mismatch,
        both without document text; only --explain uses them.
        """
        self.records[name].skipped[reason] += 1
        if self.explaining:
            self.events.append(ExplainEvent(name, reason, tuple(items), dict(shape or {})))
        self.warn(f"{name}: {detail or reason}")

    def skipped_error(
        self,
        name: str,
        error: BaseException,
        where: str,
        consequence: str = "",
        *,
        items: Iterable[str] = (),
        shape: Mapping[str, str] | None = None,
    ) -> None:
        """Skip one item because of *error*; the summary shows only its generic category."""
        text = describe(error)
        self.skipped(
            name,
            category(text, self.prefix) + consequence,
            f"{where}: {text}{consequence}",
            items=items,
            shape=shape,
        )

    def describe_item(self, token: str, kind: str, features: Mapping[str, str]) -> None:
        """Record the content-free features of the Word item marked *token*, for --explain."""
        if self.explaining:
            self.items[token] = (kind, dict(features))

    def failed(self, name: str, error: BaseException) -> None:
        text = describe(error)
        record = self.records[name]
        record.failure = record.failure or text
        self.warn(f"{name}: failed and was not applied: {text}")

    def unavailable(self, names: Iterable[str], reason: str) -> None:
        for name in names:
            record = self.records[name]
            if record.unavailable is None:
                record.unavailable = reason

    def not_requested(self, name: str, hint: str) -> None:
        record = self.records[name]
        record.requested = False
        record.unavailable = hint

    def status(self, name: str) -> str:
        return self.records[name].status()

    def child(self) -> TransformReport:
        """A buffered report for an attempt that may be discarded."""
        return TransformReport(buffered=True, prefix=self.prefix, explaining=self.explaining)

    def merge(self, child: TransformReport) -> None:
        for name, record in child.records.items():
            target = self.records[name]
            target.applied.update(record.applied)
            target.skipped.update(record.skipped)
            target.failure = target.failure or record.failure
            target.unavailable = target.unavailable or record.unavailable
            target.complete |= record.complete
            target.requested &= record.requested
        self.baseline_images += child.baseline_images
        self.items.update(child.items)
        self.events.extend(child.events)
        self.census.update(child.census)
        self.field_census.update(child.field_census)
        for message in child.pending:
            self.warn(message)
        child.pending.clear()

    def summary(self) -> list[str]:
        """Counts and generic reasons only; the warnings name individual items."""
        header = "docx-to-md: transforms (baseline: MarkItDown"
        if self.baseline_images:
            header += f"; {self.baseline_images} image(s) at MarkItDown positions"
        lines = [header + ")"]
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

    def explanation(self) -> list[str]:
        """--explain: what the items behind each reason have in common, without document text.

        For each skip reason with Word items, each feature lists its values as N/M: N of the
        M items with that value were skipped for this reason. Mismatch shapes are plain counts.
        """
        if not self.explaining:
            return []
        kinds = Counter(kind for kind, _features in self.items.values())
        document = [f"{name} {count}" for name, count in self.census.items()]
        document += [f"{label} {kinds[kind]}" for kind, label in _KINDS.items() if kinds[kind]]
        lines = [
            "docx-to-md: explain (fixed labels and counts only; no document text)",
            "  document: " + ("; ".join(document) or "not read"),
        ]
        if self.field_census:
            lines.append("  fields: " + _histogram(self.field_census))
        reasons: dict[tuple[str, str], list[ExplainEvent]] = {}
        for event in self.events:
            if event.items or event.shape:
                reasons.setdefault((event.name, event.reason), []).append(event)
        order = {name: index for index, name in enumerate(TRANSFORMS)}

        def rank(key: tuple[str, str]) -> tuple[int, int, str]:
            return order[key[0]], -len(reasons[key]), key[1]

        for name, reason in sorted(reasons, key=rank):
            events = reasons[(name, reason)]
            lines.append(f"  {name}: {reason} ({self.records[name].skipped[reason]})")
            shapes = dict.fromkeys(key for event in events for key in event.shape)
            for key in shapes:
                values = Counter(event.shape[key] for event in events if key in event.shape)
                lines.append(f"    {key}: {_histogram(values)}")
            lines += self._item_lines(events)
        return lines

    def _item_lines(self, events: list[ExplainEvent]) -> list[str]:
        skipped: dict[str, list[dict[str, str]]] = {}
        for token in dict.fromkeys(token for event in events for token in event.items):
            if token in self.items:
                kind, features = self.items[token]
                skipped.setdefault(kind, []).append(features)
        lines: list[str] = []
        for kind, bad in skipped.items():
            every = [features for other, features in self.items.values() if other == kind]
            lines.append(f"    {_KINDS[kind]}: {len(bad)} of {len(every)}")
            for feature in dict.fromkeys(name for features in every for name in features):
                totals = Counter(features.get(feature, "not recorded") for features in every)
                if len(totals) < 2:
                    continue
                counts = Counter(features.get(feature, "not recorded") for features in bad)
                ranked = sorted((-counts[value], -total, value) for value, total in totals.items())
                shown = [f"{value} {-skips}/{-total}" for skips, total, value in ranked]
                if len(shown) > _EXPLAIN_VALUES:
                    shown[_EXPLAIN_VALUES:] = [f"{len(shown) - _EXPLAIN_VALUES} more"]
                lines.append(f"      {feature}: " + "; ".join(shown))
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


def _histogram(counts: Counter[str]) -> str:
    shown = [f"{value} {count}" for value, count in counts.most_common()]
    if len(shown) > _EXPLAIN_VALUES + 1:
        shown[_EXPLAIN_VALUES:] = [f"{len(shown) - _EXPLAIN_VALUES} more"]
    return "; ".join(shown)


@contextmanager
def transform(report: TransformReport, name: str) -> Generator[None, None, None]:
    """Skip a failed layer; the baseline and the other layers remain."""
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
    source: SourceDocument | None = None,
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
        "publication": "per-path replacement with rollback, not multi-artifact atomicity",
        "warnings": findings,
        "statistics": stats,
        "baseline": "MarkItDown",
        "baseline_images": report.baseline_images if report is not None else 0,
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
        }
        if source is not None
        else dict[str, dict[str, str]](),
        "error": error,
    }
    if report is not None and report.explaining:
        document["explain"] = report.explanation()
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=".diagnostics-",
        suffix=".json",
        dir=work,
        delete=False,
    ) as stream:
        stream.write(json.dumps(document, indent=2, ensure_ascii=True) + "\n")
        prepared = Path(stream.name)
    prepared.replace(work / "diagnostics.json")
