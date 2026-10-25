"""The toolchain: where it lives, what `doctor` checks, and what `setup` installs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import urllib.error
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest

from md_doc_converter.resources import asset_path
from md_doc_converter.toolchain import checks, install
from md_doc_converter.toolchain.environment import (
    LINUX,
    MANIFEST_DIR,
    MANIFESTS,
    PANDOC_VERSION,
    PLATFORMS,
    WINDOWS,
    Platform,
    Toolchain,
    current_platform,
    default_home,
    host_platform,
    packages_current,
    prefer_installed_tools,
    render_environment,
    write_stamp,
)
from tests.support.fake_tools import BROWSER_PATH, FakeTools, browser_path, write_packages

# The root of the file system that holds the current folder: / or a drive such as C:\.
ROOT = Path(Path.cwd().anchor)
LINUX_HOME = Path.home() / ".local" / "share" / "md-doc-converter"
WINDOWS_HOME = Path.home() / "AppData" / "Local" / "md-doc-converter"
NPM_CI = ("npm", ["ci", "--ignore-scripts", "--omit=optional", "--no-audit", "--no-fund"])
BROWSER_INSTALL = ("puppeteer", ["browsers", "install", "chrome-headless-shell"])
SYSTEMS = [pytest.param(system, id=system.name) for system in PLATFORMS.values()]
Release = Callable[..., install.Archive]


@pytest.fixture
def fakes(fake_tools: FakeTools, monkeypatch: pytest.MonkeyPatch) -> FakeTools:
    """Fake tools whose directory is the whole PATH, so no real pandoc, node or npm is found."""
    fake_tools.bin.mkdir()
    monkeypatch.setenv("PATH", str(fake_tools.bin))
    return fake_tools


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Toolchain:
    """An empty toolchain, which MD_DOC_CONVERTER_HOME selects."""
    tools = Toolchain(tmp_path / "home")
    monkeypatch.setenv("MD_DOC_CONVERTER_HOME", str(tools.root))
    return tools


@pytest.fixture
def on_supported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    def supported() -> bool:
        return True

    monkeypatch.setattr(install, "supported_platform", supported)


@pytest.fixture
def downloads(tmp_path: Path) -> Path:
    folder = tmp_path / "downloads"
    folder.mkdir()
    return folder


@pytest.fixture
def release(tmp_path: Path, fake_tools: FakeTools, downloads: Path) -> Release:
    """Write a fake release of a pinned archive into the downloads folder; return it re-pinned.

    Besides the executable, the archive holds a license and a file that would be written
    outside the folder it is extracted into.
    """

    def make(archive: install.Archive, *, binary: bool = True) -> install.Archive:
        tool = FakeTools(tmp_path / "archive", fake_tools.log)
        tool.write("pandoc")
        bundle = downloads / archive.file
        license_file = tmp_path / "LICENSE"
        license_file.write_text("license", encoding="utf-8")
        members = [(license_file, "./LICENSE"), (license_file, "../escaped")]
        if binary:
            members.insert(0, (tool.path("pandoc"), f"./{archive.member}"))
        if archive.file.endswith(".zip"):
            with zipfile.ZipFile(bundle, "w") as files:
                for path, name in members:
                    files.write(path, name)
        else:
            with tarfile.open(bundle, "w:gz") as files:
                for path, name in members:
                    files.add(path, arcname=name)
        return replace(archive, sha256=hashlib.sha256(bundle.read_bytes()).hexdigest())

    return make


@pytest.mark.parametrize(
    ("system", "environment", "expected"),
    [
        pytest.param(
            LINUX,
            {"MD_DOC_CONVERTER_HOME": str(ROOT / "opt" / "r")},
            ROOT / "opt" / "r",
            id="absolute",
        ),
        pytest.param(LINUX, {"MD_DOC_CONVERTER_HOME": "~/r"}, Path.home() / "r", id="user"),
        pytest.param(LINUX, {"MD_DOC_CONVERTER_HOME": "r"}, Path.cwd() / "r", id="relative"),
        pytest.param(LINUX, {"MD_DOC_CONVERTER_HOME": "  "}, LINUX_HOME, id="blank"),
        pytest.param(
            LINUX,
            {"XDG_DATA_HOME": str(ROOT / "data")},
            ROOT / "data" / "md-doc-converter",
            id="xdg",
        ),
        pytest.param(LINUX, {"XDG_DATA_HOME": "relative"}, LINUX_HOME, id="xdg-relative"),
        pytest.param(LINUX, {"LOCALAPPDATA": str(ROOT / "data")}, LINUX_HOME, id="local-app-data"),
        pytest.param(LINUX, {}, LINUX_HOME, id="default"),
        pytest.param(
            WINDOWS, {"MD_DOC_CONVERTER_HOME": "r"}, Path.cwd() / "r", id="windows-relative"
        ),
        pytest.param(
            WINDOWS,
            {"LOCALAPPDATA": str(ROOT / "data")},
            ROOT / "data" / "md-doc-converter",
            id="windows-local-app-data",
        ),
        pytest.param(
            WINDOWS,
            {"LOCALAPPDATA": "relative"},
            WINDOWS_HOME,
            id="windows-local-app-data-relative",
        ),
        pytest.param(
            WINDOWS, {"XDG_DATA_HOME": str(ROOT / "data")}, WINDOWS_HOME, id="windows-xdg"
        ),
        pytest.param(WINDOWS, {}, WINDOWS_HOME, id="windows-default"),
    ],
)
def test_default_home(system: Platform, environment: dict[str, str], expected: Path) -> None:
    assert default_home(environment, system) == expected


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        pytest.param("linux", "x86_64", LINUX, id="linux"),
        pytest.param("win32", "AMD64", WINDOWS, id="windows"),
        pytest.param("linux", "aarch64", None, id="linux-arm"),
        pytest.param("win32", "ARM64", None, id="windows-arm"),
        pytest.param("darwin", "arm64", None, id="macos"),
    ],
)
def test_setup_supports_linux_and_windows_on_x86_64(
    system: str, machine: str, expected: Platform | None
) -> None:
    assert current_platform(system, machine) is expected


def test_each_supported_system_has_one_platform() -> None:
    # So the system alone picks this computer's platform: host_platform() doesn't ask for the
    # machine type, which takes a WMI query on Windows that every command would wait for.
    systems = [system for system, _ in PLATFORMS]
    assert len(set(systems)) == len(systems)


def test_doctor_names_the_platform_or_those_that_setup_supports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def windows() -> Platform:
        return WINDOWS

    def unsupported() -> None:
        return None

    monkeypatch.setattr(checks, "current_platform", windows)
    assert checks.check_platform() == checks.Check("platform", "ok", "Windows x86_64")
    monkeypatch.setattr(checks, "current_platform", unsupported)
    check = checks.check_platform()
    assert check.status == "warn"
    assert check.detail.endswith("not supported; setup requires Linux x86_64 or Windows x86_64")


@pytest.mark.parametrize("system", SYSTEMS)
def test_the_browser_is_found_in_the_build_for_the_platform(
    system: Platform, tmp_path: Path
) -> None:
    tools = Toolchain(tmp_path / "home", system)
    for build in PLATFORMS.values():
        browser = tools.puppeteer_cache / browser_path(build)
        browser.parent.mkdir(parents=True)
        browser.write_text("", encoding="utf-8")
    assert tools.browser_builds() == [tools.puppeteer_cache / browser_path(system)]
    assert tools.pandoc == tools.bin_dir / system.executable("pandoc")


def test_render_environment_puts_the_toolchain_first(fake_tools: FakeTools, tmp_path: Path) -> None:
    fake_tools.write("node")
    tools = Toolchain(tmp_path / "renderer")
    write_packages(tools)
    base = {"PATH": str(fake_tools.bin), "OTHER": "kept"}
    environment = render_environment(tools, base)
    assert environment["PATH"].split(os.pathsep) == [str(tools.bin_dir), str(fake_tools.bin)]
    assert environment["PUPPETEER_CACHE_DIR"] == str(tools.puppeteer_cache)
    # mermaid.lua runs mermaid-cli's script with Node.js, found on that PATH.
    assert environment["MD_DOC_CONVERTER_NODE"] == str(fake_tools.path("node"))
    mmdc = tools.node_modules / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js"
    assert environment["MD_DOC_CONVERTER_MMDC"] == str(mmdc)
    assert environment["MD_DOC_CONVERTER_MERMAID_CONFIG"] == str(asset_path("mermaid-config.json"))
    assert environment["MD_DOC_CONVERTER_NODE_MODULES"] == str(tools.node_modules)
    assert environment["OTHER"] == "kept"
    assert base["PATH"] == str(fake_tools.bin)


@pytest.mark.parametrize(
    ("manifest", "script"),
    [
        pytest.param({"bin": {"tool": "cli.js"}}, "cli.js", id="by-command"),
        pytest.param({"bin": "cli.js"}, "cli.js", id="named-after-the-package"),
        pytest.param({"bin": {"other": "cli.js"}}, None, id="another-command"),
        pytest.param({"bin": {"tool": "missing.js"}}, None, id="missing-script"),
        pytest.param({}, None, id="no-commands"),
    ],
)
def test_an_npm_command_is_the_script_its_package_names(
    manifest: dict[str, object], script: str | None, tmp_path: Path
) -> None:
    tools = Toolchain(tmp_path / "home")
    package = tools.node_modules / "@scope" / "tool"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    (package / "cli.js").write_text("", encoding="utf-8")
    expected = None if script is None else package / script
    assert tools.node_script("@scope/tool", "tool") == expected
    assert tools.node_script("@scope/absent", "tool") is None


def test_rendering_needs_mermaid_cli_command(fakes: FakeTools, tmp_path: Path) -> None:
    fakes.write()
    tools = fakes.toolchain(tmp_path / "home")
    (tools.node_modules / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js").unlink()
    problems = checks.render_problems(tools, pdf=False)
    assert problems == [f"npm packages: missing mmdc. {checks.REINSTALL_FIX}"]


def test_stamp_tracks_the_bundled_manifests(tmp_path: Path) -> None:
    tools = Toolchain(tmp_path / "home")
    assert not packages_current(tools)
    tools.node_modules.mkdir(parents=True)
    write_stamp(tools)
    assert packages_current(tools)
    tools.stamp.write_text("stale\n", encoding="utf-8")
    assert not packages_current(tools)
    write_stamp(tools)
    shutil.rmtree(tools.node_modules)
    assert not packages_current(tools)


def test_doctor_is_ready_with_a_complete_toolchain(
    fakes: FakeTools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fakes.write()
    tools = fakes.toolchain(tmp_path / "home")
    monkeypatch.setenv("MD_DOC_CONVERTER_HOME", str(tools.root))
    assert checks.main([]) == 0
    report = capsys.readouterr().out
    assert f"Toolchain: {tools.root}" in report
    assert "Ready:" in report
    for name in ("pandoc", "node", "npm packages", "browser"):
        assert re.search(rf"\n  ok +{name} ", report)


@pytest.mark.usefixtures("fakes", "home")
def test_doctor_lists_a_fix_for_each_missing_part(capsys: pytest.CaptureFixture[str]) -> None:
    assert checks.main([]) == 1
    report = capsys.readouterr().out
    assert "-> Run `md-doc-converter setup`, or `setup --no-render`" in report
    assert "Install Node.js 22.13 or newer" in report
    assert "-> Run `md-doc-converter setup`\n" in report
    assert "Not ready to render" in report


def test_fixes_install_with_the_package_manager_of_the_system() -> None:
    fixes = (checks.NODE_FIX, checks.GIT_FIX, checks.LIBREOFFICE_FIX, checks.POPPLER_FIX)
    # Windows has winget; elsewhere, each distribution has its own package manager.
    if os.name == "nt":
        assert all("`winget install " in fix for fix in fixes)
    else:
        assert not any("winget" in fix for fix in fixes)


def test_poppler_needs_each_command_that_emf_conversion_runs(fakes: FakeTools) -> None:
    unconverted = "EMF/WMF drawings in Word documents are kept in their original format"
    assert checks.check_poppler() == checks.Check(
        "poppler", "warn", f"not found; {unconverted}", checks.POPPLER_FIX
    )
    fakes.write("pdftocairo")
    cairo = fakes.path("pdftocairo")
    assert checks.check_poppler().detail == f"no pdfinfo beside {cairo}; {unconverted}"
    fakes.write("pdfinfo")
    assert checks.check_poppler() == checks.Check(
        "poppler",
        "warn",
        f"no pdftotext beside {cairo}; the text of converted EMF/WMF drawings is not searchable",
        checks.POPPLER_FIX,
    )
    fakes.write("pdftotext")
    assert checks.check_poppler() == checks.Check("poppler", "ok", str(cairo))


def test_versions_are_checked(fakes: FakeTools) -> None:
    fakes.write(versions={"node": "v20.11.1", "pandoc": "pandoc 3.1.9"})
    node = checks.check_node()
    pandoc = checks.check_pandoc()
    assert node.status == "error"
    assert "20.11.1 is older than 22.13" in node.detail
    assert pandoc.status == "warn"
    assert f"3.1.9 ({fakes.path('pandoc')}); md-doc-converter is tested with" in pandoc.detail
    assert PANDOC_VERSION in pandoc.detail
    assert "setup --no-render" in pandoc.fix


def test_the_pinned_pandoc_comes_before_any_other(
    fakes: FakeTools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fakes.write(versions={"pandoc": "pandoc 3.1.9"})
    tools = fakes.toolchain(tmp_path / "home")
    monkeypatch.setenv("MD_DOC_CONVERTER_HOME", str(tools.root))
    prefer_installed_tools()
    prefer_installed_tools()
    assert os.environ["PATH"].split(os.pathsep) == [str(tools.bin_dir), str(fakes.bin)]
    pandoc = checks.check_pandoc()
    assert pandoc.status == "ok"
    assert pandoc.detail == f"{PANDOC_VERSION} ({tools.pandoc})"


def test_preferring_a_missing_toolchain_changes_nothing(tmp_path: Path) -> None:
    environment = {"MD_DOC_CONVERTER_HOME": str(tmp_path / "none"), "PATH": "/usr/bin"}
    prefer_installed_tools(environment)
    assert environment["PATH"] == "/usr/bin"


def test_the_toolchain_is_on_path_once_however_it_is_spelled(tmp_path: Path) -> None:
    tools = Toolchain(tmp_path / "home")
    tools.bin_dir.mkdir(parents=True)
    folder = str(tools.bin_dir)
    entries = [folder + os.sep, folder.upper(), "other"]
    environment = {"MD_DOC_CONVERTER_HOME": str(tools.root), "PATH": os.pathsep.join(entries)}
    prefer_installed_tools(environment)
    # Windows names a folder in any case; elsewhere, the upper-case spelling is another folder.
    others = ["other"] if os.name == "nt" else [folder.upper(), "other"]
    assert environment["PATH"].split(os.pathsep) == [folder, *others]


def test_render_problems_name_each_missing_part(fakes: FakeTools, tmp_path: Path) -> None:
    fakes.write()
    problems = checks.render_problems(Toolchain(tmp_path / "home"), pdf=True)
    assert [problem.split(":")[0] for problem in problems] == ["npm packages", "browser"]
    assert all("md-doc-converter setup" in problem for problem in problems)


def test_html_needs_no_pdf_printer(fakes: FakeTools, tmp_path: Path) -> None:
    fakes.write()
    tools = fakes.toolchain(tmp_path / "home")
    shutil.rmtree(tools.node_modules / "puppeteer")
    assert checks.render_problems(tools, pdf=False) == []
    assert "missing puppeteer" in checks.render_problems(tools, pdf=True)[0]
    shutil.rmtree(tools.node_modules / "katex")
    assert "missing katex" in checks.render_problems(tools, pdf=False)[0]


def test_only_an_executable_chrome_headless_shell_counts_as_the_browser(tmp_path: Path) -> None:
    tools = Toolchain(tmp_path / "home")
    (tools.puppeteer_cache / "chrome" / "linux-131.0.6778.204").mkdir(parents=True)
    browser = tools.puppeteer_cache / BROWSER_PATH
    browser.parent.mkdir(parents=True)
    # Windows runs a file by its extension, and elsewhere by its permission to run.
    inert = browser.with_suffix("") if os.name == "nt" else browser
    inert.write_text("", encoding="utf-8")
    inert.chmod(0o644)
    missing = checks.check_browser(tools)
    assert missing.status == "error"
    assert missing.detail == f"no chrome-headless-shell in {tools.puppeteer_cache}"
    assert "setup --force" in missing.fix
    browser.write_text("", encoding="utf-8")
    browser.chmod(0o755)
    assert checks.check_browser(tools).status == "ok"


def test_rendering_needs_pandoc(fakes: FakeTools, tmp_path: Path) -> None:
    fakes.write("node", "npm")
    tools = fakes.toolchain(tmp_path / "home")
    tools.pandoc.unlink()
    problems = checks.render_problems(tools, pdf=False)
    assert len(problems) == 1
    assert "pandoc: not installed. Run `md-doc-converter setup`" in problems[0]


def test_npm_packages_and_one_browser_install_from_the_bundled_lockfile_once(
    fakes: FakeTools, tmp_path: Path
) -> None:
    npm = str(fakes.write() / "npm")
    tools = Toolchain(tmp_path / "home")
    (tools.puppeteer_cache / "chrome" / "linux-120.0.0.0").mkdir(parents=True)
    assert install.install_packages(tools, npm, force=False)
    assert not install.install_packages(tools, npm, force=False)
    assert install.install_packages(tools, npm, force=True)
    for name in MANIFESTS:
        assert (tools.node_dir / name).read_bytes() == (MANIFEST_DIR / name).read_bytes()
    assert packages_current(tools)
    calls = fakes.calls()
    assert [(call["tool"], call["arguments"]) for call in calls] == 2 * [NPM_CI, BROWSER_INSTALL]
    for call in calls:
        assert call["cwd"] == str(tools.node_dir)
        environment = call["environment"]
        assert isinstance(environment, dict)
        assert environment["PUPPETEER_CACHE_DIR"] == str(tools.puppeteer_cache)
    assert [path.name for path in tools.puppeteer_cache.iterdir()] == ["chrome-headless-shell"]
    browser = checks.check_browser(tools)
    assert browser.status == "ok"
    assert browser.detail == f"chrome-headless-shell 131.0.6778.204 ({tools.puppeteer_cache})"


@pytest.mark.parametrize(
    ("tool", "message"), [("npm", "npm ci failed"), ("puppeteer", "browser install failed")]
)
def test_failed_install_leaves_no_stamp(
    tool: str, message: str, fakes: FakeTools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    npm = str(fakes.write() / "npm")
    tools = Toolchain(tmp_path / "home")
    monkeypatch.setenv("FAKE_TOOL_FAIL", tool)
    with pytest.raises(install.SetupError, match=message):
        install.install_packages(tools, npm, force=False)
    assert not tools.stamp.exists()
    assert not packages_current(tools)


def test_a_browser_install_that_installs_nothing_fails(
    fakes: FakeTools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(tools: Toolchain) -> checks.Check:
        return checks.Check("browser", "error", "missing")

    npm = str(fakes.write() / "npm")
    tools = Toolchain(tmp_path / "home")
    monkeypatch.setattr(install, "check_browser", missing)
    with pytest.raises(install.SetupError, match="PUPPETEER_SKIP_DOWNLOAD"):
        install.install_packages(tools, npm, force=False)
    assert not tools.stamp.exists()


def test_the_pinned_archive_installs_only_its_verified_executable(
    release: Release, downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = release(install.PANDOC)
    tools = Toolchain(tmp_path / "home")
    download = mock.Mock()
    monkeypatch.setattr(install.urllib.request, "urlopen", download)
    assert install.install_executable(tools, archive, force=False, downloads=downloads)
    assert not install.install_executable(tools, archive, force=False)
    download.assert_not_called()
    assert [path.name for path in tools.bin_dir.iterdir()] == [tools.pandoc.name]
    assert checks.installed_version(tools.pandoc) == PANDOC_VERSION
    assert not (tmp_path / "escaped").exists()
    assert not (tools.root / "escaped").exists()


def test_each_platform_has_a_pinned_pandoc_release_archive() -> None:
    assert install.PANDOC is install.PANDOC_ARCHIVES[host_platform()]
    assert set(install.PANDOC_ARCHIVES) == set(PLATFORMS.values())
    for system, archive in install.PANDOC_ARCHIVES.items():
        assert archive.version == PANDOC_VERSION
        assert archive.url == (
            f"https://github.com/jgm/pandoc/releases/download/{PANDOC_VERSION}/{archive.file}"
        )
        assert archive.executable == system.executable("pandoc")
        assert re.fullmatch(r"[0-9a-f]{64}", archive.sha256)
    assert install.PANDOC_ARCHIVES[LINUX].file.endswith(".tar.gz")
    assert install.PANDOC_ARCHIVES[WINDOWS].file.endswith(".zip")


@pytest.mark.parametrize("system", SYSTEMS)
def test_only_the_executable_is_extracted_from_either_kind_of_archive(
    system: Platform, release: Release, downloads: Path, tmp_path: Path
) -> None:
    archive = release(install.PANDOC_ARCHIVES[system])
    work = tmp_path / "work"
    work.mkdir()
    executable = work / archive.executable
    install._extract_executable(archive, downloads / archive.file, executable)
    fake = FakeTools(tmp_path / "archive", tmp_path / "log").path("pandoc")
    assert executable.read_bytes() == fake.read_bytes()
    assert [path.name for path in work.iterdir()] == [archive.executable]
    assert not (tmp_path / "escaped").exists()


def test_a_link_in_a_zip_archive_is_not_installed(downloads: Path, tmp_path: Path) -> None:
    archive = install.PANDOC_ARCHIVES[WINDOWS]
    link = zipfile.ZipInfo(str(archive.member))
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(downloads / archive.file, "w") as files:
        files.writestr(link, "/usr/bin/pandoc")
    with pytest.raises(install.SetupError, match="does not contain"):
        install._extract_executable(archive, downloads / archive.file, tmp_path / "pandoc.exe")
    assert not (tmp_path / "pandoc.exe").exists()


def test_a_pandoc_that_cannot_be_replaced_is_reported(
    release: Release, downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy(self: Path, target: Path) -> Path:
        raise PermissionError(13, "The process cannot access the file", str(target))

    archive = release(install.PANDOC)
    tools = Toolchain(tmp_path / "home")
    monkeypatch.setattr(Path, "replace", busy)
    with pytest.raises(install.SetupError, match="Close the programs that run it"):
        install.install_executable(tools, archive, force=False, downloads=downloads)
    assert not tools.pandoc.exists()


def test_checksum_mismatch_installs_nothing(
    release: Release, downloads: Path, tmp_path: Path
) -> None:
    archive = replace(release(install.PANDOC), sha256="0" * 64)
    tools = Toolchain(tmp_path / "home")
    with pytest.raises(install.SetupError, match=r"expected 0{64}"):
        install.install_executable(tools, archive, force=False, downloads=downloads)
    assert not tools.pandoc.exists()
    assert list(tools.bin_dir.iterdir()) == []


def test_archive_without_the_executable_is_rejected(
    release: Release, downloads: Path, tmp_path: Path
) -> None:
    archive = release(install.PANDOC, binary=False)
    tools = Toolchain(tmp_path / "home")
    with pytest.raises(install.SetupError, match=f"does not contain {archive.member}"):
        install.install_executable(tools, archive, force=False, downloads=downloads)
    assert not tools.pandoc.exists()


def test_download_failure_suggests_a_downloads_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = mock.Mock(side_effect=urllib.error.URLError("network unreachable"))
    monkeypatch.setattr(install.urllib.request, "urlopen", failure)
    tools = Toolchain(tmp_path / "home")
    with pytest.raises(install.SetupError, match="--downloads FOLDER"):
        install.install_executable(tools, install.PANDOC, force=False)
    assert not tools.pandoc.exists()


@pytest.mark.parametrize(
    ("supported", "message"),
    [
        pytest.param(
            False, "setup requires Linux x86_64 or Windows x86_64", id="unsupported-platform"
        ),
        pytest.param(True, "--no-render", id="old-node"),
    ],
)
def test_setup_checks_the_platform_and_node_before_installing(
    supported: bool,
    message: str,
    fakes: FakeTools,
    home: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def platform() -> bool:
        return supported

    fakes.write(versions={"node": "v20.11.1"})
    monkeypatch.setattr(install, "supported_platform", platform)
    assert install.main([]) == 1
    assert message in capsys.readouterr().err
    assert fakes.calls() == []
    assert not home.root.exists()


@pytest.mark.usefixtures("fakes", "on_supported_platform")
def test_no_render_installs_only_pandoc_without_node(
    release: Release,
    downloads: Path,
    home: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(install, "PANDOC", release(install.PANDOC))
    assert install.main(["--no-render", "--downloads", str(downloads)]) == 0
    assert [path.name for path in home.bin_dir.iterdir()] == [home.pandoc.name]
    assert not home.node_dir.exists()
    assert "Rendering needs a full `setup`" in capsys.readouterr().out


@pytest.mark.usefixtures("on_supported_platform")
def test_setup_installs_a_complete_toolchain(
    fakes: FakeTools,
    release: Release,
    downloads: Path,
    home: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fakes.write()
    monkeypatch.setattr(install, "PANDOC", release(install.PANDOC))
    assert install.main(["--downloads", str(downloads)]) == 0
    prefer_installed_tools()
    assert checks.render_problems(home, pdf=True) == []
    assert checks.check_pandoc().detail == f"{PANDOC_VERSION} ({home.pandoc})"
    assert "md-doc-converter doctor" in capsys.readouterr().out


@pytest.mark.usefixtures("on_supported_platform")
def test_downloads_must_be_a_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert install.main(["--downloads", str(tmp_path / "missing")]) == 1
    assert "--downloads must be a folder" in capsys.readouterr().err
