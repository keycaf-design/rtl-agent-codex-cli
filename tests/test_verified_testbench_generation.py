import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from rtl_agent.backends.base import ModelBackend, ModelResult
from rtl_agent.workflows.audit_testbench import check_tb_audit_gate
from rtl_agent.workflows.generate_verified_testbench import (
    audit_testbench_candidate,
    generate_verified_testbench,
    promote_approved_candidate,
)


OLD_TB = """module demo_tb;
  logic a, y;
  demo dut(.a(a), .y(y));
  initial begin a = 0; $display("TEST_PASS"); $finish; end
endmodule
"""

REJECTED_TB = """module demo_tb;
  logic a, y;
  demo dut(.a(a), .y(y));
  initial begin
    a = 1;
    // Missing output check.
    $display("TEST_PASS");
    $finish;
  end
endmodule
"""

APPROVED_TB = """module demo_tb;
  logic a, y;
  demo dut(.a(a), .y(y));
  initial begin
    a = 1; #1;
    if (y !== 1) begin $display("TEST_FAIL"); $fatal(1); end
    $display("TEST_PASS");
    $finish;
  end
  initial begin #100; $display("TEST_FAIL timeout"); $fatal(1); end
endmodule
"""


def audit_response(decision: str) -> str:
    rejected = decision == "REJECT"
    return json.dumps({
        "decision": decision,
        "summary": "SUMMARY: output must be checked"
        if rejected else "all checks are complete",
        "findings": ["FINDING: stimulus has no comparison"] if rejected else [],
        "missing_testcases": ["MISSING: output behavior"] if rejected else [],
        "unsafe_patterns": ["UNSAFE: unconditional TEST_PASS"] if rejected else [],
        "required_changes": ["CHANGE: compare y with expected value"]
        if rejected else [],
    })


class SequenceBackend(ModelBackend):
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> ModelResult:
        self.prompts.append(prompt)
        value = self.responses[self.calls]
        self.calls += 1
        if isinstance(value, Exception):
            raise value
        return ModelResult(value, "fake", "fixture-model", {})


class VerifiedTestbenchGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.design = self.root / "designs/demo"
        self.design.mkdir(parents=True)
        config = {
            "design_name": "demo",
            "top_module": "demo",
            "rtl_filename": "demo.sv",
            "tb_filename": "demo_tb.sv",
            "tb_top_module": "demo_tb",
            "spec_file": "spec.md",
            "testplan_file": "testplan.md",
            "max_repair_attempts": 1,
            "max_tb_audit_attempts": 3,
        }
        (self.design / "design.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        (self.design / "spec.md").write_text(
            "Output y shall equal input a.", encoding="utf-8"
        )
        (self.design / "testplan.md").write_text(
            "Drive a and compare y with the expected value.", encoding="utf-8"
        )
        self.rtl = self.root / "runs/demo/rtl/demo.sv"
        self.tb = self.root / "runs/demo/tb/demo_tb.sv"
        self.rtl.parent.mkdir(parents=True)
        self.tb.parent.mkdir(parents=True)
        self.rtl.write_text(
            "module demo(input logic a, output logic y); "
            "assign y = a; endmodule\n",
            encoding="utf-8",
        )
        self.tb.write_text(OLD_TB, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def set_max_attempts(self, value) -> None:
        path = self.design / "design.json"
        config = json.loads(path.read_text())
        config["max_tb_audit_attempts"] = value
        path.write_text(json.dumps(config), encoding="utf-8")

    @property
    def final_report(self) -> Path:
        return self.root / "runs/demo/reports/tb_verified_generation.json"

    def test_reject_feedback_regenerates_then_approves_and_promotes(self) -> None:
        generator = SequenceBackend([REJECTED_TB, APPROVED_TB])
        auditor = SequenceBackend([
            audit_response("REJECT"),
            audit_response("APPROVE"),
        ])

        result = generate_verified_testbench(
            generator, auditor, self.design, self.root
        )

        self.assertEqual(result.status, "APPROVED")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.approved_attempt, 1)
        self.assertEqual(self.tb.read_text(), APPROVED_TB)
        self.assertEqual(
            (self.root / "runs/demo/tb/history/attempt_0.sv").read_text(),
            OLD_TB,
        )
        self.assertEqual(
            (self.root / "runs/demo/tb/candidates/tb_attempt_0.sv").read_text(),
            REJECTED_TB,
        )
        self.assertEqual(
            (self.root / "runs/demo/tb/candidates/tb_attempt_1.sv").read_text(),
            APPROVED_TB,
        )

        regeneration_prompt = generator.prompts[1]
        for expected in (
            "SUMMARY: output must be checked",
            "FINDING: stimulus has no comparison",
            "MISSING: output behavior",
            "UNSAFE: unconditional TEST_PASS",
            "CHANGE: compare y with expected value",
        ):
            self.assertIn(expected, regeneration_prompt)
        self.assertIn(REJECTED_TB.strip(), regeneration_prompt)

        report = json.loads(self.final_report.read_text())
        self.assertEqual(report["status"], "APPROVED")
        self.assertEqual(report["attempts"], 2)
        self.assertFalse(report["attempt_reports"][0]["promoted"])
        self.assertTrue(report["attempt_reports"][1]["promoted"])
        attempt_audit = json.loads(
            (self.root / "runs/demo/reports/tb_attempt_1_audit.json").read_text()
        )
        self.assertEqual(
            attempt_audit["candidate_sha256"],
            hashlib.sha256(APPROVED_TB.encode()).hexdigest(),
        )
        self.assertTrue(attempt_audit["promoted"])
        rejected_generation = json.loads(
            (self.root / "runs/demo/reports/tb_attempt_0_generation.json").read_text()
        )
        self.assertEqual(rejected_generation["audit_decision"], "REJECT")
        self.assertEqual(
            rejected_generation["audit_required_changes"],
            ["CHANGE: compare y with expected value"],
        )
        gate = check_tb_audit_gate("demo", self.tb, self.root)
        self.assertTrue(gate.approved, gate.reason)

    def test_all_rejects_exhaust_without_replacing_active_tb(self) -> None:
        generator = SequenceBackend([
            REJECTED_TB,
            REJECTED_TB.replace("Missing output check.", "Still missing check."),
            REJECTED_TB.replace("Missing output check.", "Third missing check."),
        ])
        auditor = SequenceBackend([audit_response("REJECT")] * 3)

        result = generate_verified_testbench(
            generator, auditor, self.design, self.root
        )

        self.assertEqual(result.status, "EXHAUSTED")
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(self.tb.read_text(), OLD_TB)
        self.assertFalse(
            check_tb_audit_gate("demo", self.tb, self.root).approved
        )
        for attempt in range(3):
            self.assertTrue(
                (self.root / f"runs/demo/tb/candidates/tb_attempt_{attempt}.sv").is_file()
            )
            self.assertTrue(
                (self.root / f"runs/demo/reports/tb_attempt_{attempt}_generation.json").is_file()
            )
            self.assertTrue(
                (self.root / f"runs/demo/reports/tb_attempt_{attempt}_audit.json").is_file()
            )
            self.assertTrue(
                (self.root / f"runs/demo/logs/tb_attempt_{attempt}_generation_raw.txt").is_file()
            )
            self.assertTrue(
                (self.root / f"runs/demo/logs/tb_attempt_{attempt}_audit_raw.txt").is_file()
            )

    def test_generation_backend_failure_fails_safely(self) -> None:
        result = generate_verified_testbench(
            SequenceBackend([RuntimeError("generator unavailable")]),
            SequenceBackend([]),
            self.design,
            self.root,
        )
        self.assertEqual(result.status, "GENERATION_ERROR")
        self.assertEqual(self.tb.read_text(), OLD_TB)
        report = json.loads(self.final_report.read_text())
        self.assertEqual(report["error_type"], "backend_error")
        self.assertTrue(
            (self.root / "runs/demo/logs/tb_attempt_0_generation_raw.txt").is_file()
        )

    def test_malformed_testbench_output_fails_safely(self) -> None:
        result = generate_verified_testbench(
            SequenceBackend(["not a module"]),
            SequenceBackend([]),
            self.design,
            self.root,
        )
        self.assertEqual(result.status, "GENERATION_ERROR")
        self.assertEqual(self.tb.read_text(), OLD_TB)
        self.assertFalse(
            (self.root / "runs/demo/tb/candidates/tb_attempt_0.sv").exists()
        )

    def test_audit_backend_failure_fails_safely(self) -> None:
        result = generate_verified_testbench(
            SequenceBackend([REJECTED_TB]),
            SequenceBackend([RuntimeError("auditor unavailable")]),
            self.design,
            self.root,
        )
        self.assertEqual(result.status, "AUDIT_ERROR")
        self.assertEqual(self.tb.read_text(), OLD_TB)
        attempt = json.loads(
            (self.root / "runs/demo/reports/tb_attempt_0_audit.json").read_text()
        )
        self.assertEqual(attempt["error_type"], "backend_error")

    def test_malformed_audit_json_fails_without_regeneration(self) -> None:
        generator = SequenceBackend([REJECTED_TB])
        result = generate_verified_testbench(
            generator,
            SequenceBackend(["not json"]),
            self.design,
            self.root,
        )
        self.assertEqual(result.status, "AUDIT_ERROR")
        self.assertEqual(generator.calls, 1)
        self.assertEqual(self.tb.read_text(), OLD_TB)

    def test_invalid_audit_schema_fails_without_regeneration(self) -> None:
        invalid = json.dumps({"decision": "REJECT"})
        generator = SequenceBackend([REJECTED_TB])
        result = generate_verified_testbench(
            generator,
            SequenceBackend([invalid]),
            self.design,
            self.root,
        )
        self.assertEqual(result.status, "AUDIT_ERROR")
        self.assertEqual(generator.calls, 1)
        self.assertEqual(self.tb.read_text(), OLD_TB)

    def test_missing_candidate_is_an_audit_error(self) -> None:
        auditor = SequenceBackend([])
        result = audit_testbench_candidate(
            auditor,
            self.design,
            self.root,
            self.root / "runs/demo/tb/candidates/missing.sv",
            attempt=0,
        )
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(result.error_type, "tb_missing")
        self.assertEqual(auditor.calls, 0)
        self.assertTrue(result.report_path.is_file())

    def test_consecutive_duplicate_candidate_hash_stops_loop(self) -> None:
        generator = SequenceBackend([REJECTED_TB, REJECTED_TB])
        auditor = SequenceBackend([audit_response("REJECT")])
        result = generate_verified_testbench(
            generator, auditor, self.design, self.root
        )
        self.assertEqual(result.status, "GENERATION_ERROR")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(auditor.calls, 1)
        self.assertEqual(self.tb.read_text(), OLD_TB)
        report = json.loads(self.final_report.read_text())
        self.assertEqual(report["error_type"], "duplicate_candidate")

    def test_invalid_max_attempt_configuration_is_config_error(self) -> None:
        for value in (0, 11, True, "3"):
            with self.subTest(value=value):
                self.set_max_attempts(value)
                result = generate_verified_testbench(
                    SequenceBackend([]),
                    SequenceBackend([]),
                    self.design,
                    self.root,
                )
                self.assertEqual(result.status, "CONFIG_ERROR")
                self.assertEqual(result.attempts, 0)
                self.set_max_attempts(3)

    def test_max_attempts_defaults_to_three(self) -> None:
        path = self.design / "design.json"
        config = json.loads(path.read_text())
        del config["max_tb_audit_attempts"]
        path.write_text(json.dumps(config), encoding="utf-8")
        result = generate_verified_testbench(
            SequenceBackend([RuntimeError("stop after config load")]),
            SequenceBackend([]),
            self.design,
            self.root,
        )
        self.assertEqual(result.max_attempts, 3)

    def test_candidate_changed_after_audit_cannot_be_promoted(self) -> None:
        candidate = self.root / "runs/demo/tb/candidates/manual.sv"
        candidate.parent.mkdir(parents=True)
        candidate.write_text(APPROVED_TB, encoding="utf-8")
        audited_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
        candidate.write_text(APPROVED_TB + "// changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed after audit"):
            promote_approved_candidate(
                candidate,
                audited_hash,
                self.tb,
                self.root / "runs/demo/tb/history",
            )
        self.assertEqual(self.tb.read_text(), OLD_TB)


if __name__ == "__main__":
    unittest.main()
