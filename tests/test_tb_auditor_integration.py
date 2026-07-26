import os
import unittest
from pathlib import Path

from rtl_agent.backends.codex_cli import CodexCLIBackend
from rtl_agent.workflows.audit_testbench import audit_testbench


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("RUN_CODEX_TB_AUDIT_INTEGRATION") == "1",
    "set RUN_CODEX_TB_AUDIT_INTEGRATION=1 to invoke the real Codex auditor",
)
class CodexTBAuditorIntegrationTests(unittest.TestCase):
    def test_counter_testbench_is_approved(self) -> None:
        backend = CodexCLIBackend(project_dir=PROJECT_ROOT)
        result = audit_testbench(
            backend,
            PROJECT_ROOT / "designs" / "counter",
            PROJECT_ROOT,
        )
        self.assertEqual(
            result.status,
            "APPROVE",
            f"{result.summary}: {result.error_message}",
        )


if __name__ == "__main__":
    unittest.main()
