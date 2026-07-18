import json
import tempfile
import unittest
from pathlib import Path

from rtl_agent.backends.base import ModelBackend, ModelResult
from rtl_agent.workflows.generate_testbench import generate_testbench


TB = """module demo_tb;
  demo dut();
  initial begin $display("TEST_PASS"); $finish; end
endmodule
"""


class FakeBackend(ModelBackend):
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def generate(self, prompt: str) -> ModelResult:
        self.calls += 1
        return ModelResult(self.response, "fake", "tb-model", {})


class GenerateTestbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.design = self.root / "designs/demo"
        self.design.mkdir(parents=True)
        config = {
            "design_name": "demo", "top_module": "demo",
            "rtl_filename": "demo.sv", "tb_filename": "demo_tb.sv",
            "tb_top_module": "demo_tb", "spec_file": "spec.md",
            "testplan_file": "testplan.md", "max_repair_attempts": 2,
        }
        (self.design / "design.json").write_text(json.dumps(config), encoding="utf-8")
        (self.design / "spec.md").write_text("A demo DUT.\n", encoding="utf-8")
        (self.design / "testplan.md").write_text("Check the demo.\n", encoding="utf-8")
        self.rtl = self.root / "runs/demo/rtl/demo.sv"
        self.rtl.parent.mkdir(parents=True)
        self.rtl.write_text("module demo; endmodule\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def report_path(self) -> Path:
        return self.root / "runs/demo/reports/testbench_generation.json"

    def test_generates_self_checking_testbench_and_success_report(self) -> None:
        result = generate_testbench(FakeBackend(TB), self.design, self.root)
        self.assertTrue(result.success)
        self.assertEqual(result.tb_path.read_text(encoding="utf-8"), TB)
        self.assertTrue(json.loads(self.report_path.read_text())["generation_success"])

    def test_rejects_wrong_module_name(self) -> None:
        result = generate_testbench(
            FakeBackend("module wrong; demo dut(); endmodule"), self.design, self.root
        )
        self.assertFalse(result.success)
        self.assertFalse(result.tb_path.exists())

    def test_rejects_response_without_module(self) -> None:
        result = generate_testbench(FakeBackend("explanation only"), self.design, self.root)
        self.assertFalse(result.success)
        self.assertIn("module", result.error_message or "")

    def test_missing_rtl_writes_failure_report(self) -> None:
        self.rtl.unlink()
        result = generate_testbench(FakeBackend(TB), self.design, self.root)
        self.assertFalse(result.success)
        self.assertTrue(self.report_path.is_file())

    def test_missing_testplan_writes_failure_report(self) -> None:
        (self.design / "testplan.md").unlink()
        result = generate_testbench(FakeBackend(TB), self.design, self.root)
        self.assertFalse(result.success)
        self.assertFalse(json.loads(self.report_path.read_text())["generation_success"])

    def test_existing_testbench_is_preserved_in_history(self) -> None:
        tb_path = self.root / "runs/demo/tb/demo_tb.sv"
        tb_path.parent.mkdir(parents=True)
        old = "module demo_tb; demo old(); endmodule\n"
        tb_path.write_text(old, encoding="utf-8")
        result = generate_testbench(FakeBackend(TB), self.design, self.root)
        self.assertTrue(result.success)
        history = self.root / "runs/demo/tb/history/attempt_0.sv"
        self.assertEqual(history.read_text(encoding="utf-8"), old)


if __name__ == "__main__":
    unittest.main()
