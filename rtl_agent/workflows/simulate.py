from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..design_config import load_design_config
from ..tools.file_manager import safe_run_path, write_text
from ..tools.simulator import SimulationResult, run_verilator_simulation

SimulatorRunner = Callable[[Path, Path, str, Path], SimulationResult]


@dataclass(frozen=True)
class FunctionalVerificationResult:
    design_name: str
    dut_top_module: str
    tb_top_module: str
    compile_passed: bool
    simulation_passed: bool
    final_result: str
    rtl_path: Path
    testbench_path: Path
    compile_log_path: Path
    simulation_log_path: Path
    report_path: Path
    error_message: str | None


def _process_log(command: list[str], return_code: int | None, stdout: str, stderr: str) -> str:
    rendered = " ".join(shlex.quote(part) for part in command)
    return (
        f"Command: {rendered}\nReturn code: {return_code}\n\n"
        f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}\n"
    )


def simulate_design(
    design_dir: Path | str,
    project_root: Path | str,
    simulator_runner: SimulatorRunner = run_verilator_simulation,
) -> FunctionalVerificationResult:
    started_clock = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    root = Path(project_root).resolve()
    runs_root = root if root.name == "runs" else root / "runs"
    design_name = Path(design_dir).resolve().name or "unknown-design"
    dut_top_module = ""
    tb_top_module = ""
    compile_passed = False
    simulation_passed = False
    error_message: str | None = None
    compile_return_code: int | None = None
    run_return_code: int | None = None
    compile_command: list[str] = []
    run_command: list[str] = []
    executable_path: str | None = None
    rtl_path = safe_run_path(runs_root, Path(design_name) / "rtl/unknown.sv")
    tb_path = safe_run_path(runs_root, Path(design_name) / "tb/unknown.sv")
    build_path = safe_run_path(runs_root, Path(design_name) / "build/verilator")
    compile_log_path = safe_run_path(
        runs_root, Path(design_name) / "logs/simulation_compile.log"
    )
    run_log_path = safe_run_path(
        runs_root, Path(design_name) / "logs/simulation_run.log"
    )
    report_path = safe_run_path(
        runs_root, Path(design_name) / "reports/simulation.json"
    )

    try:
        config = load_design_config(design_dir)
        design_name = config.design_name
        dut_top_module = config.top_module
        tb_top_module = config.tb_top_module
        rtl_path = safe_run_path(
            runs_root, Path(design_name) / "rtl" / config.rtl_filename
        )
        tb_path = safe_run_path(
            runs_root, Path(design_name) / "tb" / config.tb_filename
        )
        build_path = safe_run_path(
            runs_root, Path(design_name) / "build/verilator"
        )
        compile_log_path = safe_run_path(
            runs_root, Path(design_name) / "logs/simulation_compile.log"
        )
        run_log_path = safe_run_path(
            runs_root, Path(design_name) / "logs/simulation_run.log"
        )
        report_path = safe_run_path(
            runs_root, Path(design_name) / "reports/simulation.json"
        )
        if not rtl_path.is_file():
            raise FileNotFoundError(f"Generated RTL file does not exist: {rtl_path}")
        if not tb_path.is_file():
            raise FileNotFoundError(f"Generated testbench file does not exist: {tb_path}")

        result = simulator_runner(rtl_path, tb_path, tb_top_module, build_path)
        compile_passed = result.compile_passed
        simulation_passed = result.simulation_passed
        compile_return_code = result.compile_return_code
        run_return_code = result.run_return_code
        compile_command = result.compile_command
        run_command = result.run_command
        executable_path = result.executable_path
        error_message = result.failure_reason
        write_text(
            compile_log_path,
            _process_log(result.compile_command, result.compile_return_code,
                         result.compile_stdout, result.compile_stderr),
        )
        write_text(
            run_log_path,
            _process_log(result.run_command, result.run_return_code,
                         result.run_stdout, result.run_stderr),
        )
    except Exception as exc:
        error_message = str(exc)
        write_text(compile_log_path, f"Simulation setup/execution error: {error_message}\n")
        write_text(run_log_path, f"Simulation did not complete: {error_message}\n")
    finally:
        finished_at = datetime.now(timezone.utc)
        final_result = "PASS" if compile_passed and simulation_passed else "FAIL"
        report = {
            "design_name": design_name,
            "dut_top_module": dut_top_module,
            "tb_top_module": tb_top_module,
            "rtl_path": str(rtl_path.relative_to(runs_root)),
            "testbench_path": str(tb_path.relative_to(runs_root)),
            "compile_passed": compile_passed,
            "simulation_passed": simulation_passed,
            "final_result": final_result,
            "compile_return_code": compile_return_code,
            "run_return_code": run_return_code,
            "compile_command": compile_command,
            "run_command": run_command,
            "compile_log_path": str(compile_log_path.relative_to(runs_root)),
            "simulation_log_path": str(run_log_path.relative_to(runs_root)),
            "build_directory": str(build_path.relative_to(runs_root)),
            "executable_path": executable_path,
            "error_message": error_message,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": time.perf_counter() - started_clock,
        }
        write_text(report_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    return FunctionalVerificationResult(
        design_name, dut_top_module, tb_top_module, compile_passed,
        simulation_passed, final_result, rtl_path, tb_path, compile_log_path,
        run_log_path, report_path, error_message,
    )
