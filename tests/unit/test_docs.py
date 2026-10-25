"""The docs name the versions, limits, commands and options that the code has.

`--help` is the reference for the options: the docs name an option only where they explain it,
so these tests check that each option they name exists, not that they list them all.
"""

from __future__ import annotations

import contextlib
import io
import re
import subprocess
import tomllib

import pytest

from md_doc_converter import __main__ as _main
from md_doc_converter.__main__ import COMMANDS
from md_doc_converter.converters import packages
from md_doc_converter.rendering.pipeline import PDF_TIMEOUT_DEFAULT
from md_doc_converter.toolchain.environment import NODE_MINIMUM, PANDOC_VERSION
from tests.support.project import PROJECT_ROOT

VERSION = r"\d+(?:\.\d+)*"
# An option, such as -o or --split-depth, but not a word with a hyphen in it.
OPTION = re.compile(r"(?<![\w-])(--?[a-z][a-z0-9-]*)")
# An example command line: the word after md-doc-converter, and its arguments up to a comment.
COMMAND_LINE = re.compile(r"md-doc-converter ([^\s`]+)([^`\n#]*)")
# development.md shows the tools of development, such as make and pytest, with their options.
CONTRIBUTORS = "docs/development.md"
# The options of other programs that the user docs show: uv's, to install through a mirror.
OTHER_OPTIONS = {"--default-index"}


def user_docs() -> dict[str, str]:
    """The tracked Markdown files that describe md-doc-converter for its users, by path."""
    return {name: text for name, text in docs().items() if name != CONTRIBUTORS}


@pytest.fixture(scope="module")
def options() -> dict[str, set[str]]:
    """Each command's options, and under "" md-doc-converter's own, as --help lists them."""
    found: dict[str, set[str]] = {}
    with pytest.MonkeyPatch.context() as patch:
        # Help needs no toolchain: leave PATH as it is. Help is plain text, without color.
        patch.setattr(_main, "prefer_installed_tools", lambda: None)
        patch.setenv("PYTHON_COLORS", "0")
        for command in ("", *COMMANDS):
            written = io.StringIO()
            with contextlib.redirect_stdout(written), pytest.raises(SystemExit):
                _main.main([command, "--help"] if command else ["--help"])
            help_text = written.getvalue()
            found[command] = {
                name.split()[0]
                for line in help_text[help_text.index("\noptions:\n") :].splitlines()
                if line.startswith("  -")
                for name in re.split(r"\s{2,}", line.strip())[0].split(", ")
            }
    return found


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


def test_the_python_version_is_the_supported_one() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    supported = re.fullmatch(r">=3\.(\d+),<3\.(\d+)", project["project"]["requires-python"])
    assert supported is not None
    assert int(supported[2]) == int(supported[1]) + 1, "the docs name one supported Python"
    for name, versions in mentions(r"Python (3\.\d+)").items():
        assert versions == {f"3.{supported[1]}"}, name


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


def test_the_readme_points_every_command_to_its_guide() -> None:
    readme = docs()["README.md"]
    start = readme.index("## Commands\n")
    section = readme[start : readme.index("\n## ", start)]
    named = set(re.findall(r"`([a-z]+(?:-to-[a-z]+)?)`", section))
    assert named >= set(COMMANDS)
    assert {name for name in named if "-to-" in name} <= set(COMMANDS)


def test_each_example_gives_its_command_only_that_commands_options(
    options: dict[str, set[str]],
) -> None:
    for name, text in user_docs().items():
        for command, arguments in COMMAND_LINE.findall(text):
            if command.startswith("-"):
                command, arguments = "", f"{command}{arguments}"
            elif command not in COMMANDS:
                continue  # a placeholder, such as COMMAND, or prose
            for option in OPTION.findall(arguments):
                assert option in options[command], f"{name}: md-doc-converter {command} {option}"


def test_the_docs_name_only_options_that_exist(options: dict[str, set[str]]) -> None:
    known = set().union(*options.values(), OTHER_OPTIONS)
    for name, text in user_docs().items():
        # The table classes' widths are CSS custom properties, --col1 to --col6.
        named = {option for option in OPTION.findall(text) if not re.fullmatch(r"--col\d+", option)}
        assert named <= known, f"{name} names options that no command has: {sorted(named - known)}"
