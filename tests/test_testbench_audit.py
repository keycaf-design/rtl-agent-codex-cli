import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from rtl_agent.backends.base import ModelBackend, ModelResult
from rtl_agent.tools.testbench_audit import (
    TestbenchAuditError,
    parse_testbench_audit,
)
from rtl_agent.workflows.audit_testbench import (
    audit_testbench,
    check_tb_audit_gate,
)


def p0_response(decision: str = "APPROVE") -> str:
    return json.dumps({
        "decision": decision,
        "summary": "all required behavior is checked"
        if decision == "APPROVE" else "the testbench is unsafe",
        "findings": [] if decision == "APPROVE" else ["required check is missing"],
        "missing_testcases": [],
        "unsafe_patterns": [],
        "required_changes": [],
    })


def legacy_response(status: str = "covered", confidence: float = .9) -> str:
    return json.dumps({
        "coverage_complete": status == "covered",
        "confidence": confidence,
        "summary": "audit",
        "testcases": [{
            "testcase_id": "TP_A",
            "status": status,
            "precondition_evidence": ["TESTCASE_BEGIN: TP_A"],
            "stimulus_evidence": ["1'b0"],
            "observation_evidence": ["TESTCASE_PASS: TP_A"],
            "check_evidence": ["if (1'b0)"],
            "failure_evidence": ["$fatal(1)"],
            "missing_requirements": []
            if status == "covered" else ["stimulus missing"],
        }],
    })


COUNTER_TB_VALID = """module counter_tb;
  logic clk = 0;
  logic rst_n;
  logic enable;
  logic [7:0] count;
  logic [7:0] expected;
  integer failures = 0;
  counter dut(.clk(clk), .rst_n(rst_n), .enable(enable), .count(count));
  always #5 clk = ~clk;

  task run_reset;
    rst_n = 1'b0; enable = 1'b0;
    @(posedge clk); #1;
    if (count !== 8'h00) begin failures++; $display("TEST_FAIL reset"); end
    rst_n = 1'b1;
  endtask
  task run_hold;
    expected = count; enable = 1'b0;
    repeat (3) begin
      @(posedge clk); #1;
      if (count !== expected) begin failures++; $display("TEST_FAIL hold"); end
    end
  endtask
  task run_increment;
    enable = 1'b1;
    repeat (3) begin
      expected = expected + 1'b1; @(posedge clk); #1;
      if (count !== expected) begin failures++; $display("TEST_FAIL increment"); end
    end
  endtask
  task run_wrap;
    enable = 1'b1;
    while (expected != 8'hff) begin
      expected = expected + 1'b1; @(posedge clk); #1;
      if (count !== expected) begin failures++; $display("TEST_FAIL wrap setup"); end
    end
    expected = 8'h00; @(posedge clk); #1;
    if (count !== 8'h00) begin failures++; $display("TEST_FAIL wrap"); end
  endtask
  initial begin
    expected = 0;
    run_reset(); run_hold(); run_increment(); run_hold(); run_wrap();
    if (failures == 0) $display("TEST_PASS");
    else begin $display("TEST_FAIL"); $fatal(1); end
    $finish;
  end
  initial begin #100000; $display("TEST_FAIL timeout"); $fatal(1); end
endmodule
"""


def counter_faults() -> dict[str, str]:
    return {
        "hold_check_removed": COUNTER_TB_VALID.replace(
            'if (count !== expected) begin failures++; $display("TEST_FAIL hold"); end',
            "// FAULT: hold result check removed",
        ),
        "reset_polarity_reversed": COUNTER_TB_VALID.replace(
            "rst_n = 1'b0; enable = 1'b0;",
            "rst_n = 1'b1; enable = 1'b0; // FAULT: wrong reset polarity",
        ),
        "wrap_check_removed": COUNTER_TB_VALID.replace(
            'if (count !== 8\'h00) begin failures++; $display("TEST_FAIL wrap"); end',
            "// FAULT: wraparound result check removed",
        ),
        "stimulus_without_check": COUNTER_TB_VALID.replace(
            'if (count !== expected) begin failures++; $display("TEST_FAIL increment"); end',
            "// FAULT: increment has stimulus but no result check",
        ),
        "unconditional_test_pass": COUNTER_TB_VALID.replace(
            'if (failures == 0) $display("TEST_PASS");',
            '$display("TEST_PASS"); // FAULT: unconditional global pass',
        ),
    }


class FakeBackend(ModelBackend):
    def __init__(self, text: str | Exception) -> None:
        self.text = text
        self.calls = 0
        self.last_prompt = ""

    def generate(self, prompt: str) -> ModelResult:
        self.calls += 1
        self.last_prompt = prompt
        if isinstance(self.text, Exception):
            raise self.text
        return ModelResult(self.text, "fake", "audit-model", {})


class P0TestbenchAuditWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.design = self.root / "designs/counter"
        self.design.mkdir(parents=True)
        config = {
            "design_name": "counter",
            "top_module": "counter",
            "rtl_filename": "counter.sv",
            "tb_filename": "counter_tb.sv",
            "tb_top_module": "counter_tb",
            "spec_file": "spec.md",
            "testplan_file": "testplan.md",
            "max_repair_attempts": 1,
        }
        (self.design / "design.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        (self.design / "spec.md").write_text(
            "Active-low synchronous reset; enable holds or increments with wrap.",
            encoding="utf-8",
        )
        (self.design / "testplan.md").write_text(
            "Check reset, disabled hold, increment, hold after increment, and wrap.",
            encoding="utf-8",
        )
        self.rtl = self.root / "runs/counter/rtl/counter.sv"
        self.tb = self.root / "runs/counter/tb/counter_tb.sv"
        self.rtl.parent.mkdir(parents=True)
        self.tb.parent.mkdir(parents=True)
        self.rtl.write_text(
            "module counter(input logic clk, rst_n, enable, "
            "output logic [7:0] count); "
            "always_ff @(posedge clk) count <= count + 1; endmodule\n",
            encoding="utf-8",
        )
        self.tb.write_text(COUNTER_TB_VALID, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_normal_tb_approve_saves_report_and_raw_log(self) -> None:
        original = self.tb.read_bytes()
        backend = FakeBackend(p0_response())
        result = audit_testbench(backend, self.design, self.root)
        self.assertTrue(result.passed)
        self.assertEqual(result.status, "APPROVE")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.tb.read_bytes(), original)
        self.assertEqual(result.raw_response_path.read_text(), p0_response())
        report = json.loads(result.report_path.read_text())
        self.assertTrue(report["schema_valid"])
        self.assertEqual(report["decision"], "APPROVE")
        self.assertEqual(
            report["tb_sha256"], hashlib.sha256(original).hexdigest()
        )
        self.assertIn("SPECIFICATION (FULL)", backend.last_prompt)
        self.assertIn("TEST PLAN (FULL)", backend.last_prompt)
        self.assertIn("TESTBENCH SHA-256", backend.last_prompt)
        self.assertIn("module counter(input logic", backend.last_prompt)
        self.assertNotIn("always_ff @(posedge clk)", backend.last_prompt)

    def test_faulty_tb_reject_saves_report_and_blocks_gate(self) -> None:
        self.tb.write_text(counter_faults()["hold_check_removed"], encoding="utf-8")
        result = audit_testbench(
            FakeBackend(p0_response("REJECT")), self.design, self.root
        )
        self.assertEqual(result.status, "REJECT")
        self.assertEqual(result.exit_code, 2)
        report = json.loads(result.report_path.read_text())
        self.assertTrue(report["schema_valid"])
        self.assertEqual(report["decision"], "REJECT")
        gate = check_tb_audit_gate("counter", self.tb, self.root)
        self.assertFalse(gate.approved)

    def test_backend_exception_writes_error_report(self) -> None:
        stale_raw = self.root / "runs/counter/logs/tb_audit_raw.txt"
        stale_raw.parent.mkdir(parents=True)
        stale_raw.write_text("stale approval response", encoding="utf-8")
        result = audit_testbench(
            FakeBackend(RuntimeError("backend unavailable")),
            self.design,
            self.root,
        )
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(result.exit_code, 3)
        report = json.loads(result.report_path.read_text())
        self.assertFalse(report["schema_valid"])
        self.assertEqual(report["error_type"], "backend_error")
        self.assertIn("backend unavailable", report["error_message"])
        self.assertTrue(result.raw_response_path.is_file())
        self.assertEqual(result.raw_response_path.read_text(), "")

    def test_missing_tb_fails_safely_and_writes_report(self) -> None:
        self.tb.unlink()
        backend = FakeBackend(p0_response())
        result = audit_testbench(backend, self.design, self.root)
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(backend.calls, 0)
        report = json.loads(result.report_path.read_text())
        self.assertEqual(report["error_type"], "tb_missing")
        self.assertIsNone(report["tb_sha256"])

    def test_malformed_response_is_preserved_and_fails_safely(self) -> None:
        raw = f"explanation\n{p0_response()}"
        result = audit_testbench(FakeBackend(raw), self.design, self.root)
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(result.raw_response_path.read_text(), raw)
        report = json.loads(result.report_path.read_text())
        self.assertFalse(report["schema_valid"])
        self.assertEqual(report["error_type"], "schema_error")

    def test_tb_change_after_approve_blocks_hash_gate(self) -> None:
        result = audit_testbench(
            FakeBackend(p0_response()), self.design, self.root
        )
        self.assertTrue(result.passed)
        self.tb.write_text(COUNTER_TB_VALID + "// changed\n", encoding="utf-8")
        gate = check_tb_audit_gate("counter", self.tb, self.root)
        self.assertFalse(gate.approved)
        self.assertIn("hash", gate.reason or "")

    def test_fault_injection_candidates_are_independently_audited(self) -> None:
        for name, candidate in counter_faults().items():
            with self.subTest(name=name):
                self.tb.write_text(candidate, encoding="utf-8")
                backend = FakeBackend(p0_response("REJECT"))
                result = audit_testbench(backend, self.design, self.root)
                self.assertEqual(result.status, "REJECT")
                self.assertIn("FAULT:", backend.last_prompt)

    def test_normal_counter_fixture_is_approved(self) -> None:
        result = audit_testbench(
            FakeBackend(p0_response()), self.design, self.root
        )
        self.assertEqual(result.status, "APPROVE")


class LegacySemanticAuditParserTests(unittest.TestCase):
    def test_schema_rejects_malformed_and_wrong_ids(self) -> None:
        for text in ("bad", legacy_response().replace("TP_A", "TP_X")):
            with self.assertRaises(TestbenchAuditError):
                parse_testbench_audit(text, ["TP_A"])

    def test_weak_and_low_confidence_are_not_safe(self) -> None:
        self.assertFalse(
            parse_testbench_audit(
                legacy_response("weak"), ["TP_A"]
            ).coverage_complete
        )
        self.assertLess(
            parse_testbench_audit(
                legacy_response("covered", .5), ["TP_A"]
            ).confidence,
            .75,
        )

    def test_schema_rejects_complete_with_weak_case(self) -> None:
        data = json.loads(legacy_response("weak"))
        data["coverage_complete"] = True
        with self.assertRaises(TestbenchAuditError):
            parse_testbench_audit(json.dumps(data), ["TP_A"])

    def test_covered_requires_each_semantic_evidence_kind(self) -> None:
        data = json.loads(legacy_response())
        data["testcases"][0]["precondition_evidence"] = []
        with self.assertRaises(TestbenchAuditError):
            parse_testbench_audit(json.dumps(data), ["TP_A"])

    def test_hallucinated_task_evidence_is_rejected(self) -> None:
        with self.assertRaises(TestbenchAuditError):
            parse_testbench_audit(
                legacy_response(), ["TP_A"], {"TP_A": "if (1'b0) $fatal(1);"}
            )


if __name__ == "__main__":
    unittest.main()
