from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class SimulatorNotFoundError(RuntimeError):
    """Verilator is unavailable for simulation compilation."""


class SimulationCompileError(RuntimeError):
    """Simulation compilation could not be executed."""


class SimulationExecutionError(RuntimeError):
    """The compiled simulation could not be executed."""


@dataclass(frozen=True)
class SimulationResult:
    compile_passed: bool
    simulation_passed: bool
    compile_return_code: int
    run_return_code: int | None
    compile_stdout: str
    compile_stderr: str
    run_stdout: str
    run_stderr: str
    compile_command: list[str]
    run_command: list[str]
    build_directory: str
    executable_path: str | None
    duration_seconds: float
    failure_reason: str | None


def _validate_build_directory(path: Path) -> None:
    if not any(parent.name == "runs" for parent in (path, *path.parents)):
        raise ValueError(f"Build directory must be inside a runs directory: {path}")


def run_verilator_simulation(
    rtl_file: Path,
    tb_file: Path,
    tb_top_module: str,
    build_directory: Path,
    compile_timeout_seconds: int = 180,
    run_timeout_seconds: int = 60,
) -> SimulationResult:
    started = time.perf_counter()
    rtl_path, tb_path = Path(rtl_file).resolve(), Path(tb_file).resolve()
    build_path = Path(build_directory).resolve()
    if not rtl_path.is_file():
        raise SimulationCompileError(f"RTL file does not exist: {rtl_path}")
    if not tb_path.is_file():
        raise SimulationCompileError(f"Testbench file does not exist: {tb_path}")
    _validate_build_directory(build_path)
    if shutil.which("verilator") is None:
        raise SimulatorNotFoundError("Verilator was not found on PATH")
    if build_path.exists():
        shutil.rmtree(build_path)
    build_path.mkdir(parents=True)

    compile_command = [
        "verilator", "--binary", "--timing", "--Wall",
        "--top-module", tb_top_module, "--Mdir", str(build_path),
        str(rtl_path), str(tb_path),
    ]
    try:
        compiled = subprocess.run(
            compile_command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=compile_timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SimulationCompileError(
            f"Verilator compile exceeded {compile_timeout_seconds} seconds"
        ) from exc
    except OSError as exc:
        raise SimulationCompileError(f"Could not execute Verilator: {exc}") from exc

    executable = build_path / f"V{tb_top_module}"
    if compiled.returncode != 0:
        return SimulationResult(
            False, False, compiled.returncode, None, compiled.stdout,
            compiled.stderr, "", "", compile_command, [], str(build_path),
            None, time.perf_counter() - started, "Verilator compilation failed",
        )
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SimulationCompileError(
            f"Compiled simulation executable is missing or not executable: {executable}"
        )

    run_command = [str(executable)]
    try:
        executed = subprocess.run(
            run_command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=run_timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SimulationExecutionError(
            f"Simulation exceeded {run_timeout_seconds} seconds"
        ) from exc
    except OSError as exc:
        raise SimulationExecutionError(f"Could not run simulation: {exc}") from exc

    combined = f"{executed.stdout}\n{executed.stderr}"
    has_pass = "TEST_PASS" in executed.stdout
    has_fail = "TEST_FAIL" in combined
    simulation_passed = executed.returncode == 0 and has_pass and not has_fail
    if executed.returncode != 0:
        reason = f"Simulation exited with code {executed.returncode}"
    elif has_fail:
        reason = "Simulation emitted TEST_FAIL"
    elif not has_pass:
        reason = "Simulation did not emit TEST_PASS"
    else:
        reason = None
    return SimulationResult(
        True, simulation_passed, compiled.returncode, executed.returncode,
        compiled.stdout, compiled.stderr, executed.stdout, executed.stderr,
        compile_command, run_command, str(build_path), str(executable),
        time.perf_counter() - started, reason,
    )
