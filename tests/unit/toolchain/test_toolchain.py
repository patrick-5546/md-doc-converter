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
from dataclasses import replace
from pathlib import Path
from unittest import mock

from typing_extensions import override

from md_doc_converter.resources import asset_path
from md_doc_converter.toolchain import checks, install
from md_doc_converter.toolchain.environment import (
    MANIFEST_DIR,
    MANIFESTS,
    PANDOC_VERSION,
    Toolchain,
    default_home,
    katex_version,
    packages_current,
    prefer_installed_tools,
    render_environment,
    write_stamp,
)
from tests.support.fake_tools import (
    BROWSER_PATH,
    install_fake_toolchain,
    read_log,
    write_fake_tools,
)


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
        base = {"PATH": "/usr/bin", "OTHER": "kept"}
        environment = render_environment(tools, base)
        self.assertEqual(
            environment["PATH"].split(os.pathsep),
            ["/renderer/node/node_modules/.bin", "/renderer/bin", "/usr/bin"],
        )
        self.assertEqual(environment["PUPPETEER_CACHE_DIR"], "/renderer/puppeteer")
        self.assertEqual(
            environment["MD_DOC_CONVERTER_MMDC"], "/renderer/node/node_modules/.bin/mmdc"
        )
        self.assertEqual(
            environment["MD_DOC_CONVERTER_MERMAID_CONFIG"], str(asset_path("mermaid-config.json"))
        )
        self.assertEqual(
            environment["MD_DOC_CONVERTER_NODE_MODULES"], "/renderer/node/node_modules"
        )
        self.assertEqual(environment["OTHER"], "kept")
        self.assertEqual(base["PATH"], "/usr/bin")

    def test_katex_version_comes_from_the_installed_package(self) -> None:
        tools = Toolchain(self.root / "home")
        self.assertIsNone(katex_version(tools))
        manifest = tools.node_modules / "katex" / "package.json"
        manifest.parent.mkdir(parents=True)
        for text, expected in (("{", None), ("[]", None), ('{"version": "0.19.0"}', "0.19.0")):
            manifest.write_text(text)
            self.assertEqual(katex_version(tools), expected)

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
        for name in ("pandoc", "node", "npm packages", "browser"):
            self.assertRegex(report, rf"\n  ok +{name} ")

    def test_doctor_lists_a_fix_for_each_missing_part(self) -> None:
        with (
            self.path_with(self.bin),
            mock.patch.dict(os.environ, {"MD_DOC_CONVERTER_HOME": str(self.root / "empty")}),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(checks.main([]), 1)
        report = output.getvalue()
        self.assertIn("-> Run `md-doc-converter setup`, or `setup --no-render`", report)
        self.assertIn("Install Node.js 22.13 or newer", report)
        self.assertIn("-> Run `md-doc-converter setup`\n", report)
        self.assertIn("Not ready to render", report)

    def test_versions_are_checked(self) -> None:
        write_fake_tools(
            self.bin, self.log, versions={"node": "v20.11.1", "pandoc": "pandoc 3.1.9"}
        )
        with self.path_with(self.bin):
            node = checks.check_node()
            pandoc = checks.check_pandoc()
        self.assertEqual(node.status, "error")
        self.assertIn("20.11.1 is older than 22.13", node.detail)
        self.assertEqual(pandoc.status, "warn")
        self.assertIn(
            f"3.1.9 ({self.bin / 'pandoc'}); md-doc-converter is tested with", pandoc.detail
        )
        self.assertIn(PANDOC_VERSION, pandoc.detail)
        self.assertIn("setup --no-render", pandoc.fix)

    def test_the_pinned_pandoc_comes_before_any_other(self) -> None:
        write_fake_tools(self.bin, self.log, versions={"pandoc": "pandoc 3.1.9"})
        tools = install_fake_toolchain(self.root / "home", self.log)
        with (
            self.path_with(self.bin),
            mock.patch.dict(os.environ, {"MD_DOC_CONVERTER_HOME": str(tools.root)}),
        ):
            prefer_installed_tools()
            prefer_installed_tools()
            search = os.environ["PATH"].split(os.pathsep)
            pandoc = checks.check_pandoc()
        self.assertEqual(search, [str(tools.bin_dir), str(self.bin)])
        self.assertEqual(pandoc.status, "ok")
        self.assertEqual(pandoc.detail, f"{PANDOC_VERSION} ({tools.pandoc})")

    def test_preferring_a_missing_toolchain_changes_nothing(self) -> None:
        environment = {"MD_DOC_CONVERTER_HOME": str(self.root / "none"), "PATH": "/usr/bin"}
        prefer_installed_tools(environment)
        self.assertEqual(environment["PATH"], "/usr/bin")

    def test_render_problems_name_each_missing_part_and_html_needs_no_printer(self) -> None:
        write_fake_tools(self.bin, self.log)
        tools = Toolchain(self.root / "home")
        with self.path_with(self.bin):
            problems = checks.render_problems(tools, pdf=True)
        self.assertEqual(
            [problem.split(":")[0] for problem in problems],
            ["npm packages", "browser"],
        )
        self.assertTrue(all("md-doc-converter setup" in problem for problem in problems))
        tools = install_fake_toolchain(self.root / "complete", self.log)
        shutil.rmtree(tools.node_modules / "pagedjs-cli")
        with self.path_with(self.bin):
            self.assertEqual(checks.render_problems(tools, pdf=False), [])
            self.assertIn("pagedjs-cli", checks.render_problems(tools, pdf=True)[0])
        shutil.rmtree(tools.node_modules / "katex")
        with self.path_with(self.bin):
            self.assertIn("missing katex", checks.render_problems(tools, pdf=False)[0])

    def test_only_an_executable_chrome_headless_shell_counts_as_the_browser(self) -> None:
        tools = Toolchain(self.root / "home")
        (tools.puppeteer_cache / "chrome" / "linux-131.0.6778.204").mkdir(parents=True)
        browser = tools.puppeteer_cache / BROWSER_PATH
        browser.parent.mkdir(parents=True)
        browser.write_text("")
        browser.chmod(0o644)
        missing = checks.check_browser(tools)
        self.assertEqual(missing.status, "error")
        self.assertEqual(missing.detail, f"no chrome-headless-shell in {tools.puppeteer_cache}")
        self.assertIn("setup --force", missing.fix)
        browser.chmod(0o755)
        self.assertEqual(checks.check_browser(tools).status, "ok")

    def test_rendering_needs_pandoc(self) -> None:
        write_fake_tools(self.bin, self.log, ("node", "npm"))
        tools = install_fake_toolchain(self.root / "home", self.log)
        tools.pandoc.unlink()
        with self.path_with(self.bin):
            problems = checks.render_problems(tools, pdf=False)
        self.assertEqual(len(problems), 1)
        self.assertIn("pandoc: not installed. Run `md-doc-converter setup`", problems[0])


class InstallTest(TemporaryTest):
    def archive(self, archive: install.Archive, *, binary: bool = True) -> install.Archive:
        """Write a fake release of *archive* into the downloads folder; return it re-pinned."""
        tool = write_fake_tools(self.root / "archive", self.log, (archive.executable,))
        downloads = self.root / "downloads"
        downloads.mkdir(exist_ok=True)
        tarball = downloads / archive.file
        with tarfile.open(tarball, "w:gz") as bundle:
            if binary:
                bundle.add(tool / archive.executable, arcname="./" + str(archive.member))
            license_file = self.root / "LICENSE"
            license_file.write_text("license")
            bundle.add(license_file, arcname="./LICENSE")
            bundle.add(license_file, arcname="../escaped")
        return replace(archive, sha256=hashlib.sha256(tarball.read_bytes()).hexdigest())

    def test_npm_packages_and_one_browser_install_from_the_bundled_lockfile_once(self) -> None:
        write_fake_tools(self.bin, self.log)
        tools = Toolchain(self.root / "home")
        stale = tools.puppeteer_cache / "chrome" / "linux-120.0.0.0"
        stale.mkdir(parents=True)
        npm = str(self.bin / "npm")
        with self.path_with(self.bin), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(install.install_packages(tools, npm, force=False))
            self.assertFalse(install.install_packages(tools, npm, force=False))
            self.assertTrue(install.install_packages(tools, npm, force=True))
        for name in MANIFESTS:
            self.assertEqual(
                (tools.node_dir / name).read_bytes(), (MANIFEST_DIR / name).read_bytes()
            )
        self.assertTrue(packages_current(tools))
        calls = read_log(self.log)
        self.assertEqual(
            [(call["tool"], call["arguments"]) for call in calls],
            2
            * [
                ("npm", ["ci", "--ignore-scripts", "--no-audit", "--no-fund"]),
                ("puppeteer", ["browsers", "install", "chrome-headless-shell"]),
            ],
        )
        for call in calls:
            self.assertEqual(call["cwd"], str(tools.node_dir))
            environment = call["environment"]
            assert isinstance(environment, dict)
            self.assertEqual(environment["PUPPETEER_CACHE_DIR"], str(tools.puppeteer_cache))
        self.assertEqual(
            [path.name for path in tools.puppeteer_cache.iterdir()], ["chrome-headless-shell"]
        )
        browser = checks.check_browser(tools)
        self.assertEqual(browser.status, "ok")
        self.assertEqual(
            browser.detail, f"chrome-headless-shell 131.0.6778.204 ({tools.puppeteer_cache})"
        )

    def test_failed_install_leaves_no_stamp(self) -> None:
        for tool, message in (("npm", "npm ci failed"), ("puppeteer", "browser install failed")):
            write_fake_tools(self.bin, self.log)
            tools = Toolchain(self.root / tool)
            with (
                self.subTest(tool=tool),
                self.path_with(self.bin),
                mock.patch.dict(os.environ, {"FAKE_TOOL_FAIL": tool}),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(install.SetupError, message),
            ):
                install.install_packages(tools, str(self.bin / "npm"), force=False)
            self.assertFalse(tools.stamp.exists())
            self.assertFalse(packages_current(tools))

    def test_a_browser_install_that_installs_nothing_fails(self) -> None:
        write_fake_tools(self.bin, self.log)
        tools = Toolchain(self.root / "home")
        missing = checks.Check("browser", "error", "missing")
        with (
            self.path_with(self.bin),
            mock.patch.object(install, "check_browser", return_value=missing),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(install.SetupError, "PUPPETEER_SKIP_DOWNLOAD"),
        ):
            install.install_packages(tools, str(self.bin / "npm"), force=False)
        self.assertFalse(tools.stamp.exists())

    def test_the_pinned_archive_installs_only_its_verified_executable(self) -> None:
        archive = self.archive(install.PANDOC)
        tools = Toolchain(self.root / "home")
        downloads = self.root / "downloads"
        with (
            mock.patch.object(install.urllib.request, "urlopen") as download,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertTrue(
                install.install_executable(tools, archive, force=False, downloads=downloads)
            )
            self.assertFalse(install.install_executable(tools, archive, force=False))
        download.assert_not_called()
        self.assertEqual([path.name for path in tools.bin_dir.iterdir()], ["pandoc"])
        self.assertEqual(checks.installed_version(tools.pandoc), PANDOC_VERSION)
        self.assertFalse((self.root / "escaped").exists())
        self.assertFalse((tools.root / "escaped").exists())

    def test_checksum_mismatch_installs_nothing(self) -> None:
        archive = replace(self.archive(install.PANDOC), sha256="0" * 64)
        tools = Toolchain(self.root / "home")
        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(install.SetupError, "expected 0{64}"),
        ):
            install.install_executable(
                tools, archive, force=False, downloads=self.root / "downloads"
            )
        self.assertFalse(tools.pandoc.exists())
        self.assertEqual(list(tools.bin_dir.iterdir()), [])

    def test_archive_without_the_executable_is_rejected(self) -> None:
        archive = self.archive(install.PANDOC, binary=False)
        tools = Toolchain(self.root / "home")
        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(install.SetupError, f"does not contain {archive.member}"),
        ):
            install.install_executable(
                tools, archive, force=False, downloads=self.root / "downloads"
            )
        self.assertFalse(tools.pandoc.exists())

    def test_download_failure_suggests_a_downloads_folder(self) -> None:
        tools = Toolchain(self.root / "home")
        failure = urllib.error.URLError("network unreachable")
        with (
            mock.patch.object(install.urllib.request, "urlopen", side_effect=failure),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(install.SetupError, "--downloads FOLDER"),
        ):
            install.install_executable(tools, install.PANDOC, force=False)
        self.assertFalse(tools.pandoc.exists())

    def test_setup_checks_the_platform_and_node_before_installing(self) -> None:
        write_fake_tools(self.bin, self.log, versions={"node": "v20.11.1"})
        home = {"MD_DOC_CONVERTER_HOME": str(self.root / "home")}
        for supported, message in ((False, "Linux x86_64 only"), (True, "--no-render")):
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

    def test_no_render_installs_only_pandoc_without_node(self) -> None:
        pandoc = self.archive(install.PANDOC)
        tools = Toolchain(self.root / "home")
        with (
            self.path_with(self.bin),
            mock.patch.dict(os.environ, {"MD_DOC_CONVERTER_HOME": str(tools.root)}),
            mock.patch.object(install, "supported_platform", return_value=True),
            mock.patch.object(install, "PANDOC", pandoc),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            arguments = ["--no-render", "--downloads", str(self.root / "downloads")]
            self.assertEqual(install.main(arguments), 0)
        self.assertEqual([path.name for path in tools.bin_dir.iterdir()], ["pandoc"])
        self.assertFalse(tools.node_dir.exists())
        self.assertIn("Rendering needs a full `setup`", output.getvalue())

    def test_setup_installs_a_complete_toolchain(self) -> None:
        write_fake_tools(self.bin, self.log)
        pandoc = self.archive(install.PANDOC)
        tools = Toolchain(self.root / "home")
        with (
            self.path_with(self.bin),
            mock.patch.dict(os.environ, {"MD_DOC_CONVERTER_HOME": str(tools.root)}),
            mock.patch.object(install, "supported_platform", return_value=True),
            mock.patch.object(install, "PANDOC", pandoc),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(install.main(["--downloads", str(self.root / "downloads")]), 0)
            prefer_installed_tools()
            self.assertEqual(checks.render_problems(tools, pdf=True), [])
            self.assertEqual(checks.check_pandoc().detail, f"{PANDOC_VERSION} ({tools.pandoc})")
        self.assertIn("md-doc-converter doctor", output.getvalue())

    def test_downloads_must_be_a_folder(self) -> None:
        with (
            mock.patch.object(install, "supported_platform", return_value=True),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(install.main(["--downloads", str(self.root / "missing")]), 1)
        self.assertIn("--downloads must be a folder", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
