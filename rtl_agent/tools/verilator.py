from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class VerilatorNotFoundError(RuntimeError):
    """Verilator is not available on PATH."""


class VerilatorExecutionError(RuntimeError):
    """Verilator could not be executed to completion."""


@dataclass(frozen=True)
class LintResult:
    passed: bool
    return_code: int
    stdout: str
    stderr: str
    command: list[str]
    duration_seconds: float


def run_verilator_lint(
    rtl_file: Path,
    top_module: str,
    timeout_seconds: int = 60,
) -> LintResult:
    rtl_path = Path(rtl_file).resolve()
    if not rtl_path.is_file():
        raise VerilatorExecutionError(f"RTL file does not exist: {rtl_path}")
    if shutil.which("verilator") is None:
        raise VerilatorNotFoundError(
            "Verilator was not found on PATH. Install it before running verification."
        )

    command = [
        "verilator", "--lint-only", "--Wall",
        "--top-module", top_module, str(rtl_path),
    ]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerilatorExecutionError(
            f"Verilator lint exceeded {timeout_seconds} seconds"
        ) from exc
    except OSError as exc:
        raise VerilatorExecutionError(f"Could not execute Verilator: {exc}") from exc

    return LintResult(
        passed=completed.returncode == 0,
        return_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        command=command,
        duration_seconds=time.perf_counter() - started,
    )
