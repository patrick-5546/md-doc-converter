"""The docs name the versions, limits, commands and options that the code has."""

from __future__ import annotations

import re
import subprocess
import tomllib

import pytest

from md_doc_converter.__main__ import COMMANDS
from md_doc_converter.converters import packages
from md_doc_converter.converters.docx import cli as docx_cli
from md_doc_converter.rendering.pipeline import PDF_TIMEOUT_DEFAULT
from md_doc_converter.toolchain.environment import NODE_MINIMUM, PANDOC_VERSION
from tests.support.project import PROJECT_ROOT

VERSION = r"\d+(?:\.\d+)*"


def docs() -> dict[str, str]:
    """The tracked Markdown files, by path."""
    names = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return {name: (PROJECT_ROOT / name).read_text(encoding="utf-8") for name in names}


def mentions(pattern: str) -> dict[str, set[str]]:
    """What *pattern*'s group matches in each doc that has a match; at least one must."""
    found = {name: set(re.findall(pattern, text)) for name, text in docs().items()}
    found = {name: matches for name, matches in found.items() if matches}
    assert found, f"no doc matches {pattern!r}: update the docs or this test"
    return found


def test_the_pandoc_version_is_the_pinned_one() -> None:
    for pattern in (rf"[Pp]andoc[ -]({VERSION})", rf"jgm/pandoc/releases/tag/({VERSION})"):
        for name, versions in mentions(pattern).items():
            assert versions == {PANDOC_VERSION}, name


def test_the_nodejs_version_is_the_minimum_that_setup_checks() -> None:
    for name, versions in mentions(rf"Node\.js(?:\]\([^)]*\))? ({VERSION}) or newer").items():
        for version in versions:
            parts = tuple(int(part) for part in version.split("."))
            assert parts + (0,) * (len(NODE_MINIMUM) - len(parts)) == NODE_MINIMUM, name


def test_the_python_versions_are_the_supported_ones() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    supported = re.fullmatch(r">=3\.(\d+),<3\.(\d+)", project["project"]["requires-python"])
    assert supported is not None
    oldest, newest = f"3.{supported[1]}", f"3.{int(supported[2]) - 1}"
    for name, ranges in mentions(r"Python (3\.\d+) to (3\.\d+)").items():
        assert ranges == {(oldest, newest)}, name
    for name, versions in mentions(r"UV_PYTHON=(3\.\d+)").items():
        assert versions == {oldest}, name


@pytest.mark.parametrize(
    ("pattern", "value"),
    [
        pytest.param(
            r"([\d,]+) MiB", f"{packages.MAX_EXPANDED_SIZE // packages.MIB:,}", id="size-limit"
        ),
        pytest.param(r"up to (\d+) seconds by default", str(PDF_TIMEOUT_DEFAULT), id="pdf-guide"),
        pytest.param(r"in seconds \((\d+);", str(PDF_TIMEOUT_DEFAULT), id="pdf-setting"),
    ],
)
def test_limits_are_the_codes(pattern: str, value: str) -> None:
    for name, values in mentions(pattern).items():
        assert values == {value}, name


def test_the_readme_lists_every_command() -> None:
    readme = docs()["README.md"]
    assert sorted(re.findall(r"^\| `([a-z-]+)[ `]", readme, re.MULTILINE)) == sorted(COMMANDS)


def test_the_word_guide_lists_every_option_of_docx_to_md(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        docx_cli.parse_args(["--help"])
    options: set[str] = set()
    for line in capsys.readouterr().out.splitlines():
        if line.startswith("  -"):
            names = re.split(r"\s{2,}", line.strip())[0]
            options.update(name.split()[0] for name in names.split(", "))
    guide = docs()["docs/word-conversion.md"]
    start = guide.index("| Option |", guide.index("## Options"))
    table = guide[start : guide.index("\n\n", start)]
    listed = {
        option
        for row in table.splitlines()
        for option in re.findall(r"`(-{1,2}[a-z][a-z-]*)", re.split(r"(?<!\\)\|", row)[1])
    }
    assert listed == options - {"-h", "--help"}
