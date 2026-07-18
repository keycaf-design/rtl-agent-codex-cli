import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rtl_agent.tools.simulator import (
    SimulationCompileError,
    SimulationExecutionError,
    SimulatorNotFoundError,
    run_verilator_simulation,
)


class SimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        source = self.root / "source"
        source.mkdir()
        self.rtl = source / "demo.sv"
        self.tb = source / "demo_tb.sv"
        self.rtl.write_text("module demo; endmodule\n", encoding="utf-8")
        self.tb.write_text("module demo_tb; demo dut(); endmodule\n", encoding="utf-8")
        self.build = self.root / "runs/demo/build/verilator"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, run_code: int = 0, run_out: str = "TEST_PASS", run_err: str = ""):
        def execute(command, **_kwargs):
            if command[0] == "verilator":
                executable = self.build / "Vdemo_tb"
                executable.write_text("binary", encoding="utf-8")
                executable.chmod(0o755)
                return subprocess.CompletedProcess(command, 0, "compile", "")
            return subprocess.CompletedProcess(command, run_code, run_out, run_err)
        with patch("rtl_agent.tools.simulator.shutil.which", return_value="/usr/bin/verilator"), \
             patch("rtl_agent.tools.simulator.subprocess.run", side_effect=execute) as mocked:
            result = run_verilator_simulation(self.rtl, self.tb, "demo_tb", self.build)
        return result, mocked

    def test_compile_and_test_pass(self) -> None:
        result, _ = self._run()
        self.assertTrue(result.compile_passed)
        self.assertTrue(result.simulation_passed)

    def test_compile_failure_does_not_run(self) -> None:
        completed = subprocess.CompletedProcess([], 2, "", "compile error")
        with patch("rtl_agent.tools.simulator.shutil.which", return_value="verilator"), \
             patch("rtl_agent.tools.simulator.subprocess.run", return_value=completed) as run:
            result = run_verilator_simulation(self.rtl, self.tb, "demo_tb", self.build)
        self.assertFalse(result.compile_passed)
        self.assertEqual(run.call_count, 1)

    def test_runtime_nonzero_is_failure(self) -> None:
        result, _ = self._run(run_code=1, run_out="TEST_PASS")
        self.assertFalse(result.simulation_passed)

    def test_missing_test_pass_is_failure(self) -> None:
        result, _ = self._run(run_out="completed")
        self.assertFalse(result.simulation_passed)

    def test_test_fail_is_failure(self) -> None:
        result, _ = self._run(run_out="TEST_PASS\nTEST_FAIL")
        self.assertFalse(result.simulation_passed)

    @patch("rtl_agent.tools.simulator.shutil.which", return_value=None)
    def test_verilator_missing(self, _which: Mock) -> None:
        with self.assertRaises(SimulatorNotFoundError):
            run_verilator_simulation(self.rtl, self.tb, "demo_tb", self.build)

    @patch(
        "rtl_agent.tools.simulator.shutil.which",
        side_effect=lambda executable: (
            "/usr/bin/verilator" if executable == "verilator" else None
        ),
    )
    def test_cpp_compiler_missing(self, _which: Mock) -> None:
        with patch.dict("rtl_agent.tools.simulator.os.environ", {}, clear=True):
            with self.assertRaisesRegex(SimulatorNotFoundError, "C\\+\\+ compiler"):
                run_verilator_simulation(self.rtl, self.tb, "demo_tb", self.build)

    @patch("rtl_agent.tools.simulator.subprocess.run")
    @patch("rtl_agent.tools.simulator.shutil.which", return_value="verilator")
    def test_compile_timeout(self, _which: Mock, run: Mock) -> None:
        run.side_effect = subprocess.TimeoutExpired(["verilator"], 180)
        with self.assertRaises(SimulationCompileError):
            run_verilator_simulation(self.rtl, self.tb, "demo_tb", self.build)

    def test_runtime_timeout(self) -> None:
        def execute(command, **_kwargs):
            if command[0] == "verilator":
                executable = self.build / "Vdemo_tb"
                executable.write_text("binary", encoding="utf-8")
                executable.chmod(0o755)
                return subprocess.CompletedProcess(command, 0, "", "")
            raise subprocess.TimeoutExpired(command, 60)
        with patch("rtl_agent.tools.simulator.shutil.which", return_value="verilator"), \
             patch("rtl_agent.tools.simulator.subprocess.run", side_effect=execute):
            with self.assertRaises(SimulationExecutionError):
                run_verilator_simulation(self.rtl, self.tb, "demo_tb", self.build)

    def test_command_and_stale_build_handling(self) -> None:
        self.build.mkdir(parents=True)
        stale = self.build / "stale"
        stale.write_text("old", encoding="utf-8")
        result, mocked = self._run()
        self.assertFalse(stale.exists())
        self.assertEqual(result.compile_command[:4], ["verilator", "--binary", "--timing", "--Wall"])
        self.assertIn("-Wno-fatal", result.compile_command)
        self.assertIn("--Mdir", result.compile_command)
        self.assertEqual(mocked.call_count, 2)


if __name__ == "__main__":
    unittest.main()
