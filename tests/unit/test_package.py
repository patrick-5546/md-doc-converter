"""The package's layout: its files, command-line help, resources, npm manifests and imports."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

from md_doc_converter import resources
from md_doc_converter.__main__ import COMMANDS
from md_doc_converter.rendering.pipeline import PDF_PRINTER
from md_doc_converter.resources import PACKAGE_DIR
from md_doc_converter.toolchain.environment import MANIFEST_DIR, MANIFESTS
from tests.support.project import PROJECT_ROOT, SOURCE_ROOT

SHARED_LAYERS = (
    "md_doc_converter.markdown",
    "md_doc_converter.pandoc",
    "md_doc_converter.resources",
)
FEATURES = ("md_doc_converter.converters", "md_doc_converter.rendering")
ASSETS = (
    "pandoc-defaults.yaml",
    "gfm-math.lua",
    "mermaid-config.json",
    "mermaid.lua",
    "katex.js",
    "style-overrides.css",
    "markdown-body-open.html",
    "markdown-body-close.html",
    "strip-colwidths.lua",
    "table-code-pipes.lua",
    "reference-notes.lua",
)
DOCX_ORCHESTRATION = {
    "md_doc_converter.converters.docx.cli",
    "md_doc_converter.converters.docx.pipeline",
}


def module_name(path: Path) -> str:
    """The dotted name of a module of the package, from its file."""
    parts = path.relative_to(PACKAGE_DIR.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


@pytest.fixture(scope="module")
def imports() -> dict[str, set[str]]:
    """Each module of the package, by dotted name, with the package modules it imports."""
    modules = {
        module_name(path): ast.parse(path.read_text(encoding="utf-8"))
        for path in PACKAGE_DIR.rglob("*.py")
    }
    graph: dict[str, set[str]] = {name: set() for name in modules}
    for name, tree in modules.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    child = node.module + "." + alias.name
                    target = child if child in modules else node.module
                    if target in modules and target != name:
                        graph[name].add(target)
            elif isinstance(node, ast.Import):
                graph[name].update(alias.name for alias in node.names if alias.name in modules)
    return graph


def test_root_contains_only_the_project_manifests_and_readme() -> None:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    files = {
        name
        for name in result.stdout.splitlines()
        if Path(name).parent == Path() and not name.startswith(".")
    }
    assert files == {"Makefile", "README.md", "pyproject.toml", "uv.lock"}


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param([], id="no-arguments"),
        pytest.param(["--help"], id="help"),
        pytest.param(["--version"], id="version"),
        *(pytest.param([command, "--help"], id=f"{command}-help") for command in COMMANDS),
    ],
)
def test_cli_help_is_available_without_site_packages_or_external_tools(
    arguments: list[str],
) -> None:
    script = (
        "import sys\nfrom md_doc_converter.__main__ import main\n"
        f"try:\n    status = main({arguments!r})\n"
        "except SystemExit as error:\n    status = error.code\n"
        "assert status in (None, 0), status\n"
        "assert not {'markitdown', 'openpyxl'} & set(sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-S", "-c", script],
        cwd=PROJECT_ROOT,
        env={"PATH": "", "PYTHONPATH": str(SOURCE_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = "md-doc-converter" if arguments == ["--version"] else "usage: md-doc-converter"
    assert expected in result.stdout


def test_resource_paths_are_durable_and_independent_of_caller_directory(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import json\nfrom md_doc_converter.resources import asset_path\n"
                f"print(json.dumps([str(asset_path(name)) for name in {ASSETS!r}]))"
            ),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(SOURCE_ROOT)},
        capture_output=True,
        text=True,
        check=True,
    )
    actual = [Path(path) for path in json.loads(result.stdout)]
    assert actual == [PACKAGE_DIR / "assets" / name for name in ASSETS]
    assert all(path.is_file() for path in actual)
    assert PDF_PRINTER.is_file()
    assert all((MANIFEST_DIR / name).is_file() for name in MANIFESTS)


def test_npm_manifests_agree_and_resolve_from_the_public_registry() -> None:
    package = json.loads((MANIFEST_DIR / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((MANIFEST_DIR / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["name"] == package["name"]
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    resolved = {
        entry["resolved"].split("/")[2]
        for entry in lock["packages"].values()
        if "resolved" in entry
    }
    assert resolved == {"registry.npmjs.org"}


def test_package_initializers_import_nothing() -> None:
    for path in PACKAGE_DIR.rglob("__init__.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in tree.body), (
            f"Package initializer eagerly imports dependencies: {module_name(path)}"
        )


def test_runtime_imports_are_layered(imports: dict[str, set[str]]) -> None:
    for name, targets in imports.items():
        if name.startswith(SHARED_LAYERS):
            assert not any(
                target.startswith((*FEATURES, "md_doc_converter.toolchain")) for target in targets
            ), f"Shared layer imports a feature: {name}"
        if name.startswith("md_doc_converter.toolchain"):
            assert not any(
                target.startswith((*FEATURES, "md_doc_converter.markdown")) for target in targets
            ), f"Toolchain imports a feature or the Markdown layer: {name}"
        if name.startswith("md_doc_converter.converters.docx.") and not name.endswith(
            (".cli", ".pipeline")
        ):
            assert not targets & DOCX_ORCHESTRATION, f"Domain module imports orchestration: {name}"


def test_runtime_import_graph_is_acyclic(imports: dict[str, set[str]]) -> None:
    def visit(name: str, stack: tuple[str, ...]) -> None:
        assert name not in stack, " -> ".join((*stack, name))
        for dependency in imports[name]:
            visit(dependency, (*stack, name))

    for name in imports:
        visit(name, ())


def test_a_source_tree_reports_that_the_package_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def not_installed(distribution: str) -> str:
        raise metadata.PackageNotFoundError(distribution)

    monkeypatch.setattr(resources.metadata, "version", not_installed)
    assert resources.package_version() == "(not installed)"
