"""Which entries of a folder input are skipped because Git ignores them, asked of real git."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from md_doc_converter.console import Console, activated, shown
from md_doc_converter.gitignore import ignored_by_git, skips_ignored


def test_git_ignores_a_folder_by_a_rule_for_it_or_a_parent(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    for folder in ("scratch/sub", "scratch/nested", "build", "docs/a", "kept"):
        (outer / folder).mkdir(parents=True)
    (outer / ".gitignore").write_text("/scratch/\nbuild/\ndocs/*\n", encoding="utf-8")
    for repository in (outer, outer / "scratch" / "nested"):
        subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True, capture_output=True)
    expected = {
        ".": False,
        "scratch/sub": True,
        # The top of a work tree, which its own repository answers for, not the enclosing one.
        "scratch/nested": False,
        "build": True,
        "docs": False,
        "docs/a": True,
        "kept": False,
    }
    # Only some Windows users may create symbolic links.
    if sys.platform != "win32":
        # A link is judged as the folder that it leads to, whatever its own name.
        (outer / "alias").symlink_to(outer / "build", target_is_directory=True)
        expected["alias"] = True

    assert {folder: ignored_by_git(outer / folder) for folder in expected} == expected


@pytest.mark.parametrize(
    ("source", "choice", "expected"),
    [
        pytest.param("docs", None, True, id="folder"),
        pytest.param("docs", False, False, id="folder-opted-out"),
        pytest.param("scratch", None, False, id="ignored-folder"),
        pytest.param("scratch", True, True, id="ignored-folder-opted-in"),
        pytest.param("docs/page.md", True, False, id="file"),
    ],
)
def test_a_folder_in_a_work_tree_skips_what_git_ignores_unless_git_ignores_it(
    source: str, choice: bool | None, expected: bool, tmp_path: Path
) -> None:
    for folder in ("docs", "scratch"):
        (tmp_path / folder).mkdir()
    (tmp_path / "docs" / "page.md").write_text("# Page\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("/scratch/\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True, capture_output=True)

    assert skips_ignored(choice, tmp_path / source) is expected


@pytest.mark.parametrize(
    ("choice", "warnings"),
    [pytest.param(None, 0, id="by-default"), pytest.param(True, 1, id="on-request")],
)
def test_a_folder_outside_work_trees_skips_nothing_and_warns_if_asked_to(
    choice: bool | None, warnings: int, tmp_path: Path
) -> None:
    console = Console("csv-to-md", stream=(stream := io.StringIO()))
    with activated(console):
        assert not skips_ignored(choice, tmp_path)

    warning = (
        f"csv-to-md: warning: --respect-gitignore had no effect: '{shown(tmp_path)}' is not "
        "inside a git working tree (or git is unavailable)"
    )
    assert stream.getvalue().splitlines() == [warning] * warnings
