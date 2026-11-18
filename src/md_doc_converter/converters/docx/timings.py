"""How long a Word conversion takes, for the end of its report with --verbose.

A conversion's phases follow one another, from reading the document to writing the files. The
drawings of the document's body convert in the background from the start, and the phases and
that conversion run programs, such as Pandoc and LibreOffice. `timing` makes a conversion's
timings active, so that running a program, or converting drawings in the background, adds to
them, on any thread that runs in a copy of the conversion's context.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from md_doc_converter.console import plural

# The extensions that Windows runs a program by, which a program's name leaves out.
_PROGRAM_EXTENSIONS = frozenset({".exe", ".com", ".bat", ".cmd"})


def _seconds(value: float) -> str:
    milliseconds = round(value * 1000)
    return f"{milliseconds} ms" if milliseconds < 1000 else f"{value:.1f} s"


def _program(command: str) -> str:
    """The name of the program *command*, as on every system: pandoc, not pandoc.exe."""
    path = Path(command)
    if sys.platform == "win32" and path.suffix.lower() in _PROGRAM_EXTENSIONS:
        return path.stem
    return path.name


@dataclass
class Timings:
    """How long a conversion took: in all, in each phase, in converting drawings in the
    background, and in running each program, with how many times it ran."""

    total: float = 0.0
    phases: dict[str, float] = field(default_factory=dict[str, float])
    background: float | None = None
    programs: dict[str, float] = field(default_factory=dict[str, float])
    runs: Counter[str] = field(default_factory=Counter[str])
    _phase: str | None = field(default=None, init=False, repr=False, compare=False)
    _since: float = field(default=0.0, init=False, repr=False, compare=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def phase(self, name: str | None) -> None:
        """End the running phase, if any, and start *name*, unless it is None."""
        now = time.perf_counter()
        if self._phase is not None:
            self.phases[self._phase] = self.phases.get(self._phase, 0.0) + now - self._since
        self._phase, self._since = name, now

    def ran(self, program: str, seconds: float) -> None:
        with self._lock:
            self.programs[program] = self.programs.get(program, 0.0) + seconds
            self.runs[program] += 1

    def converted(self, seconds: float) -> None:
        with self._lock:
            self.background = (self.background or 0.0) + seconds

    def lines(self) -> list[str]:
        """The time in all, in each phase in turn, and in converting drawings in the background;
        then the time in each program, the longest first, with how many times it ran."""
        line = f"time: {_seconds(self.total)}"
        if self.phases:
            line += ": " + ", ".join(
                f"{name} {_seconds(spent)}" for name, spent in self.phases.items()
            )
        if self.background is not None:
            line += f"; and in the background, converting drawings {_seconds(self.background)}"
        lines = [line]
        if self.programs:
            ranked = sorted(self.programs.items(), key=lambda item: (-item[1], item[0]))
            lines.append(
                "time in programs: "
                + ", ".join(
                    f"{name} {_seconds(spent)} in {plural(self.runs[name], 'run')}"
                    for name, spent in ranked
                )
            )
        return lines


_active: ContextVar[Timings | None] = ContextVar("timings", default=None)


@contextmanager
def timing(timings: Timings) -> Generator[Timings]:
    """Make *timings* the running conversion's in the block, and add the block's time to it."""
    token = _active.set(timings)
    start = time.perf_counter()
    try:
        yield timings
    finally:
        timings.phase(None)
        timings.total += time.perf_counter() - start
        _active.reset(token)


@contextmanager
def _adding(add: Callable[[Timings, float], None]) -> Generator[None]:
    timings = _active.get()
    start = time.perf_counter()
    try:
        yield
    finally:
        if timings is not None:
            add(timings, time.perf_counter() - start)


def running(command: str) -> AbstractContextManager[None]:
    """Time the block as a run of the program *command*, for the running conversion, if any."""
    program = _program(command)
    return _adding(lambda timings, seconds: timings.ran(program, seconds))


def converting_in_background() -> AbstractContextManager[None]:
    """Time the block as converting drawings in the background, for the running conversion."""
    return _adding(Timings.converted)
