import hashlib
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
        self.write_approved_audit()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def report_path(self) -> Path:
        return self.root / "runs/demo/reports/simulation.json"

    def runner(self, compile_passed: bool, simulation_passed: bool):
        return lambda *_args: simulation_result(compile_passed, simulation_passed)

    def write_approved_audit(self, **overrides) -> None:
        report = {
            "schema_valid": True,
            "decision": "APPROVE",
            "tb_path": "runs/demo/tb/demo_tb.sv",
            "tb_sha256": hashlib.sha256(self.tb.read_bytes()).hexdigest(),
        }
        report.update(overrides)
        path = self.root / "runs/demo/reports/tb_audit.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report), encoding="utf-8")

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

    def test_missing_audit_blocks_runner_and_prints_rerun_command(self) -> None:
        (self.root / "runs/demo/reports/tb_audit.json").unlink()
        calls = 0

        def runner(*_args):
            nonlocal calls
            calls += 1
            return simulation_result(True, True)

        result = simulate_design(self.design, self.root, runner)
        self.assertEqual(calls, 0)
        self.assertIn("python3 main.py audit-tb --design designs/demo",
                      result.error_message or "")
        self.assertFalse(json.loads(self.report_path.read_text())["tb_audit_gate_passed"])

    def test_rejected_audit_blocks_runner(self) -> None:
        self.write_approved_audit(decision="REJECT")
        calls = 0

        def runner(*_args):
            nonlocal calls
            calls += 1
            return simulation_result(True, True)

        result = simulate_design(self.design, self.root, runner)
        self.assertEqual(calls, 0)
        self.assertIn("not APPROVE", result.error_message or "")

    def test_invalid_audit_schema_blocks_runner(self) -> None:
        self.write_approved_audit(schema_valid=False)
        calls = 0

        def runner(*_args):
            nonlocal calls
            calls += 1
            return simulation_result(True, True)

        result = simulate_design(self.design, self.root, runner)
        self.assertEqual(calls, 0)
        self.assertIn("schema is not valid", result.error_message or "")

    def test_changed_testbench_hash_blocks_runner(self) -> None:
        self.tb.write_text(self.tb.read_text() + "// changed\n", encoding="utf-8")
        calls = 0

        def runner(*_args):
            nonlocal calls
            calls += 1
            return simulation_result(True, True)

        result = simulate_design(self.design, self.root, runner)
        self.assertEqual(calls, 0)
        self.assertIn("hash does not match", result.error_message or "")

    def test_different_audited_path_blocks_runner(self) -> None:
        self.write_approved_audit(tb_path="runs/demo/tb/other.sv")
        result = simulate_design(
            self.design, self.root, self.runner(True, True)
        )
        self.assertFalse(result.compile_passed)
        self.assertIn("path does not match", result.error_message or "")

    def test_structured_testplan_requires_runtime_markers(self) -> None:
        (self.design / "testplan.md").write_text("## TP_A\nDrive and check demo.\n")
        result = simulate_design(self.design, self.root, self.runner(True, True))
        self.assertFalse(result.simulation_passed)
        report = json.loads(self.report_path.read_text())
        self.assertEqual(report["missing_passed_testcase_ids"], ["TP_A"])

    def test_structured_runtime_coverage_passes(self) -> None:
        (self.design / "testplan.md").write_text("## TP_A\nDrive and check demo.\n")
        covered = simulation_result(True, True)
        covered = SimulationResult(**{
            **covered.__dict__,
            "run_stdout": "TESTCASE_BEGIN: TP_A\nTESTCASE_PASS: TP_A\nTEST_PASS",
        })
        result = simulate_design(self.design, self.root, lambda *_: covered)
        self.assertTrue(result.simulation_passed)


if __name__ == "__main__":
    unittest.main()
