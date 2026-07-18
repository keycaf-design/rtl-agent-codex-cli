import json
import tempfile
import unittest
from pathlib import Path

from rtl_agent.tools.simulator import SimulationResult
from rtl_agent.workflows.simulate import simulate_design


def simulation_result(compile_passed: bool, simulation_passed: bool) -> SimulationResult:
    return SimulationResult(
        compile_passed=compile_passed,
        simulation_passed=simulation_passed,
        compile_return_code=0 if compile_passed else 1,
        run_return_code=0 if compile_passed else None,
        compile_stdout="compiled" if compile_passed else "",
        compile_stderr="" if compile_passed else "compile error",
        run_stdout="TEST_PASS" if simulation_passed else "TEST_FAIL",
        run_stderr="",
        compile_command=["verilator", "--binary"],
        run_command=["Vdemo_tb"] if compile_passed else [],
        build_directory="build",
        executable_path="Vdemo_tb" if compile_passed else None,
        duration_seconds=0.1,
        failure_reason=None if simulation_passed else "fixture failure",
    )


class SimulateWorkflowTests(unittest.TestCase):
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
        (self.design / "spec.md").write_text("Demo.\n", encoding="utf-8")
        (self.design / "testplan.md").write_text("Check demo.\n", encoding="utf-8")
        self.rtl = self.root / "runs/demo/rtl/demo.sv"
        self.tb = self.root / "runs/demo/tb/demo_tb.sv"
        self.rtl.parent.mkdir(parents=True)
        self.tb.parent.mkdir(parents=True)
        self.rtl.write_text("module demo; endmodule\n", encoding="utf-8")
        self.tb.write_text("module demo_tb; demo dut(); endmodule\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def report_path(self) -> Path:
        return self.root / "runs/demo/reports/simulation.json"

    def runner(self, compile_passed: bool, simulation_passed: bool):
        return lambda *_args: simulation_result(compile_passed, simulation_passed)

    def test_full_pass(self) -> None:
        result = simulate_design(self.design, self.root, self.runner(True, True))
        self.assertEqual(result.final_result, "PASS")
        self.assertEqual(json.loads(self.report_path.read_text())["final_result"], "PASS")

    def test_compile_failure(self) -> None:
        result = simulate_design(self.design, self.root, self.runner(False, False))
        self.assertFalse(result.compile_passed)
        self.assertEqual(result.final_result, "FAIL")
        self.assertEqual(result.compile_return_code, 1)

    def test_simulation_failure(self) -> None:
        result = simulate_design(self.design, self.root, self.runner(True, False))
        self.assertTrue(result.compile_passed)
        self.assertFalse(result.simulation_passed)

    def test_missing_testbench_writes_report(self) -> None:
        self.tb.unlink()
        result = simulate_design(self.design, self.root, self.runner(True, True))
        self.assertEqual(result.final_result, "FAIL")
        self.assertIn("testbench", result.error_message or "")
        self.assertTrue(self.report_path.is_file())

    def test_runner_exception_writes_report(self) -> None:
        def broken(*_args):
            raise RuntimeError("simulator broke")
        result = simulate_design(self.design, self.root, broken)
        self.assertEqual(result.final_result, "FAIL")
        report = json.loads(self.report_path.read_text())
        self.assertEqual(report["error_message"], "simulator broke")


if __name__ == "__main__":
    unittest.main()
