import json
import tempfile
import unittest
from pathlib import Path

from rtl_agent.backends.base import ModelBackend, ModelResult
from rtl_agent.tools.simulator import SimulationResult
from rtl_agent.tools.verilator import LintResult
from rtl_agent.workflows.repair_simulation import repair_simulation

RTL = "module demo(input logic a, output logic y); assign y = a; endmodule\n"
FIXED_RTL = "module demo(input logic a, output logic y); assign y = ~a; endmodule\n"
TB = "module demo_tb; demo dut(); initial begin $display(\"TEST_PASS\"); $display(\"TEST_FAIL\"); $fatal; $finish; end endmodule\n"
FIXED_TB = "module demo_tb; demo dut(); initial begin if (0) begin $display(\"TEST_FAIL\"); $fatal; end $display(\"TEST_PASS\"); $finish; end endmodule\n"


class FakeBackend(ModelBackend):
    def __init__(self, responses=()) -> None:
        self.responses = list(responses)
        self.calls = 0
    def generate(self, prompt: str) -> ModelResult:
        self.calls += 1
        return ModelResult(self.responses.pop(0), "fake", "model", {})


def sim(passed=False, compile_error="", run="TEST_FAIL") -> SimulationResult:
    return SimulationResult(not compile_error, passed, 1 if compile_error else 0,
        0 if not compile_error else None, "", compile_error, run, "",
        ["verilator"], ["binary"] if not compile_error else [], "build", "binary",
        0.1, None if passed else "failed")


class SequenceSimulator:
    def __init__(self, results): self.results, self.calls = list(results), 0
    def __call__(self, *_args):
        value = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        if isinstance(value, Exception): raise value
        return value


class RepairSimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.design = self.root / "designs/demo"; self.design.mkdir(parents=True)
        data = {"design_name":"demo","top_module":"demo","rtl_filename":"demo.sv",
            "tb_filename":"demo_tb.sv","tb_top_module":"demo_tb","spec_file":"spec.md",
            "testplan_file":"testplan.md","max_repair_attempts":2,
            "max_simulation_repair_attempts":2}
        (self.design/"design.json").write_text(json.dumps(data)); (self.design/"spec.md").write_text("Invert a.")
        (self.design/"testplan.md").write_text("Check inversion.")
        self.rtl=self.root/"runs/demo/rtl/demo.sv"; self.tb=self.root/"runs/demo/tb/demo_tb.sv"
        self.rtl.parent.mkdir(parents=True); self.tb.parent.mkdir(parents=True)
        self.rtl.write_text(RTL); self.tb.write_text(TB)
        self.lint=lambda *_: LintResult(True,0,"","",["verilator"],0.1)
    def tearDown(self): self.temp.cleanup()
    def classifier(self, category, confidence=.9):
        return json.dumps({"category":category,"confidence":confidence,"summary":"classified",
                           "evidence":["TEST_FAIL"],"target_file":category if category in ("rtl","testbench") else "none"})
    def test_initial_pass_has_no_backend_call(self):
        backend=FakeBackend(); result=repair_simulation(backend,self.design,self.root,SequenceSimulator([sim(True,run="TEST_PASS")]),self.lint)
        self.assertTrue(result.passed); self.assertEqual(backend.calls,0)
    def test_environment_never_calls_backend_or_changes_files(self):
        backend=FakeBackend(); result=repair_simulation(backend,self.design,self.root,SequenceSimulator([RuntimeError("g++ not found on PATH")]),self.lint)
        self.assertEqual(result.final_failure_category,"environment"); self.assertEqual(backend.calls,0); self.assertEqual(self.rtl.read_text(),RTL)
    def test_deterministic_rtl_repair_then_pass(self):
        error=f"%Error: {self.rtl.resolve()}:1: syntax"
        backend=FakeBackend([FIXED_RTL]); result=repair_simulation(backend,self.design,self.root,SequenceSimulator([sim(False,error),sim(True,run="TEST_PASS")]),self.lint)
        self.assertTrue(result.passed); self.assertEqual(result.rtl_repair_attempts,1); self.assertEqual(self.tb.read_text(),TB)
    def test_deterministic_tb_repair_then_pass(self):
        error=f"%Error: {self.tb.resolve()}:1: syntax"
        backend=FakeBackend([FIXED_TB]); result=repair_simulation(backend,self.design,self.root,SequenceSimulator([sim(False,error),sim(True,run="TEST_PASS")]),self.lint)
        self.assertTrue(result.passed); self.assertEqual(result.testbench_repair_attempts,1); self.assertEqual(self.rtl.read_text(),RTL)
    def test_model_classifies_rtl_then_repairs(self):
        backend=FakeBackend([self.classifier("rtl"),FIXED_RTL]); result=repair_simulation(backend,self.design,self.root,SequenceSimulator([sim(),sim(True,run="TEST_PASS")]),self.lint)
        self.assertTrue(result.passed); self.assertEqual(backend.calls,2)
    def test_model_classifies_tb_then_repairs(self):
        backend=FakeBackend([self.classifier("testbench"),FIXED_TB]); result=repair_simulation(backend,self.design,self.root,SequenceSimulator([sim(),sim(True,run="TEST_PASS")]),self.lint)
        self.assertTrue(result.passed); self.assertEqual(result.testbench_repair_attempts,1)
    def test_malformed_or_low_confidence_does_not_modify(self):
        for response in ("bad json", self.classifier("rtl",.5)):
            self.rtl.write_text(RTL); backend=FakeBackend([response])
            result=repair_simulation(backend,self.design,self.root,SequenceSimulator([sim()]),self.lint)
            self.assertFalse(result.passed); self.assertEqual(self.rtl.read_text(),RTL); self.assertTrue(result.report_path.is_file())
    def test_invalid_repair_preserves_original(self):
        backend=FakeBackend([self.classifier("rtl"),"no module"])
        result=repair_simulation(backend,self.design,self.root,SequenceSimulator([sim()]),self.lint)
        self.assertFalse(result.passed); self.assertEqual(self.rtl.read_text(),RTL)
    def test_changed_rtl_interface_is_rejected(self):
        changed="module demo(input logic other, output logic y); assign y=other; endmodule"
        backend=FakeBackend([self.classifier("rtl"),changed])
        result=repair_simulation(backend,self.design,self.root,SequenceSimulator([sim()]),self.lint)
        self.assertFalse(result.passed); self.assertEqual(self.rtl.read_text(),RTL)
        self.assertIn("interface", result.error_message or "")
    def test_tb_without_pass_is_rejected(self):
        bad="module demo_tb; demo dut(); initial begin $display(\"TEST_FAIL\"); $fatal; $finish; end endmodule"
        backend=FakeBackend([self.classifier("testbench"),bad]); result=repair_simulation(backend,self.design,self.root,SequenceSimulator([sim()]),self.lint)
        self.assertFalse(result.passed); self.assertEqual(self.tb.read_text(),TB)
    def test_repair_loop_is_bounded(self):
        responses=[self.classifier("rtl"),FIXED_RTL,self.classifier("rtl"),FIXED_RTL,
                   self.classifier("rtl")]
        backend=FakeBackend(responses); simulator=SequenceSimulator([sim()])
        result=repair_simulation(backend,self.design,self.root,simulator,self.lint)
        self.assertFalse(result.passed); self.assertEqual(result.total_attempts,2)
        self.assertEqual(simulator.calls,3)


if __name__ == "__main__": unittest.main()
