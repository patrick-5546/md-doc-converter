from __future__ import annotations

import contextlib
import hashlib
import io
import os
import shutil
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from typing_extensions import override

from md_doc_converter.resources import asset_path
from md_doc_converter.toolchain import checks, install
from md_doc_converter.toolchain.environment import (
    MANIFEST_DIR,
    MANIFESTS,
    Toolchain,
    default_home,
    packages_current,
    render_environment,
    write_stamp,
)
from tests.support.fake_tools import install_fake_toolchain, read_log, write_fake_tools


class TemporaryTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.log = self.root / "tools.jsonl"
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()

    def path_with(self, *directories: Path) -> contextlib.AbstractContextManager[object]:
        """Search only the given directories, so no real pandoc, node or npm is found."""
        search = os.pathsep.join(str(directory) for directory in directories)
        return mock.patch.dict(os.environ, {"PATH": search})


class HomeTest(unittest.TestCase):
    def test_environment_variable_selects_an_absolute_home(self) -> None:
        self.assertEqual(
            default_home({"MD_DOC_CONVERTER_HOME": "/opt/renderer"}), Path("/opt/renderer")
        )
        self.assertEqual(
            default_home({"MD_DOC_CONVERTER_HOME": "~/renderer"}), Path.home() / "renderer"
        )
        self.assertEqual(
            default_home({"MD_DOC_CONVERTER_HOME": "renderer"}), Path.cwd() / "renderer"
        )

    def test_data_home_defaults(self) -> None:
        local = Path.home() / ".local" / "share" / "md-doc-converter"
        self.assertEqual(default_home({"XDG_DATA_HOME": "/data"}), Path("/data/md-doc-converter"))
        self.assertEqual(default_home({"XDG_DATA_HOME": "relative"}), local)
        self.assertEqual(default_home({"MD_DOC_CONVERTER_HOME": "  "}), local)
        self.assertEqual(default_home({}), local)


class EnvironmentTest(TemporaryTest):
    def test_render_environment_puts_the_toolchain_first(self) -> None:
        tools = Toolchain(Path("/renderer"))
        base = {"PATH": "/usr/bin", "MERMAID_FILTER_FORMAT": "png", "OTHER": "kept"}
        environment = render_environment(tools, base)
        self.assertEqual(
            environment["PATH"].split(os.pathsep),
            ["/renderer/node/node_modules/.bin", "/renderer/bin", "/usr/bin"],
        )
        self.assertEqual(environment["PUPPETEER_CACHE_DIR"], "/renderer/puppeteer")
        self.assertEqual(
            environment["MERMAID_FILTER_CMD_MMDC"], "/renderer/node/node_modules/.bin/mmdc"
        )
        self.assertEqual(
            environment["MERMAID_FILTER_MERMAID_CONFIG"], str(asset_path("mermaid-config.json"))
        )
        self.assertEqual(environment["MERMAID_FILTER_FORMAT"], "png")
        self.assertEqual(environment["OTHER"], "kept")
        self.assertEqual(base["PATH"], "/usr/bin")
        self.assertEqual(render_environment(tools, {})["MERMAID_FILTER_FORMAT"], "svg")

    def test_stamp_tracks_the_bundled_manifests(self) -> None:
        tools = Toolchain(self.root / "home")
        self.assertFalse(packages_current(tools))
        tools.node_modules.mkdir(parents=True)
        write_stamp(tools)
        self.assertTrue(packages_current(tools))
        tools.stamp.write_text("stale\n")
        self.assertFalse(packages_current(tools))
        write_stamp(tools)
        shutil.rmtree(tools.node_modules)
        self.assertFalse(packages_current(tools))


class ChecksTest(TemporaryTest):
    def test_doctor_is_ready_with_a_complete_toolchain(self) -> None:
        write_fake_tools(self.bin, self.log)
        tools = install_fake_toolchain(self.root / "home", self.log)
        with (
            self.path_with(self.bin),
            mock.patch.dict(os.environ, {"MD_DOC_CONVERTER_HOME": str(tools.root)}),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(checks.main([]), 0)
        report = output.getvalue()
        self.assertIn(f"Toolchain: {tools.root}", report)
        self.assertIn("Ready:", report)
        for name in ("pandoc", "node", "npm packages", "browser", "pandoc-katex"):
            self.assertRegex(report, rf"\n  ok +{name} ")

    def test_doctor_lists_a_fix_for_each_missing_part(self) -> None:
        with (
            self.path_with(self.bin),
            mock.patch.dict(os.environ, {"MD_DOC_CONVERTER_HOME": str(self.root / "empty")}),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(checks.main([]), 1)
        report = output.getvalue()
        self.assertIn("Install Pandoc 3.10 or newer", report)
        self.assertIn("Install Node.js 22.12 or newer", report)
        self.assertIn("-> Run `md-doc-converter setup`", report)
        self.assertIn("Not ready to render", report)

    def test_versions_are_checked(self) -> None:
        write_fake_tools(
            self.bin, self.log, versions={"node": "v20.11.1", "pandoc": "pandoc 3.1.9"}
        )
        with self.path_with(self.bin):
            node = checks.check_node()
            pandoc = checks.check_pandoc()
        self.assertEqual(node.status, "error")
        self.assertIn("20.11.1 is older than 22.12", node.detail)
        self.assertEqual(pandoc.status, "warn")
        self.assertIn("older than the tested 3.10", pandoc.detail)

    def test_render_problems_name_each_missing_part_and_html_needs_no_printer(self) -> None:
        write_fake_tools(self.bin, self.log)
        tools = Toolchain(self.root / "home")
        with self.path_with(self.bin):
            problems = checks.render_problems(tools, pdf=True)
        self.assertEqual(
            [problem.split(":")[0] for problem in problems],
            ["npm packages", "browser", "pandoc-katex"],
        )
        self.assertTrue(all("md-doc-converter setup" in problem for problem in problems))
        tools = install_fake_toolchain(self.root / "complete", self.log)
        shutil.rmtree(tools.node_modules / "pagedjs-cli")
        with self.path_with(self.bin):
            self.assertEqual(checks.render_problems(tools, pdf=False), [])
            self.assertIn("pagedjs-cli", checks.render_problems(tools, pdf=True)[0])


class InstallTest(TemporaryTest):
    def archive(self, *, binary: bool = True) -> tuple[Path, str]:
        tool = write_fake_tools(self.root / "archive", self.log, ("pandoc-katex",)) / "pandoc-katex"
        archive = self.root / "pandoc-katex.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            if binary:
                bundle.add(tool, arcname="./bin/pandoc-katex")
            license_file = self.root / "LICENSE-MIT"
            license_file.write_text("license")
            bundle.add(license_file, arcname="./LICENSE-MIT")
            bundle.add(license_file, arcname="../escaped")
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    def test_npm_packages_install_from_the_bundled_lockfile_once(self) -> None:
        write_fake_tools(self.bin, self.log)
        tools = Toolchain(self.root / "home")
        npm = str(self.bin / "npm")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(install.install_packages(tools, npm, force=False))
            self.assertFalse(install.install_packages(tools, npm, force=False))
            self.assertTrue(install.install_packages(tools, npm, force=True))
        for name in MANIFESTS:
            self.assertEqual(
                (tools.node_dir / name).read_bytes(), (MANIFEST_DIR / name).read_bytes()
            )
        self.assertTrue(packages_current(tools))
        calls = read_log(self.log)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["arguments"], ["ci", "--no-audit", "--no-fund"])
        self.assertEqual(calls[0]["cwd"], str(tools.node_dir))
        environment = calls[0]["environment"]
        assert isinstance(environment, dict)
        self.assertEqual(environment["PUPPETEER_CACHE_DIR"], str(tools.puppeteer_cache))
        self.assertEqual(checks.check_browser(tools).status, "ok")

    def test_failed_npm_install_leaves_no_stamp(self) -> None:
        write_fake_tools(self.bin, self.log)
        tools = Toolchain(self.root / "home")
        with (
            mock.patch.dict(os.environ, {"FAKE_TOOL_FAIL": "npm"}),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(install.SetupError, "npm ci failed"),
        ):
            install.install_packages(tools, str(self.bin / "npm"), force=False)
        self.assertFalse(tools.stamp.exists())
        self.assertFalse(packages_current(tools))

    def test_pandoc_katex_installs_only_the_verified_binary(self) -> None:
        archive, digest = self.archive()
        tools = Toolchain(self.root / "home")
        with (
            mock.patch.object(install, "PANDOC_KATEX_SHA256", digest),
            mock.patch.object(install.urllib.request, "urlopen") as download,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertTrue(install.install_pandoc_katex(tools, force=False, archive=archive))
            self.assertFalse(install.install_pandoc_katex(tools, force=False))
        download.assert_not_called()
        self.assertEqual(checks.check_pandoc_katex(tools).status, "ok")
        self.assertEqual([path.name for path in tools.bin_dir.iterdir()], ["pandoc-katex"])
        self.assertFalse((self.root / "escaped").exists())
        self.assertFalse((tools.root / "escaped").exists())

    def test_checksum_mismatch_installs_nothing(self) -> None:
        archive, _ = self.archive()
        tools = Toolchain(self.root / "home")
        with self.assertRaisesRegex(install.SetupError, install.PANDOC_KATEX_SHA256):
            install.install_pandoc_katex(tools, force=False, archive=archive)
        self.assertFalse(tools.pandoc_katex.exists())
        self.assertEqual(list(tools.bin_dir.iterdir()), [])

    def test_archive_without_the_binary_is_rejected(self) -> None:
        archive, digest = self.archive(binary=False)
        tools = Toolchain(self.root / "home")
        with (
            mock.patch.object(install, "PANDOC_KATEX_SHA256", digest),
            self.assertRaisesRegex(install.SetupError, "does not contain bin/pandoc-katex"),
        ):
            install.install_pandoc_katex(tools, force=False, archive=archive)
        self.assertFalse(tools.pandoc_katex.exists())

    def test_download_failure_suggests_a_local_archive(self) -> None:
        tools = Toolchain(self.root / "home")
        failure = urllib.error.URLError("network unreachable")
        with (
            mock.patch.object(install.urllib.request, "urlopen", side_effect=failure),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(install.SetupError, "--pandoc-katex-archive"),
        ):
            install.install_pandoc_katex(tools, force=False)
        self.assertFalse(tools.pandoc_katex.exists())

    def test_setup_checks_the_platform_and_node_before_installing(self) -> None:
        write_fake_tools(self.bin, self.log, versions={"node": "v20.11.1"})
        home = {"MD_DOC_CONVERTER_HOME": str(self.root / "home")}
        for supported, message in ((False, "Linux x86_64 only"), (True, "older than 22.12")):
            with (
                self.subTest(supported=supported),
                self.path_with(self.bin),
                mock.patch.dict(os.environ, home),
                mock.patch.object(install, "supported_platform", return_value=supported),
                contextlib.redirect_stderr(io.StringIO()) as errors,
            ):
                self.assertEqual(install.main([]), 1)
            self.assertIn(message, errors.getvalue())
        self.assertEqual(read_log(self.log), [])
        self.assertFalse((self.root / "home").exists())

    def test_setup_installs_a_complete_toolchain(self) -> None:
        write_fake_tools(self.bin, self.log)
        archive, digest = self.archive()
        tools = Toolchain(self.root / "home")
        with (
            self.path_with(self.bin),
            mock.patch.dict(os.environ, {"MD_DOC_CONVERTER_HOME": str(tools.root)}),
            mock.patch.object(install, "supported_platform", return_value=True),
            mock.patch.object(install, "PANDOC_KATEX_SHA256", digest),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(install.main(["--pandoc-katex-archive", str(archive)]), 0)
            self.assertEqual(checks.render_problems(tools, pdf=True), [])
        self.assertIn("md-doc-converter doctor", output.getvalue())


if __name__ == "__main__":
    unittest.main()
