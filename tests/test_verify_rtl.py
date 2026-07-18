import json
import tempfile
import unittest
from pathlib import Path

from rtl_agent.backends.base import ModelBackend, ModelResult
from rtl_agent.tools.verilator import LintResult
from rtl_agent.workflows.verify_rtl import verify_rtl


GOOD_RTL = "module demo(input logic clk); endmodule\n"
FIXED_RTL = "module demo(input logic clk); logic ok; endmodule\n"


class FakeBackend(ModelBackend):
    def __init__(self, response: str = FIXED_RTL) -> None:
        self.response = response
        self.calls = 0

    def generate(self, prompt: str) -> ModelResult:
        self.calls += 1
        return ModelResult(self.response, "fake", "unit-model", {})


def lint(passed: bool, index: int = 0) -> LintResult:
    return LintResult(
        passed, 0 if passed else 1, "", "error" if not passed else "",
        ["verilator", "--lint-only", f"attempt-{index}"], 0.01,
    )


class SequenceLint:
    def __init__(self, results: list[bool]) -> None:
        self.results = results
        self.calls = 0

    def __call__(self, rtl_file: Path, top_module: str) -> LintResult:
        value = self.results[min(self.calls, len(self.results) - 1)]
        result = lint(value, self.calls)
        self.calls += 1
        return result


class VerifyRTLTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.design = self.root / "designs/demo"
        self.design.mkdir(parents=True)
        config = {
            "design_name": "demo", "top_module": "demo",
            "rtl_filename": "demo.sv", "tb_filename": "demo_tb.sv",
            "spec_file": "spec.md", "testplan_file": "testplan.md",
            "max_repair_attempts": 2,
        }
        (self.design / "design.json").write_text(json.dumps(config), encoding="utf-8")
        (self.design / "spec.md").write_text("Keep input clk.\n", encoding="utf-8")
        (self.design / "testplan.md").write_text("Check behavior.\n", encoding="utf-8")
        self.rtl = self.root / "runs/demo/rtl/demo.sv"
        self.rtl.parent.mkdir(parents=True)
        self.rtl.write_text(GOOD_RTL, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def report(self) -> dict[str, object]:
        path = self.root / "runs/demo/reports/verification.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_initial_lint_passes_without_backend(self) -> None:
        backend = FakeBackend()
        result = verify_rtl(backend, self.design, self.root, SequenceLint([True]))
        self.assertTrue(result.passed)
        self.assertEqual(backend.calls, 0)
        self.assertTrue(self.report()["verification_success"])
        self.assertEqual(self.report()["lint_attempts"], 1)

    def test_repair_then_pass_preserves_history(self) -> None:
        backend = FakeBackend()
        result = verify_rtl(backend, self.design, self.root, SequenceLint([False, True]))
        self.assertTrue(result.passed)
        self.assertEqual(backend.calls, 1)
        self.assertEqual(result.repair_attempts, 1)
        history = self.root / "runs/demo/rtl/history/attempt_0.sv"
        self.assertEqual(history.read_text(encoding="utf-8"), GOOD_RTL)
        self.assertEqual(self.rtl.read_text(encoding="utf-8"), FIXED_RTL)

    def test_continuing_failure_stops_at_maximum(self) -> None:
        backend = FakeBackend()
        result = verify_rtl(backend, self.design, self.root, SequenceLint([False]))
        self.assertFalse(result.passed)
        self.assertEqual(result.repair_attempts, 2)
        self.assertEqual(result.lint_attempts, 3)
        self.assertFalse(self.report()["verification_success"])

    def test_missing_rtl_writes_failure_report(self) -> None:
        self.rtl.unlink()
        result = verify_rtl(FakeBackend(), self.design, self.root, SequenceLint([True]))
        self.assertFalse(result.passed)
        self.assertIn("does not exist", result.error_message or "")
        self.assertFalse(self.report()["verification_success"])

    def test_invalid_model_response_does_not_replace_rtl(self) -> None:
        backend = FakeBackend("not systemverilog")
        result = verify_rtl(backend, self.design, self.root, SequenceLint([False]))
        self.assertFalse(result.passed)
        self.assertEqual(self.rtl.read_text(encoding="utf-8"), GOOD_RTL)
        self.assertEqual(result.repair_attempts, 0)
        self.assertIn("module", result.error_message or "")
        self.assertFalse(self.report()["verification_success"])


if __name__ == "__main__":
    unittest.main()
