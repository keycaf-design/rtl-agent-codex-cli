import os
import unittest
from pathlib import Path

from rtl_agent.backends.base import ModelBackend, ModelResult
from rtl_agent.backends.codex_cli import CodexCLIBackend
from rtl_agent.workflows.generate_verified_testbench import (
    generate_verified_testbench,
)
from rtl_agent.workflows.simulate import simulate_design


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _faulty_counter_candidate() -> str:
    current = (
        PROJECT_ROOT / "runs/counter/tb/counter_tb.sv"
    ).read_text(encoding="utf-8")
    task_start = current.index("task automatic run_TP_HOLD_DISABLED")
    task_end = current.index("endtask", task_start)
    task = current[task_start:task_end]
    if "if (count !== expected_value)" not in task:
        raise AssertionError("Counter TB hold comparison was not found")
    faulty_task = task.replace(
        "if (count !== expected_value)",
        "if (1'b0 /* injected missing hold comparison */)",
        1,
    )
    return current[:task_start] + faulty_task + current[task_end:]


class SeedThenCodexBackend(ModelBackend):
    def __init__(self, codex: CodexCLIBackend) -> None:
        self.codex = codex
        self.calls = 0

    def generate(self, prompt: str) -> ModelResult:
        self.calls += 1
        if self.calls == 1:
            return ModelResult(
                _faulty_counter_candidate(),
                "fault-injection-fixture",
                None,
                {},
            )
        return self.codex.generate(prompt)


@unittest.skipUnless(
    os.environ.get("RUN_CODEX_TB_REGEN_INTEGRATION") == "1",
    "set RUN_CODEX_TB_REGEN_INTEGRATION=1 to invoke real Codex regeneration",
)
class CodexTBRegenerationIntegrationTests(unittest.TestCase):
    def test_reject_regenerate_approve_and_simulate(self) -> None:
        generator = SeedThenCodexBackend(
            CodexCLIBackend(project_dir=PROJECT_ROOT)
        )
        auditor = CodexCLIBackend(project_dir=PROJECT_ROOT)
        result = generate_verified_testbench(
            generator,
            auditor,
            PROJECT_ROOT / "designs/counter",
            PROJECT_ROOT,
        )
        self.assertEqual(
            result.status,
            "APPROVED",
            result.error_message,
        )
        self.assertGreaterEqual(result.attempts, 2)
        simulation = simulate_design(
            PROJECT_ROOT / "designs/counter",
            PROJECT_ROOT,
        )
        self.assertEqual(simulation.final_result, "PASS")


if __name__ == "__main__":
    unittest.main()
