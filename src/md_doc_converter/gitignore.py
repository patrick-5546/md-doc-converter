"""Which entries of a folder input to skip because Git ignores them.

A folder input in a Git work tree skips what Git ignores, unless Git ignores the folder itself,
in which every file is ignored. --respect-gitignore and --no-respect-gitignore choose for any
folder. git check-ignore decides, so .gitignore files, .git/info/exclude and the global excludes
file all count, and tracked files are never skipped. Without git, nothing is skipped.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from md_doc_converter import executables
from md_doc_converter.console import active, shown


def add_option(parser: argparse.ArgumentParser) -> None:
    """Add --respect-gitignore and --no-respect-gitignore, the *choice* of skips_ignored."""
    parser.add_argument(
        "--respect-gitignore",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Skip Git-ignored files in a folder input (default: when it is in a Git work tree, "
            "unless Git ignores the folder)"
        ),
    )


def skips_ignored(choice: bool | None, source: Path) -> bool:
    """Whether the input *source* skips what Git ignores, given the option's *choice*, if any.

    Only a folder in a Git work tree skips anything; asked to outside one, it warns.
    """
    if choice is False or not source.is_dir():
        return False
    if not in_git_work_tree(source):
        if choice:
            active().warning(
                f"--respect-gitignore had no effect: '{shown(source)}' is not "
                "inside a git working tree (or git is unavailable)"
            )
        return False
    # By default, not in a folder that Git ignores, which would skip every file in it.
    return choice is True or not ignored_by_git(source)


def in_git_work_tree(directory: Path) -> bool:
    """Return True if git is available and *directory* is inside a work tree."""
    git = executables.which("git")
    if git is None:
        return False
    try:
        proc = subprocess.run(
            [git, "-C", str(directory), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def ignored_by_git(directory: Path) -> bool:
    """Return True if git ignores *directory* itself, by a rule for it or for a parent.

    Asked from inside *directory*, its own repository answers: the top of a work tree is never
    ignored, even when an enclosing repository ignores it.
    """
    # Named from its parent: git would test "." as "docs/", say, which a rule for the folder's
    # entries, such as "docs/*", also matches. git works in the folder that a symbolic link
    # leads to, so the name is that folder's.
    path = f"../{directory.resolve().name}"
    return path in git_ignored([path], directory)


def git_ignored(names: list[str], directory: Path) -> set[str]:
    """Return the subset of *names* (entries of *directory*) that git ignores.

    Runs ``git check-ignore`` from *directory*.  It honours .gitignore,
    .git/info/exclude and the global excludes file, and (by consulting the
    index) never reports tracked files, so a tracked file matching an ignore
    rule is still kept.  Returns an empty set on any git error.
    """
    if not names:
        return set()
    try:
        proc = subprocess.run(
            [executables.resolve("git"), "check-ignore", "--stdin", "-z"],
            input="\0".join(names),
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    # 0 = one or more paths ignored, 1 = none ignored; anything else is an error.
    if proc.returncode not in (0, 1):
        return set()
    return {name for name in proc.stdout.split("\0") if name}
