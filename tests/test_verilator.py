import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rtl_agent.tools.verilator import (
    VerilatorExecutionError,
    VerilatorNotFoundError,
    run_verilator_lint,
)


class VerilatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.rtl = Path(self.temporary.name) / "demo.sv"
        self.rtl.write_text("module demo; endmodule\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @patch("rtl_agent.tools.verilator.subprocess.run")
    @patch("rtl_agent.tools.verilator.shutil.which", return_value="/usr/bin/verilator")
    def test_lint_pass_and_command(self, _which: Mock, run: Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "clean", "")
        result = run_verilator_lint(self.rtl, "demo")
        self.assertTrue(result.passed)
        self.assertEqual(result.return_code, 0)
        self.assertEqual(
            result.command[:5],
            ["verilator", "--lint-only", "--Wall", "--top-module", "demo"],
        )
        self.assertEqual(result.command[-1], str(self.rtl.resolve()))
        run.assert_called_once()

    @patch("rtl_agent.tools.verilator.subprocess.run")
    @patch("rtl_agent.tools.verilator.shutil.which", return_value="/usr/bin/verilator")
    def test_lint_failure_is_result(self, _which: Mock, run: Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 1, "out", "syntax error")
        result = run_verilator_lint(self.rtl, "demo")
        self.assertFalse(result.passed)
        self.assertEqual(result.stderr, "syntax error")

    @patch("rtl_agent.tools.verilator.shutil.which", return_value=None)
    def test_missing_verilator(self, _which: Mock) -> None:
        with self.assertRaises(VerilatorNotFoundError):
            run_verilator_lint(self.rtl, "demo")

    @patch("rtl_agent.tools.verilator.subprocess.run")
    @patch("rtl_agent.tools.verilator.shutil.which", return_value="/usr/bin/verilator")
    def test_timeout(self, _which: Mock, run: Mock) -> None:
        run.side_effect = subprocess.TimeoutExpired(["verilator"], 60)
        with self.assertRaises(VerilatorExecutionError):
            run_verilator_lint(self.rtl, "demo")


if __name__ == "__main__":
    unittest.main()
