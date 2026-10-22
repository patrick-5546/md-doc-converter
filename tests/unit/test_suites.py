from __future__ import annotations

import contextlib
import io
import os
import unittest
from unittest import mock

from md_doc_converter.toolchain.checks import Check
from md_doc_converter.toolchain.environment import PANDOC_VERSION
from tests import __main__ as suites
from tests.support.project import PROJECT_ROOT

TOOLCHAIN_CHECKS = ("check_packages", "check_browser", "check_pandoc_katex")


def toolchain_checks(status: str = "ok") -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    for name in TOOLCHAIN_CHECKS:
        label = name.removeprefix("check_")
        stack.enter_context(
            mock.patch.object(
                suites, name, return_value=Check(label, status, "detail", "Run setup")
            )
        )
    return stack


def pandoc_check(
    status: str = "ok", detail: str = "detail"
) -> contextlib.AbstractContextManager[object]:
    return mock.patch.object(
        suites, "check_pandoc", return_value=Check("pandoc", status, detail, "Run setup")
    )


class TestSuiteSelection(unittest.TestCase):
    def test_core_does_not_require_render_dependencies(self) -> None:
        def which(name: str) -> str | None:
            self.assertEqual(name, "git", f"Unexpected render dependency probe: {name}")
            return f"/bin/{name}"

        with (
            mock.patch.object(suites.shutil, "which", side_effect=which),
            pandoc_check(),
            mock.patch.object(suites, "toolchain", side_effect=AssertionError("render toolchain")),
        ):
            self.assertEqual(suites.missing_dependencies("core"), [])

    def test_core_reports_required_native_dependencies(self) -> None:
        with (
            mock.patch.object(suites.shutil, "which", return_value=None),
            pandoc_check("error", "not installed"),
        ):
            self.assertEqual(
                suites.missing_dependencies("core"),
                [
                    "git",
                    f"pandoc {PANDOC_VERSION}: not installed (run `uv run poe setup-pandoc`)",
                ],
            )

    def test_core_requires_the_pinned_pandoc(self) -> None:
        with (
            mock.patch.object(suites.shutil, "which", return_value="/bin/git"),
            pandoc_check("warn", "3.1.9 (/usr/bin/pandoc)"),
        ):
            missing = suites.missing_dependencies("core")
        expected = f"pandoc {PANDOC_VERSION}: 3.1.9 (/usr/bin/pandoc) ({suites.SETUP_PANDOC})"
        self.assertEqual(missing, [expected])

    def test_tests_use_the_checkout_toolchain_unless_another_is_set(self) -> None:
        for home, expected in ((None, PROJECT_ROOT / ".toolchain"), ("/elsewhere", "/elsewhere")):
            environment = {"PATH": "/usr/bin"}
            if home is not None:
                environment["MD_DOC_CONVERTER_HOME"] = home
            with (
                self.subTest(home=home),
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(suites, "prefer_installed_tools") as prefer,
            ):
                suites.use_project_toolchain()
                self.assertEqual(os.environ["MD_DOC_CONVERTER_HOME"], str(expected))
            prefer.assert_called_once_with()

    def test_render_and_all_require_native_conversion_tools(self) -> None:
        available = {name: f"/bin/{name}" for name in ("git", "node")}
        for selection in ("render", "all"):
            with (
                self.subTest(selection=selection),
                mock.patch.object(suites.shutil, "which", side_effect=available.get),
                pandoc_check(),
                toolchain_checks(),
            ):
                self.assertEqual(
                    suites.missing_dependencies(selection),
                    ["pdftocairo", "libreoffice/soffice (EMF regression coverage)"],
                )

    def test_render_accepts_both_office_executable_names(self) -> None:
        for office in ("libreoffice", "soffice"):
            available = {name: f"/bin/{name}" for name in ("git", "node", "pdftocairo", office)}
            with (
                self.subTest(office=office),
                mock.patch.object(suites.shutil, "which", side_effect=available.get),
                pandoc_check(),
                toolchain_checks(),
            ):
                self.assertEqual(suites.missing_dependencies("render"), [])

    def test_render_reports_each_missing_toolchain_part(self) -> None:
        with (
            mock.patch.object(suites.shutil, "which", return_value="/bin/tool"),
            pandoc_check(),
            toolchain_checks("error"),
        ):
            missing = suites.missing_dependencies("render")
        self.assertEqual(
            missing,
            [
                "packages: detail (Run setup)",
                "browser: detail (Run setup)",
                "pandoc_katex: detail (Run setup)",
            ],
        )

    def test_missing_dependencies_fail_before_discovery(self) -> None:
        with (
            mock.patch.object(suites, "missing_dependencies", return_value=["pandoc"]),
            mock.patch.object(suites, "load_suite") as load,
            contextlib.redirect_stderr(io.StringIO()) as messages,
        ):
            self.assertEqual(suites.main(["core"]), 2)
        load.assert_not_called()
        self.assertIn("prerequisites missing: pandoc", messages.getvalue())

    def test_empty_suite_is_not_success(self) -> None:
        with (
            mock.patch.object(suites, "missing_dependencies", return_value=[]),
            mock.patch.object(suites, "load_suite", return_value=unittest.TestSuite()),
            contextlib.redirect_stderr(io.StringIO()) as messages,
        ):
            self.assertEqual(suites.main(["core"]), 2)
        self.assertIn("no core tests discovered", messages.getvalue())

    def test_selected_skips_are_failures(self) -> None:
        class SkippedCase(unittest.TestCase):
            def runTest(self) -> None:
                self.skipTest("missing required tool")

        with (
            mock.patch.object(suites, "missing_dependencies", return_value=[]),
            mock.patch.object(
                suites, "load_suite", return_value=unittest.TestSuite([SkippedCase()])
            ),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as messages,
        ):
            self.assertEqual(suites.main(["core"]), 1)
        self.assertIn("selected suites must not skip", messages.getvalue())

    def test_passing_selected_suite_succeeds(self) -> None:
        with (
            mock.patch.object(suites, "missing_dependencies", return_value=[]),
            mock.patch.object(
                suites,
                "load_suite",
                return_value=unittest.TestSuite([unittest.FunctionTestCase(lambda: None)]),
            ),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(suites.main(["core"]), 0)

    def test_discovery_partitions_are_explicit_and_disjoint(self) -> None:
        self.assertEqual(suites.SUITES["core"], ("unit", "integration"))
        self.assertEqual(suites.SUITES["render"], ("rendering",))
        self.assertEqual(suites.SUITES["all"], (*suites.SUITES["core"], *suites.SUITES["render"]))

    def test_real_native_cases_are_discovered_only_by_render_suites(self) -> None:
        def case_ids(suite: unittest.TestSuite) -> list[str]:
            result: list[str] = []
            for case in suite:
                if isinstance(case, unittest.TestSuite):
                    result.extend(case_ids(case))
                else:
                    self.assertIsInstance(case, unittest.TestCase)
                    result.append(case.id())
            return result

        core = case_ids(suites.load_suite("core"))
        render = case_ids(suites.load_suite("render"))
        combined = case_ids(suites.load_suite("all"))
        self.assertFalse(set(core) & set(render))
        self.assertCountEqual(combined, [*core, *render])
        for name in (
            "test_real_synthetic_emf_conversion",
            "test_real_synthetic_emf_pdf_fallback",
            "test_zero_timeout_is_accepted",
        ):
            self.assertFalse(any(case.endswith("." + name) for case in core))
            self.assertEqual(sum(case.endswith("." + name) for case in render), 1)


if __name__ == "__main__":
    unittest.main()
