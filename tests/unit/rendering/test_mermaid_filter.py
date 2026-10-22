from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from typing_extensions import override

from md_doc_converter.resources import asset_path
from tests.support.fake_tools import read_log, write_fake_tools

DATA_URI = "data:image/svg+xml;base64,"


def images(document: object) -> list[dict[str, object]]:
    """Every Pandoc JSON Image in document order, as id, classes and decoded SVG."""
    found: list[dict[str, object]] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            if node.get("t") == "Image":
                [identifier, classes, _], _, [source, _] = node["c"]
                assert isinstance(source, str) and source.startswith(DATA_URI), source
                svg = base64.b64decode(source.removeprefix(DATA_URI)).decode()
                found.append({"id": identifier, "classes": classes, "svg": svg})
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(document)
    return found


class MermaidFilterTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.log = self.root / "tools.jsonl"
        self.mmdc = write_fake_tools(self.root / "bin", self.log, ("mmdc",)) / "mmdc"

    def convert(
        self, markdown: str, *, mmdc: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        pandoc = shutil.which("pandoc")
        assert pandoc is not None
        environment = {
            **os.environ,
            "MD_DOC_CONVERTER_MMDC": str(mmdc or self.mmdc),
            "MD_DOC_CONVERTER_MERMAID_CONFIG": "/config/mermaid.json",
        }
        return subprocess.run(
            [pandoc, "--from=markdown", "--to=json", f"--lua-filter={asset_path('mermaid.lua')}"],
            input=markdown,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def mmdc_calls(self) -> list[list[str]]:
        calls: list[list[str]] = []
        for call in read_log(self.log):
            arguments = call["arguments"]
            assert isinstance(arguments, list)
            calls.append([str(argument) for argument in arguments])
        return calls

    def test_all_diagrams_render_in_one_run_and_keep_their_place(self) -> None:
        result = self.convert(
            "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
            "```python\nprint('kept')\n```\n\n"
            "- item\n\n  ```{.mermaid #sequence}\n  sequenceDiagram\n    A->>B: Hi\n  ```\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(
            images(document),
            [
                {
                    "id": "",
                    "classes": ["mermaid-diagram"],
                    "svg": "<svg>flowchart LR\n  A --> B</svg>",
                },
                {
                    "id": "sequence",
                    "classes": ["mermaid-diagram"],
                    "svg": "<svg>sequenceDiagram\n  A->>B: Hi</svg>",
                },
            ],
        )
        self.assertEqual(document["blocks"][1]["t"], "CodeBlock")
        [call] = self.mmdc_calls()
        self.assertEqual(call[call.index("--configFile") + 1], "/config/mermaid.json")
        self.assertIn("--quiet", call)
        self.assertIn("--artefacts", call)

    def test_documents_without_diagrams_need_no_mmdc(self) -> None:
        result = self.convert("```python\nprint('kept')\n```\n", mmdc=self.root / "missing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(images(json.loads(result.stdout)), [])
        self.assertEqual(self.mmdc_calls(), [])

    def test_missing_mmdc_names_setup(self) -> None:
        result = self.convert("```mermaid\nflowchart LR\n```\n", mmdc=self.root / "missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mermaid-cli (mmdc) is not installed", result.stderr)
        self.assertIn("md-doc-converter setup", result.stderr)

    def test_a_failing_diagram_is_named_with_the_mermaid_message(self) -> None:
        result = self.convert(
            "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
            "```mermaid\n---\ntitle: Broken\n---\ngraph TD\n  FAIL\n```\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Mermaid diagram 2 of 2 (graph TD) could not be rendered:", result.stderr)
        self.assertIn("Error: Parse error on line 2", result.stderr)
        self.assertNotIn("file:///mermaid.mjs", result.stderr)
        calls = self.mmdc_calls()
        self.assertEqual(len(calls), 3)
        self.assertIn("--artefacts", calls[0])
        self.assertTrue(all("--artefacts" not in call for call in calls[1:]))

    def test_a_diagram_with_a_fence_line_renders_on_its_own(self) -> None:
        result = self.convert(
            "````mermaid\nflowchart LR\n```\n````\n\n```mermaid\nflowchart TD\n```\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [image["svg"] for image in images(json.loads(result.stdout))],
            ["<svg>flowchart LR\n```</svg>", "<svg>flowchart TD</svg>"],
        )
        batch, alone = self.mmdc_calls()
        self.assertIn("--artefacts", batch)
        self.assertNotIn("--artefacts", alone)


if __name__ == "__main__":
    unittest.main()
