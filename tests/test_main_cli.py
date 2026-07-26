import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main


class AuditCLItests(unittest.TestCase):
    def test_audit_cli_has_distinct_status_and_exit_codes(self) -> None:
        project_root = Path(main.__file__).resolve().parent
        report_path = project_root / "runs/counter/reports/tb_audit.json"
        cases = (
            ("APPROVE", 0),
            ("REJECT", 2),
            ("ERROR", 3),
        )
        for status, exit_code in cases:
            with self.subTest(status=status), patch(
                "sys.argv",
                ["main.py", "audit-tb", "--design", "designs/counter"],
            ), patch("main.CodexCLIBackend"), patch(
                "main.audit_testbench",
                return_value=SimpleNamespace(
                    status=status,
                    exit_code=exit_code,
                    report_path=report_path,
                ),
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    actual = main.main()
                self.assertEqual(actual, exit_code)
                self.assertEqual(
                    output.getvalue(),
                    f"TB_AUDIT_{status}\n"
                    "report: runs/counter/reports/tb_audit.json\n",
                )


if __name__ == "__main__":
    unittest.main()
