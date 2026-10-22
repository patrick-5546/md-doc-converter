from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


class PackageLayoutTest(unittest.TestCase):
    def test_root_contains_only_the_project_manifests_and_readme(self) -> None:
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
            if Path(name).parent == Path(".") and not name.startswith(".")
        }
        self.assertEqual(files, {"Makefile", "README.md", "pyproject.toml", "uv.lock"})

    def test_all_cli_help_is_available_without_site_packages_or_external_tools(self) -> None:
        cases: tuple[list[str], ...] = (
            [],
            ["--help"],
            ["--version"],
            *([command, "--help"] for command in COMMANDS),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
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
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                expected = (
                    "md-doc-converter" if arguments == ["--version"] else "usage: md-doc-converter"
                )
                self.assertIn(expected, result.stdout)

    def test_resource_paths_are_durable_and_independent_of_caller_directory(self) -> None:
        assets = (
            "pandoc-defaults.yaml",
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
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    (
                        "import json\nfrom md_doc_converter.resources import asset_path\n"
                        f"print(json.dumps([str(asset_path(name)) for name in {assets!r}]))"
                    ),
                ],
                cwd=directory,
                env={**os.environ, "PYTHONPATH": str(SOURCE_ROOT)},
                capture_output=True,
                text=True,
                check=True,
            )
        actual = [Path(path) for path in json.loads(result.stdout)]
        self.assertEqual(actual, [PACKAGE_DIR / "assets" / name for name in assets])
        self.assertTrue(all(path.is_file() for path in actual))
        self.assertTrue(PDF_PRINTER.is_file())
        self.assertTrue(all((MANIFEST_DIR / name).is_file() for name in MANIFESTS))

    def test_npm_manifests_agree_and_resolve_from_the_public_registry(self) -> None:
        package = json.loads((MANIFEST_DIR / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((MANIFEST_DIR / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["name"], package["name"])
        self.assertEqual(lock["packages"][""]["dependencies"], package["dependencies"])
        resolved = {
            entry["resolved"].split("/")[2]
            for entry in lock["packages"].values()
            if "resolved" in entry
        }
        self.assertEqual(resolved, {"registry.npmjs.org"})

    def test_runtime_import_graph_is_acyclic_and_layered(self) -> None:
        modules = {}
        for path in PACKAGE_DIR.rglob("*.py"):
            parts = path.relative_to(PACKAGE_DIR.parent).with_suffix("").parts
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            modules[module] = ast.parse(path.read_text(encoding="utf-8"))
            if path.name == "__init__.py":
                self.assertFalse(
                    any(
                        isinstance(node, (ast.Import, ast.ImportFrom))
                        for node in modules[module].body
                    ),
                    f"Package initializer eagerly imports dependencies: {module}",
                )
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
            if name.startswith(SHARED_LAYERS):
                self.assertFalse(
                    any(
                        target.startswith((*FEATURES, "md_doc_converter.toolchain"))
                        for target in graph[name]
                    ),
                    f"Shared layer imports a feature: {name}",
                )
            if name.startswith("md_doc_converter.toolchain"):
                self.assertFalse(
                    any(
                        target.startswith((*FEATURES, "md_doc_converter.markdown"))
                        for target in graph[name]
                    ),
                    f"Toolchain imports a feature or the Markdown layer: {name}",
                )
            if name.startswith("md_doc_converter.converters.docx.") and not name.endswith(
                (".cli", ".pipeline")
            ):
                self.assertFalse(
                    graph[name]
                    & {
                        "md_doc_converter.converters.docx.cli",
                        "md_doc_converter.converters.docx.pipeline",
                    },
                    f"Domain module imports orchestration: {name}",
                )

        def visit(name: str, stack: tuple[str, ...]) -> None:
            self.assertNotIn(name, stack, " -> ".join((*stack, name)))
            for dependency in graph[name]:
                visit(dependency, (*stack, name))

        for name in graph:
            visit(name, ())
