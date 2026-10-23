"""What a command prints: warnings by kind, errors, details, the status line and the result."""

from __future__ import annotations

import io
import os
import threading
from pathlib import Path
from typing import override

import pytest

from md_doc_converter import console as _console
from md_doc_converter.console import Console, Verbosity


class Terminal(io.StringIO):
    """Standard error on a terminal."""

    @override
    def isatty(self) -> bool:
        return True


def run(
    verbosity: Verbosity, stream: io.StringIO | None = None, *, warnings: int = 5
) -> tuple[Console, io.StringIO]:
    """A console that reported *warnings* of one kind, one of another and an unkinded one."""
    stream = io.StringIO() if stream is None else stream
    console = Console("md-to-html", verbosity, stream=stream)
    for number in range(1, warnings + 1):
        console.warning(f"broken anchor '#a{number}'", kind="broken anchor", where="a.md")
    console.warning("missing image 'b.png'", kind="missing image", where="b.md:3")
    console.warning("--respect-gitignore had no effect")
    console.detail("reading a.md")
    console.note("intermediates kept in kept")
    return console, stream


def test_a_run_shows_the_first_warnings_of_each_kind_then_counts_the_rest() -> None:
    console, stream = run("normal")
    console.finish()

    assert stream.getvalue().splitlines() == [
        "md-to-html: warning: a.md: broken anchor '#a1'",
        "md-to-html: warning: a.md: broken anchor '#a2'",
        "md-to-html: warning: a.md: broken anchor '#a3'",
        "md-to-html: warning: b.md:3: missing image 'b.png'",
        "md-to-html: warning: --respect-gitignore had no effect",
        "md-to-html: intermediates kept in kept",
        "md-to-html: warning: and 2 more: broken anchor",
        "md-to-html: --verbose shows every warning",
    ]


def test_the_more_counts_list_the_most_common_kind_first() -> None:
    console = Console("docx-to-md", stream=(stream := io.StringIO()))
    for kind, count in (("rare", 4), ("common", 9), ("also rare", 4)):
        for _ in range(count):
            console.warning("x", kind=kind)
    console.finish()

    assert stream.getvalue().splitlines()[-4:] == [
        "docx-to-md: warning: and 6 more: common",
        "docx-to-md: warning: and 1 more: also rare",
        "docx-to-md: warning: and 1 more: rare",
        "docx-to-md: --verbose shows every warning",
    ]


def test_a_summary_that_counts_each_kind_leaves_only_the_hint() -> None:
    console, stream = run("normal")
    console.finish(counted=True)

    assert stream.getvalue().splitlines()[-1] == "md-to-html: --verbose shows every warning"
    assert "more" not in stream.getvalue()


def test_verbose_shows_every_warning_and_the_details() -> None:
    console, stream = run("verbose")
    console.finish()

    lines = stream.getvalue().splitlines()
    assert sum("broken anchor" in line for line in lines) == 5
    assert "md-to-html: reading a.md" in lines
    assert not any("more" in line for line in lines)


def test_quiet_shows_only_errors_and_no_result(capsys: pytest.CaptureFixture[str]) -> None:
    console, stream = run("quiet")
    console.error("cannot prepare a.md")
    console.finish()
    console.result("rendered a.md -> a.html")

    assert stream.getvalue() == "md-to-html: error: cannot prepare a.md\n"
    assert capsys.readouterr().out == ""
    assert console.counts["broken anchor"] == 5


def test_the_result_goes_to_standard_output(capsys: pytest.CaptureFixture[str]) -> None:
    Console("md-to-html", stream=io.StringIO()).result("rendered a.md -> a.html")

    assert capsys.readouterr().out == "rendered a.md -> a.html\n"


def test_on_a_terminal_the_status_line_gives_way_to_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    terminal = Terminal()
    console = Console("docx-to-md", stream=terminal)

    console.status("converting 2 drawings")
    console.warning("drawing kept as EMF", kind="drawing")
    console.finish()

    assert terminal.getvalue() == (
        "\r\033[Kdocx-to-md: converting 2 drawings"
        "\r\033[Kdocx-to-md: \033[1;33mwarning\033[0m: drawing kept as EMF\n"
        "docx-to-md: converting 2 drawings"
        "\r\033[K"
    )


@pytest.mark.parametrize(
    ("variable", "value", "status"),
    [("NO_COLOR", "1", True), ("TERM", "dumb", False)],
    ids=["no-color", "dumb-terminal"],
)
def test_no_color_and_dumb_terminals_get_plain_messages(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, *, status: bool
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv(variable, value)
    terminal = Terminal()
    console = Console("md-to-pdf", stream=terminal)

    console.status("printing the PDF")
    console.error("node failed with exit status 1")

    assert "\033[1;31m" not in terminal.getvalue()
    assert "md-to-pdf: error: node failed with exit status 1\n" in terminal.getvalue()
    assert ("printing the PDF" in terminal.getvalue()) is status


def test_without_a_terminal_there_is_no_status_line() -> None:
    stream = io.StringIO()
    console = Console("md-to-pdf", stream=stream)

    console.status("printing the PDF")
    console.finish()

    assert stream.getvalue() == ""


def test_a_long_status_is_cut_to_the_terminal_width(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr(_console.shutil, "get_terminal_size", lambda: os.terminal_size((20, 5)))
    terminal = Terminal()

    Console("md-to-html", stream=terminal).status("preparing docs/a-very-long-name.md")

    # At most 19 characters, so the line never wraps.
    assert terminal.getvalue() == "\r\033[Kmd-to-html: prep..."


def test_messages_from_threads_stay_whole() -> None:
    stream = io.StringIO()
    console = Console("docx-to-md", "verbose", stream=stream)

    def report(thread: int) -> None:
        for number in range(200):
            console.warning(f"thread {thread} message {number}", kind=f"kind {thread}")

    threads = [threading.Thread(target=report, args=(thread,)) for thread in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1600
    assert all(line.startswith("docx-to-md: warning: thread ") for line in lines)


def test_paths_inside_the_working_directory_are_shown_relative_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert _console.shown(tmp_path / "docs" / "a.md") == str(Path("docs", "a.md"))
    assert _console.shown(Path("docs", "a.md")) == str(Path("docs", "a.md"))
    assert _console.shown(tmp_path.parent / "b.md") == str(tmp_path.parent / "b.md")


def test_counts_have_real_plurals() -> None:
    assert _console.plural(1, "page") == "1 page"
    assert _console.plural(259, "table") == "259 tables"
    assert _console.plural(0, "image reference") == "0 image references"
    assert _console.plural(2, "child", "children") == "2 children"


def test_shared_code_reports_through_the_running_commands_console() -> None:
    stream = io.StringIO()
    running = Console("md-to-pdf", "quiet", stream=stream)

    with _console.activated(running):
        assert _console.active() is running
    outside = _console.active()

    assert outside is not running
    assert (outside.command, outside.verbosity) == ("md-doc-converter", "normal")
