import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rtl_agent.backends.codex_cli import CodexCLIBackend, CodexCLIError


PROJECT_DIR = Path(__file__).parent.parent.resolve()


class CodexCLIBackendTests(unittest.TestCase):
    @patch("rtl_agent.backends.codex_cli.subprocess.run")
    @patch("rtl_agent.backends.codex_cli.shutil.which", return_value="/usr/bin/codex")
    def test_every_generate_uses_a_new_read_only_ephemeral_process(
        self, _which, run
    ) -> None:
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "FIRST\n", ""),
            subprocess.CompletedProcess([], 0, "SECOND\n", ""),
        ]
        with tempfile.TemporaryDirectory() as name:
            backend = CodexCLIBackend(project_dir=Path(name))
            self.assertEqual(backend.generate("one").text, "FIRST")
            self.assertEqual(backend.generate("two").text, "SECOND")

        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(command[:3], ["codex", "exec", "--ephemeral"])
            self.assertIn("read-only", command)


def main() -> None:
    backend = CodexCLIBackend(
        project_dir=PROJECT_DIR,
        timeout_seconds=300,
    )

    result = backend.generate(
        "Return exactly CODEX_OK and nothing else."
    )

    print(f"Codex response: {result.text!r}")

    if result.text.strip() != "CODEX_OK":
        raise AssertionError(
            f"Expected 'CODEX_OK', received {result.text!r}"
        )

    print("PASS: Codex CLI backend is working.")


if __name__ == "__main__":
    try:
        main()

    except (
        CodexCLIError,
        AssertionError,
        ValueError,
    ) as exc:
        print(f"FAIL: {exc}")
        raise SystemExit(1)
