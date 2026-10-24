"""The test suites' policies: tests/support/suites.py and the hooks in tests/conftest.py."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from md_doc_converter.toolchain.checks import Check
from md_doc_converter.toolchain.environment import PANDOC_VERSION
from tests.support import suites
from tests.support.project import PROJECT_ROOT

TOOLCHAIN_CHECKS = ("check_packages", "check_browser")


def tools(monkeypatch: pytest.MonkeyPatch, *available: str, toolchain: str = "ok") -> None:
    """Make only *available* commands exist, and every toolchain check report *toolchain*."""

    def which(name: str, path: str | None = None) -> str | None:
        return f"/bin/{name}" if name in available else None

    def libreoffice() -> str | None:
        return which("libreoffice") or which("soffice")

    monkeypatch.setattr(suites.executables, "which", which)
    monkeypatch.setattr(suites.executables, "libreoffice", libreoffice)
    for name in TOOLCHAIN_CHECKS:
        check = Check(name.removeprefix("check_"), toolchain, "detail", "Run setup")

        def reported(_tools: object, check: Check = check) -> Check:
            return check

        monkeypatch.setattr(suites, name, reported)


@pytest.fixture
def pinned_pandoc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(suites, "check_pandoc", lambda: Check("pandoc", "ok", PANDOC_VERSION))


@pytest.mark.usefixtures("pinned_pandoc")
def test_the_core_suite_needs_git_but_no_render_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools(monkeypatch, "git")
    monkeypatch.setattr(suites, "toolchain", lambda: pytest.fail("render toolchain checked"))
    assert suites.missing_prerequisites(render=False) == []
    tools(monkeypatch)
    assert suites.missing_prerequisites(render=False) == ["git"]


@pytest.mark.parametrize(
    ("status", "detail"), [("error", "not installed"), ("warn", "3.1.9 (/usr/bin/pandoc)")]
)
def test_every_suite_needs_the_pinned_pandoc(
    monkeypatch: pytest.MonkeyPatch, status: str, detail: str
) -> None:
    tools(monkeypatch, "git")
    monkeypatch.setattr(suites, "check_pandoc", lambda: Check("pandoc", status, detail))
    assert suites.missing_prerequisites(render=False) == [
        f"pandoc {PANDOC_VERSION}: {detail} (run `make setup-pandoc`)"
    ]


@pytest.mark.usefixtures("pinned_pandoc")
def test_the_render_suite_needs_its_native_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    tools(monkeypatch, "git", "node")
    assert suites.missing_prerequisites(render=True) == [
        *suites.POPPLER,
        "libreoffice/soffice (EMF regression coverage)",
    ]


@pytest.mark.parametrize("office", ["libreoffice", "soffice"])
@pytest.mark.usefixtures("pinned_pandoc")
def test_either_office_command_will_do(monkeypatch: pytest.MonkeyPatch, office: str) -> None:
    tools(monkeypatch, "git", "node", *suites.POPPLER, office)
    assert suites.missing_prerequisites(render=True) == []


@pytest.mark.usefixtures("pinned_pandoc")
def test_the_render_suite_names_each_missing_toolchain_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools(monkeypatch, "git", "node", *suites.POPPLER, "soffice", toolchain="error")
    assert suites.missing_prerequisites(render=True) == [
        "packages: detail (Run setup)",
        "browser: detail (Run setup)",
    ]


@pytest.mark.parametrize(
    ("home", "expected"),
    [(None, str(PROJECT_ROOT / ".toolchain")), ("/elsewhere", "/elsewhere")],
)
def test_tests_use_the_checkout_toolchain_unless_another_is_set(
    monkeypatch: pytest.MonkeyPatch, home: str | None, expected: str
) -> None:
    preferred: list[dict[str, str]] = []

    def prefer(environment: dict[str, str]) -> None:
        preferred.append(dict(environment))

    monkeypatch.setattr(suites, "prefer_installed_tools", prefer)
    environment = {"PATH": "/usr/bin"}
    if home is not None:
        environment["MD_DOC_CONVERTER_HOME"] = home
    suites.use_project_toolchain(environment)
    assert environment["MD_DOC_CONVERTER_HOME"] == expected
    assert preferred == [environment]


@pytest.mark.parametrize(
    ("arguments", "render"),
    [
        ([], False),
        (["tests/unit", "tests/integration"], False),
        (["tests/unit/test_suites.py::test_x"], False),
        (["tests/rendering"], True),
        (["tests/rendering/test_emf.py::EmfWorkflowTest::test_x"], True),
        (["tests/unit", "tests/rendering/test_emf.py"], True),
        (["tests"], True),
        (["."], True),
    ],
)
def test_the_rendering_suite_is_selected_by_any_path_that_includes_it(
    arguments: list[str], render: bool
) -> None:
    assert suites.selects_rendering(arguments, PROJECT_ROOT) is render


@pytest.fixture
def project(pytester: pytest.Pytester) -> pytest.Pytester:
    """A pytester directory with the suites' conftest.py."""
    pytester.makeconftest((PROJECT_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))
    return pytester


def test_a_skipped_test_fails_the_run(project: pytest.Pytester) -> None:
    project.makepyfile(
        """
        import pytest

        def test_skipped():
            pytest.skip("missing tool")

        @pytest.mark.xfail(reason="known")
        def test_expected_failure():
            assert False
        """
    )
    result = project.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(failed=1, xfailed=1)
    result.stdout.fnmatch_lines(["*tests must not skip: Skipped: missing tool*"])


def test_missing_prerequisites_stop_the_run_before_collection(
    project: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected: list[bool] = []

    def missing(*, render: bool) -> list[str]:
        selected.append(render)
        return ["git", "pdftocairo"]

    monkeypatch.setattr(suites, "missing_prerequisites", missing)
    project.makepyfile("def test_never_run(): pass")
    result = project.runpytest("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.INTERRUPTED
    result.stdout.fnmatch_lines(["*prerequisites missing: git, pdftocairo*"])
    assert "passed" not in result.stdout.str()
    project.runpytest("-p", "no:cacheprovider", str(suites.RENDERING))
    assert selected == [False, True]
    collected = project.runpytest("-p", "no:cacheprovider", "--collect-only")
    assert collected.ret == pytest.ExitCode.OK
    assert selected == [False, True]


def collected(*paths: str) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *paths],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=True,
    )
    return {line for line in result.stdout.splitlines() if "::" in line}


def test_real_native_cases_are_collected_only_by_the_render_suite() -> None:
    core, render = collected(), collected("tests/rendering")
    assert core
    assert not core & render
    for name in (
        "test_real_synthetic_emf_conversion",
        "test_real_synthetic_emf_pdf_fallback",
        "test_zero_timeout_is_accepted",
    ):
        assert not any(case.endswith("::" + name) for case in core)
        assert sum(case.endswith("::" + name) for case in render) == 1
