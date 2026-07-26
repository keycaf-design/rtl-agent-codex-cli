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
from ..tools.runtime_coverage import parse_runtime_coverage
from ..tools.testplan_parser import parse_testplan_if_structured
from .audit_testbench import check_tb_audit_gate

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
    compile_return_code: int | None
    primary_error: str | None


def _process_log(command: list[str], return_code: int | None, stdout: str, stderr: str) -> str:
    rendered = " ".join(shlex.quote(part) for part in command)
    return (
        f"Command: {rendered}\nReturn code: {return_code}\n\n"
        f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}\n"
    )


def _primary_diagnostics(stderr: str, limit: int = 3) -> str | None:
    diagnostics = [
        line.strip() for line in stderr.splitlines()
        if line.lstrip().startswith(("%Error", "%Warning"))
        or "No such file or directory" in line
    ]
    return " | ".join(diagnostics[:limit]) or None


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
    compile_stdout = ""
    compile_stderr = ""
    run_stdout = ""
    run_stderr = ""
    primary_error: str | None = None
    required_ids: list[str] = []
    coverage = None
    audit_gate_passed = False
    audit_gate_reason: str | None = None
    audit_report_path: Path | None = None
    current_tb_sha256: str | None = None
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

        audit_gate = check_tb_audit_gate(design_name, tb_path, root)
        audit_gate_passed = audit_gate.approved
        audit_gate_reason = audit_gate.reason
        audit_report_path = audit_gate.report_path
        current_tb_sha256 = audit_gate.current_tb_sha256
        if not audit_gate.approved:
            raise PermissionError(
                "Simulation blocked by TB audit gate: "
                f"{audit_gate.reason}. Run this first: "
                "python3 main.py audit-tb "
                f"--design designs/{design_name}"
            )

        structured_plan = parse_testplan_if_structured(config.testplan)
        required_ids = structured_plan.required_testcase_ids if structured_plan else []

        result = simulator_runner(rtl_path, tb_path, tb_top_module, build_path)
        compile_passed = result.compile_passed
        simulation_passed = result.simulation_passed
        compile_return_code = result.compile_return_code
        run_return_code = result.run_return_code
        compile_command = result.compile_command
        run_command = result.run_command
        executable_path = result.executable_path
        error_message = result.failure_reason
        compile_stdout = result.compile_stdout
        compile_stderr = result.compile_stderr
        run_stdout = result.run_stdout
        run_stderr = result.run_stderr
        if required_ids and result.compile_passed and result.run_return_code is not None:
            coverage = parse_runtime_coverage(
                f"{result.run_stdout}\n{result.run_stderr}", required_ids
            )
            simulation_passed = simulation_passed and coverage.passed
            if not coverage.passed:
                error_message = "Required testcase coverage was incomplete: " + "; ".join(coverage.errors)
        primary_error = _primary_diagnostics(result.compile_stderr)
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
            "compile_stdout": compile_stdout,
            "compile_stderr": compile_stderr,
            "run_stdout": run_stdout,
            "run_stderr": run_stderr,
            "primary_error": primary_error,
            "error_message": error_message,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": time.perf_counter() - started_clock,
            "required_testcase_ids": required_ids,
            "started_testcase_ids": coverage.started_ids if coverage else [],
            "passed_testcase_ids": coverage.passed_ids if coverage else [],
            "failed_testcase_ids": coverage.failed_ids if coverage else [],
            "missing_started_testcase_ids": coverage.missing_started_ids if coverage else required_ids,
            "missing_passed_testcase_ids": coverage.missing_passed_ids if coverage else required_ids,
            "unknown_testcase_ids": coverage.unknown_ids if coverage else [],
            "testcase_coverage_passed": coverage.passed if coverage else (None if not required_ids else False),
            "tb_audit_gate_passed": audit_gate_passed,
            "tb_audit_gate_reason": audit_gate_reason,
            "tb_audit_report_path": (
                str(audit_report_path.relative_to(runs_root))
                if audit_report_path is not None else None
            ),
            "current_tb_sha256": current_tb_sha256,
        }
        write_text(report_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    return FunctionalVerificationResult(
        design_name, dut_top_module, tb_top_module, compile_passed,
        simulation_passed, final_result, rtl_path, tb_path, compile_log_path,
        run_log_path, report_path, error_message, compile_return_code,
        primary_error,
    )
