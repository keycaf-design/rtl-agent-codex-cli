import json
import tempfile
import unittest
from pathlib import Path

from rtl_agent.tools.simulation_classifier import (
    ClassificationError, deterministic_classification, parse_classification,
)
from rtl_agent.tools.simulator import SimulationResult


def failed(stderr: str) -> SimulationResult:
    return SimulationResult(False, False, 1, None, "", stderr, "", "",
                            ["verilator"], [], "build", None, 0.1, "compile failed")


class SimulationClassifierTests(unittest.TestCase):
    def test_parses_valid_json(self) -> None:
        value = {"category": "rtl", "confidence": 0.9, "summary": "bad RTL",
                 "evidence": ["error"], "target_file": "rtl"}
        result = parse_classification(json.dumps(value))
        self.assertEqual(result.category, "rtl")

    def test_rejects_invalid_json_category_and_confidence(self) -> None:
        for text in ("not json", json.dumps({"category": "other", "confidence": 2,
                    "summary": "x", "evidence": [], "target_file": "none"})):
            with self.assertRaises(ClassificationError):
                parse_classification(text)

    def test_environment_is_deterministic(self) -> None:
        result = deterministic_classification(None, Path("a.sv"), Path("b.sv"),
                                              "g++ was not found on PATH")
        self.assertEqual(result.category, "environment")

    def test_compile_source_location_selects_target(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            rtl, tb = Path(name) / "dut.sv", Path(name) / "tb.sv"
            rtl.write_text("module dut; endmodule")
            tb.write_text("module tb; endmodule")
            rtl_result = deterministic_classification(
                failed(f"%Error: {rtl.resolve()}:3: syntax"), rtl, tb)
            tb_result = deterministic_classification(
                failed(f"%Error: {tb.resolve()}:4: syntax"), rtl, tb)
            self.assertEqual(rtl_result.category, "rtl")
            self.assertEqual(tb_result.category, "testbench")


if __name__ == "__main__":
    unittest.main()
