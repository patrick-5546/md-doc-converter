"""The command line, run as `python -m md_doc_converter` with fake tools and a fake toolchain."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from md_doc_converter.toolchain.environment import PANDOC_VERSION, Toolchain
from md_doc_converter.toolchain.install import PANDOC
from tests.support.fake_tools import FakeTools

# A quote for file names: Windows allows single quotes in them, but not double ones.
QUOTE = "'" if sys.platform == "win32" else '"'


@dataclass(frozen=True)
class CommandLine:
    """Runs `python -m md_doc_converter` from the workspace.

    The fake tools come first on PATH and the fake toolchain is MD_DOC_CONVERTER_HOME; no other
    MD_DOC_CONVERTER_ or FAKE_TOOL_ variable reaches the command unless a test sets it.
    """

    workspace: Path
    bin: Path
    home: Path

    def __call__(
        self,
        *arguments: str,
        cwd: Path | None = None,
        environment: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run the command with *environment* added; with *check*, assert that it succeeds."""
        selected = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("MD_DOC_CONVERTER_", "FAKE_TOOL_"))
        }
        selected["PATH"] = f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"
        selected["MD_DOC_CONVERTER_HOME"] = str(self.home)
        selected.update(environment or {})
        result = subprocess.run(
            [sys.executable, "-m", "md_doc_converter", *arguments],
            cwd=cwd or self.workspace,
            env=selected,
            capture_output=True,
            text=True,
            check=False,
        )
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result


@pytest.fixture
def tools(fake_tools: FakeTools, tmp_path: Path) -> Toolchain:
    """A complete fake toolchain, whose pandoc records its calls with the other fakes."""
    return fake_tools.toolchain(tmp_path / "home")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """The command's working directory, with one document: docs/input.md."""
    folder = tmp_path / "workspace"
    (folder / "docs").mkdir(parents=True)
    (folder / "docs" / "input.md").write_text("# Example\n", encoding="utf-8")
    return folder


@pytest.fixture
def docs(workspace: Path) -> Path:
    return workspace / "docs"


@pytest.fixture
def run_cli(fake_tools: FakeTools, tools: Toolchain, workspace: Path) -> CommandLine:
    """The command line, with fake pandoc, node and npm on PATH and the fake toolchain."""
    return CommandLine(workspace, fake_tools.write(), tools.root)


@pytest.fixture
def quoted_source(docs: Path) -> Path:
    """A document whose name has a space, an apostrophe and quotes."""
    source = docs / f"input file's {QUOTE}review{QUOTE}.md"
    source.write_text("# Example\n", encoding="utf-8")
    return source


@pytest.fixture
def table(docs: Path) -> Path:
    """A CSV file whose name has a space, an apostrophe and quotes."""
    source = docs / f"signal table's {QUOTE}review{QUOTE}.csv"
    source.write_text("Name,Value\nsignal,one\n", encoding="utf-8")
    return source


def test_version_and_help(run_cli: CommandLine) -> None:
    assert re.search(r"^md-doc-converter \d+\.\d+\.\d+", run_cli("--version").stdout)
    assert "md-to-pdf" in run_cli().stdout
    result = run_cli("not-a-command", check=False)
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_render_defaults_next_to_files_and_inside_folders(
    run_cli: CommandLine, fake_tools: FakeTools, tools: Toolchain, workspace: Path, docs: Path
) -> None:
    result = run_cli("md-to-html", "docs/input.md")
    html = docs / "input.html"
    # Paths inside the working directory are shown relative to it, as the system writes them.
    shown_input, shown_html = Path("docs", "input.md"), Path("docs", "input.html")
    assert result.stdout == f"rendered {shown_input} -> {shown_html}\n"
    assert result.stderr == f"md-to-html: rendering {shown_input}\n"
    assert "# Example" in html.read_text(encoding="utf-8")
    run_cli("md-to-pdf", "docs")
    assert (docs / "docs.pdf").read_bytes() == b"%PDF-fake"
    (docs / "docs.pdf").unlink()
    run_cli("md-to-pdf", ".", cwd=docs)
    assert (docs / "docs.pdf").read_bytes() == b"%PDF-fake"
    assert sorted(path.name for path in docs.iterdir()) == ["docs.pdf", "input.html", "input.md"]
    assert sorted(path.name for path in workspace.iterdir()) == ["docs"]
    pandoc, node = fake_tools.calls("pandoc"), fake_tools.calls("node")
    for call in pandoc + node:
        scratch = Path(str(call["cwd"]))
        assert not scratch.exists()
        assert not scratch.is_relative_to(workspace)
    printer = node[0]["arguments"]
    assert isinstance(printer, list)
    assert printer[printer.index("--node-modules") + 1] == str(tools.node_modules)
    environment = pandoc[0]["environment"]
    assert isinstance(environment, dict)
    assert environment["PUPPETEER_CACHE_DIR"] == str(tools.puppeteer_cache)
    assert str(environment["PATH"]).startswith(str(tools.bin_dir))
    arguments = pandoc[0]["arguments"]
    assert isinstance(arguments, list)
    # Pandoc embeds the stylesheets: KaTeX's from the work directory, which katex.lua adds
    # to a document with math, and the inlined GitHub Markdown and shared styles.
    assert "--css" not in arguments
    katex = Path(str(environment["MD_DOC_CONVERTER_KATEX_CSS"]))
    assert katex.is_absolute()
    assert katex.name == "katex.css"
    styles = pandoc[0]["styles"]
    assert isinstance(styles, str)
    assert styles.index(".markdown-body { color: #1f2328; }") < styles.index(".table-cols")
    assert environment["MD_DOC_CONVERTER_NODE_MODULES"] == str(tools.node_modules)
    # The fake PATH also has a pandoc; the toolchain's pinned one comes first.
    assert {call["path"] for call in pandoc} == {str(tools.pandoc)}


@pytest.mark.parametrize(
    ("output", "absolute"),
    [
        pytest.param(
            Path(f"review output's {QUOTE}final{QUOTE}") / f"chosen {QUOTE}name{QUOTE}.pdf",
            False,
            id="relative-output",
        ),
        pytest.param(Path("absolute output") / "chosen.pdf", True, id="absolute-output"),
    ],
)
def test_paths_preserve_spaces_quotes_and_apostrophes(
    output: Path,
    absolute: bool,
    run_cli: CommandLine,
    quoted_source: Path,
    workspace: Path,
    tmp_path: Path,
) -> None:
    if absolute:
        output = tmp_path / output
    run_cli("md-to-pdf", str(quoted_source.relative_to(workspace)), "-o", str(output))
    # A relative output is relative to the working directory, the workspace.
    assert (workspace / output).read_bytes() == b"%PDF-fake"


def test_absolute_source_paths_preserve_spaces_quotes_and_apostrophes(
    run_cli: CommandLine, quoted_source: Path
) -> None:
    run_cli("md-to-html", str(quoted_source))
    assert quoted_source.with_suffix(".html").is_file()


def test_wrong_output_suffix_is_rejected_before_rendering(
    run_cli: CommandLine, fake_tools: FakeTools, workspace: Path
) -> None:
    result = run_cli("md-to-pdf", "docs/input.md", "-o", "out.html", check=False)
    assert result.returncode == 1
    assert "Output must end in .pdf" in result.stderr
    assert fake_tools.calls() == []
    assert not (workspace / "out.html").exists()


def test_missing_toolchain_fails_without_rendering(
    run_cli: CommandLine, fake_tools: FakeTools, docs: Path, tmp_path: Path
) -> None:
    result = run_cli(
        "md-to-pdf",
        "docs/input.md",
        environment={"MD_DOC_CONVERTER_HOME": str(tmp_path / "not installed")},
        check=False,
    )
    assert result.returncode == 1
    assert "rendering prerequisites are missing" in result.stderr
    assert "md-doc-converter setup" in result.stderr
    assert "md-doc-converter doctor" in result.stderr
    assert fake_tools.calls() == []
    assert not (docs / "input.pdf").exists()


def test_failed_render_keeps_the_previous_file_and_requested_intermediates(
    run_cli: CommandLine, docs: Path, tmp_path: Path
) -> None:
    previous = docs / "input.pdf"
    previous.write_bytes(b"previous")
    kept = tmp_path / "kept"
    result = run_cli(
        "md-to-pdf",
        "docs/input.md",
        "--keep-intermediates",
        str(kept),
        environment={"FAKE_TOOL_FAIL": "node"},
        check=False,
    )
    assert result.returncode == 1
    assert "node failed with exit status 3" in result.stderr
    assert previous.read_bytes() == b"previous"
    assert sorted(path.name for path in docs.iterdir()) == ["input.md", "input.pdf"]
    kept_document = json.loads((kept / "document" / "input.json").read_text(encoding="utf-8"))
    assert kept_document["blocks"][0]["c"][2] == [{"t": "Str", "c": "Example"}]
    assert (kept / "page-styles.html").is_file()
    assert (kept / "render" / "input.html").is_file()


def test_gitignore_filtering_is_automatic_inside_git_work_trees(
    run_cli: CommandLine, fake_tools: FakeTools, workspace: Path, docs: Path
) -> None:
    (docs / "ignored.md").write_text("# Ignored\n", encoding="utf-8")
    (workspace / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
    run_cli("md-to-html", "docs")
    assert "Ignored" not in str(fake_tools.calls("pandoc")[-1]["input"])
    run_cli("md-to-html", "docs", "--no-respect-gitignore")
    assert "Ignored" in str(fake_tools.calls("pandoc")[-1]["input"])


def test_respect_gitignore_outside_a_git_work_tree_warns_only_when_requested(
    run_cli: CommandLine, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "page.md").write_text("# Page\n", encoding="utf-8")
    quiet = run_cli("md-to-html", str(outside))
    assert "WARNING" not in quiet.stderr
    explicit = run_cli("md-to-html", str(outside), "--respect-gitignore")
    assert "--respect-gitignore had no effect" in explicit.stderr


def test_csv_to_md_writes_beside_the_source_by_default(
    run_cli: CommandLine, table: Path, workspace: Path
) -> None:
    run_cli("csv-to-md", str(table.relative_to(workspace)))
    converted = table.parent / f"{table.stem}.md"
    assert "| signal | one |" in converted.read_text(encoding="utf-8")
    assert "md-doc-converter (csv-to-md)" in converted.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("output", "absolute"),
    [
        pytest.param(Path("converted"), False, id="relative"),
        pytest.param(Path(f"converted output's {QUOTE}review{QUOTE}"), False, id="quoted"),
        pytest.param(Path("absolute output"), True, id="absolute"),
    ],
)
def test_csv_to_md_resolves_output_paths_from_the_working_directory(
    output: Path,
    absolute: bool,
    run_cli: CommandLine,
    table: Path,
    workspace: Path,
    tmp_path: Path,
) -> None:
    if absolute:
        output = tmp_path / output
    run_cli("csv-to-md", str(table.relative_to(workspace)), "-o", str(output))
    converted = workspace / output / f"{table.stem}.md"
    assert "| signal | one |" in converted.read_text(encoding="utf-8")
    assert "md-doc-converter (csv-to-md)" in converted.read_text(encoding="utf-8")


def test_doctor_reports_through_the_command_line(
    run_cli: CommandLine, tools: Toolchain, tmp_path: Path
) -> None:
    ready = run_cli("doctor")
    assert "Ready:" in ready.stdout
    assert f"{PANDOC_VERSION} ({tools.pandoc})" in ready.stdout
    empty = {"MD_DOC_CONVERTER_HOME": str(tmp_path / "empty")}
    missing = run_cli("doctor", environment=empty, check=False)
    assert missing.returncode == 1
    assert "-> Run `md-doc-converter setup`" in missing.stdout


def test_setup_reports_through_the_command_line(
    run_cli: CommandLine, fake_tools: FakeTools, tmp_path: Path
) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / PANDOC.file).write_bytes(b"not the pinned archive")
    empty = {"MD_DOC_CONVERTER_HOME": str(tmp_path / "empty")}
    result = run_cli("setup", "--downloads", str(downloads), environment=empty, check=False)
    assert result.returncode == 1
    assert f"{PANDOC.file} has SHA-256" in result.stderr
    assert f"pandoc: using {downloads / PANDOC.file}" in result.stdout
    assert fake_tools.calls("npm") == []
    assert list((tmp_path / "empty" / "bin").iterdir()) == []
