"""What a command prints.

Warnings and errors go to standard error as ``<command>: warning: [<where>: ]<message>``, where
*where* is a file, with its line when it is known. By default a command shows the first
`EXAMPLES` warnings of each kind and then how many more there were; ``--verbose`` shows every
warning and the details of each step, and ``--quiet`` only errors. On a terminal, a status line
names the running step, and ``warning`` and ``error`` are colored, unless NO_COLOR is set or
TERM is dumb. The result goes to standard output.

The command's console is active while it runs, so that shared code reports through `active`.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import threading
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Literal, TextIO

Verbosity = Literal["quiet", "normal", "verbose"]

# How many warnings of each kind a command shows without --verbose.
EXAMPLES = 3

_COLORS = {"warning": "\033[1;33m", "error": "\033[1;31m"}
_RESET = "\033[0m"
_CLEAR_LINE = "\r\033[K"

_active: ContextVar[Console | None] = ContextVar("console", default=None)


def add_verbosity_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-v",
        "--verbose",
        dest="verbosity",
        action="store_const",
        const="verbose",
        default="normal",
        help="Show every warning, and the details of each step",
    )
    group.add_argument(
        "-q",
        "--quiet",
        dest="verbosity",
        action="store_const",
        const="quiet",
        help="Show only errors",
    )


def shown(path: str | os.PathLike[str]) -> str:
    """*path* as messages show it: relative to the working directory when it is inside it."""
    value = Path(path)
    if value.is_absolute():
        try:
            return str(value.relative_to(Path.cwd()))
        except ValueError:
            pass
    return str(value)


def plural(count: int, noun: str, nouns: str = "") -> str:
    """*count* and *noun*, or *nouns* (by default *noun* with an s) unless *count* is 1."""
    return f"{count} {noun if count == 1 else nouns or noun + 's'}"


class Console:
    """The messages of one run of *command*."""

    def __init__(
        self, command: str, verbosity: Verbosity = "normal", *, stream: TextIO | None = None
    ) -> None:
        self.command = command
        self.verbosity: Verbosity = verbosity
        self._stream = stream
        # Warnings by kind, shown or not.
        self.counts: Counter[str] = Counter()
        self._status = ""
        # Background threads may report too; each message is written whole.
        self._lock = threading.RLock()

    @property
    def stream(self) -> TextIO:
        return sys.stderr if self._stream is None else self._stream

    @property
    def terminal(self) -> bool:
        try:
            return self.stream.isatty() and os.environ.get("TERM") != "dumb"
        except AttributeError, ValueError:
            return False

    def warning(self, message: str, *, kind: str = "", where: str = "") -> None:
        """Report a problem. Without --verbose, only the first few of each *kind* are shown."""
        with self._lock:
            self.counts[kind] += 1
            if self.verbosity == "quiet":
                return
            if self.verbosity == "normal" and kind and self.counts[kind] > EXAMPLES:
                return
            self._write(self._message("warning", message, where))

    def error(self, message: str, *, where: str = "") -> None:
        self._write(self._message("error", message, where))

    def note(self, message: str) -> None:
        """Say something that every run shows, unless --quiet."""
        if self.verbosity != "quiet":
            self._write(f"{self.command}: {message}")

    def block(self, lines: list[str]) -> None:
        """Print *lines*, such as a summary, as they are, unless --quiet."""
        if self.verbosity != "quiet":
            with self._lock:
                for line in lines:
                    self._write(line)

    def detail(self, message: str) -> None:
        """Say something that only --verbose shows: a step's details, or what worked out."""
        if self.verbosity == "verbose":
            self._write(f"{self.command}: {message}")

    def status(self, text: str) -> None:
        """Name the running step on a terminal, until the next step or the end of the run."""
        with self._lock:
            if self.verbosity == "quiet" or not self.terminal:
                return
            line = f"{self.command}: {text}"
            width = shutil.get_terminal_size().columns - 1
            self._status = line if len(line) <= width else line[: max(width - 3, 0)] + "..."
            self.stream.write(_CLEAR_LINE + self._status)
            self.stream.flush()

    def suppressed(self) -> list[tuple[str, int]]:
        """The kinds of warnings not all shown, with how many were not, most first."""
        if self.verbosity != "normal":
            return []
        more = [
            (kind, count - EXAMPLES)
            for kind, count in self.counts.items()
            if kind and count > EXAMPLES
        ]
        return sorted(more, key=lambda item: (-item[1], item[0]))

    def finish(self, *, counted: bool = False) -> None:
        """Clear the status line; count the warnings not shown, unless a summary *counted* them."""
        with self._lock:
            self._clear_status()
            more = self.suppressed()
            if not counted:
                for kind, count in more:
                    self._write(self._message("warning", f"and {count} more: {kind}", ""))
            if more:
                self.note("--verbose shows every warning")

    def result(self, message: str) -> None:
        """Print the command's result on standard output, unless --quiet."""
        if self.verbosity != "quiet":
            with self._lock:
                self._clear_status()
                print(message, flush=True)

    def _message(self, label: str, message: str, where: str) -> str:
        if self.terminal and not os.environ.get("NO_COLOR"):
            label = f"{_COLORS[label]}{label}{_RESET}"
        prefix = f"{self.command}: {label}: {where}: " if where else f"{self.command}: {label}: "
        return prefix + message

    def _clear_status(self) -> None:
        if self._status:
            self.stream.write(_CLEAR_LINE)
            self.stream.flush()
            self._status = ""

    def _write(self, line: str) -> None:
        with self._lock:
            stream = self.stream
            if self._status:
                stream.write(_CLEAR_LINE)
            stream.write(line + "\n")
            if self._status:
                stream.write(self._status)
            stream.flush()


def active() -> Console:
    """The running command's console; outside a command, a new one, which shows every warning."""
    console = _active.get()
    return Console("md-doc-converter") if console is None else console


@contextmanager
def activated(console: Console) -> Generator[Console]:
    """Make *console* the active one while the command runs."""
    token = _active.set(console)
    try:
        yield console
    finally:
        _active.reset(token)
