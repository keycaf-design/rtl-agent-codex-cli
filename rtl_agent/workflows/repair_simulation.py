from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..backends.base import ModelBackend
from ..design_config import load_design_config
from ..tools.file_manager import read_nonempty_text, safe_run_path, write_text
from ..tools.rtl_parser import (
    RTLParseError, extract_rtl, extract_testbench, require_same_interface,
)
from ..tools.simulation_classifier import (
    FailureClassification, deterministic_classification, parse_classification,
)
from ..tools.simulator import SimulationResult, run_verilator_simulation
from ..tools.verilator import LintResult, run_verilator_lint

SimulatorRunner = Callable[[Path, Path, str, Path], SimulationResult]
LintRunner = Callable[[Path, str], LintResult]


@dataclass(frozen=True)
class SimulationRepairResult:
    design_name: str
    passed: bool
    initial_failure_category: str | None
    final_failure_category: str | None
    total_attempts: int
    rtl_repair_attempts: int
    testbench_repair_attempts: int
    final_rtl_path: Path
    final_testbench_path: Path
    report_path: Path
    error_message: str | None


def _log(command: list[str], code: int | None, stdout: str, stderr: str) -> str:
    return (
        f"Command: {' '.join(shlex.quote(x) for x in command)}\n"
        f"Return code: {code}\n\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}\n"
    )


def _classification_prompt(template: str, spec: str, testplan: str, rtl: str,
                           tb: str, compile_log: str, simulation_log: str) -> str:
    return (
        f"{template.rstrip()}\n\nORIGINAL SPECIFICATION:\n{spec.rstrip()}\n\n"
        f"TEST PLAN:\n{testplan.rstrip()}\n\nCURRENT RTL:\n{rtl.rstrip()}\n\n"
        f"CURRENT TESTBENCH:\n{tb.rstrip()}\n\nCOMPILE LOG:\n{compile_log.rstrip()}\n\n"
        f"SIMULATION LOG:\n{simulation_log.rstrip()}\n"
    )


def _repair_prompt(template: str, config, rtl: str, tb: str, compile_log: str,
                   simulation_log: str, classification: FailureClassification) -> str:
    return (
        f"{template.rstrip()}\n\nORIGINAL SPECIFICATION:\n{config.spec.rstrip()}\n\n"
        f"TEST PLAN:\n{config.testplan.rstrip()}\n\nCURRENT DUT RTL:\n{rtl.rstrip()}\n\n"
        f"CURRENT TESTBENCH:\n{tb.rstrip()}\n\nCOMPILE LOG:\n{compile_log.rstrip()}\n\n"
        f"SIMULATION LOG:\n{simulation_log.rstrip()}\n\n"
        f"CLASSIFICATION RESULT:\n{json.dumps(classification.to_dict())}\n"
    )


def _validate_tb_safety(testbench: str, original: str) -> None:
    required = ("TEST_PASS", "TEST_FAIL", "$fatal", "$finish")
    missing = [token for token in required if token not in testbench]
    if missing:
        raise RTLParseError(f"Repaired testbench lacks required safety tokens: {', '.join(missing)}")
    for token in ("TEST_FAIL", "$fatal"):
        if testbench.count(token) < original.count(token):
            raise RTLParseError(f"Repaired testbench weakens existing {token} checks")


def repair_simulation(
    backend: ModelBackend,
    design_dir: Path | str,
    project_root: Path | str,
    simulator_runner: SimulatorRunner = run_verilator_simulation,
    lint_runner: LintRunner = run_verilator_lint,
) -> SimulationRepairResult:
    started_clock, started_at = time.perf_counter(), datetime.now(timezone.utc)
    root = Path(project_root).resolve()
    runs_root = root if root.name == "runs" else root / "runs"
    design_name = Path(design_dir).resolve().name or "unknown-design"
    dut_top = tb_top = ""
    rtl_path = safe_run_path(runs_root, Path(design_name) / "rtl/unknown.sv")
    tb_path = safe_run_path(runs_root, Path(design_name) / "tb/unknown.sv")
    report_path = safe_run_path(runs_root, Path(design_name) / "reports/simulation_repair.json")
    attempts: list[dict] = []
    backend_name, model_name = backend.__class__.__name__, None
    initial_passed = final_passed = False
    initial_category = final_category = None
    rtl_repairs = tb_repairs = 0
    error_message = None
    max_repairs = 0

    try:
        config = load_design_config(design_dir)
        design_name, dut_top, tb_top = config.design_name, config.top_module, config.tb_top_module
        max_repairs = config.max_simulation_repair_attempts
        rtl_path = safe_run_path(runs_root, Path(design_name) / "rtl" / config.rtl_filename)
        tb_path = safe_run_path(runs_root, Path(design_name) / "tb" / config.tb_filename)
        report_path = safe_run_path(runs_root, Path(design_name) / "reports/simulation_repair.json")
        if not rtl_path.is_file() or not tb_path.is_file():
            raise FileNotFoundError("Existing RTL and testbench are required for simulation repair")
        prompt_root = Path(__file__).resolve().parents[1] / "prompts"
        classifier_template = read_nonempty_text(prompt_root / "simulation_classifier.md")
        rtl_template = read_nonempty_text(prompt_root / "rtl_functional_repair.md")
        tb_template = read_nonempty_text(prompt_root / "tb_repair.md")

        for simulation_index in range(max_repairs + 1):
            result: SimulationResult | None = None
            runner_error: str | None = None
            build_path = safe_run_path(runs_root, Path(design_name) / "build/simulation_repair")
            try:
                result = simulator_runner(rtl_path, tb_path, tb_top, build_path)
                compile_text = _log(result.compile_command, result.compile_return_code,
                                    result.compile_stdout, result.compile_stderr)
                run_text = _log(result.run_command, result.run_return_code,
                                result.run_stdout, result.run_stderr)
            except Exception as exc:
                runner_error = str(exc)
                compile_text, run_text = f"ERROR: {runner_error}\n", "Simulation did not run.\n"
            compile_log_path = safe_run_path(
                runs_root, Path(design_name) / "logs" / f"simulation_repair_attempt_{simulation_index}_compile.log")
            run_log_path = safe_run_path(
                runs_root, Path(design_name) / "logs" / f"simulation_repair_attempt_{simulation_index}_run.log")
            write_text(compile_log_path, compile_text)
            write_text(run_log_path, run_text)
            passed = bool(result and result.compile_passed and result.simulation_passed)
            if simulation_index == 0:
                initial_passed = passed
            if passed:
                final_passed = True
                break

            rtl, tb = read_nonempty_text(rtl_path), read_nonempty_text(tb_path)
            classification = deterministic_classification(result, rtl_path, tb_path, runner_error)
            if classification is None:
                classified = backend.generate(_classification_prompt(
                    classifier_template, config.spec, config.testplan, rtl, tb,
                    compile_text, run_text))
                backend_name, model_name = classified.backend_name, classified.model_name
                classification = parse_classification(classified.text)
                if classification.confidence < 0.75:
                    classification = FailureClassification(
                        classification.source, "ambiguous", classification.confidence,
                        "Insufficient confidence to safely select a repair target",
                        classification.evidence, "none")
            initial_category = initial_category or classification.category
            final_category = classification.category
            entry = {
                "attempt_index": simulation_index,
                "classification_source": classification.source,
                "category": classification.category,
                "confidence": classification.confidence,
                "summary": classification.summary,
                "evidence": classification.evidence,
                "target_file": classification.target_file,
                "compile_passed": bool(result and result.compile_passed),
                "simulation_passed": bool(result and result.simulation_passed),
                "compile_log_path": str(compile_log_path.relative_to(runs_root)),
                "simulation_log_path": str(run_log_path.relative_to(runs_root)),
                "repaired_file": None, "repair_backend_name": None,
                "repair_model_name": None, "repair_error": None,
            }
            attempts.append(entry)
            if classification.category in {"environment", "ambiguous"}:
                error_message = classification.summary
                break
            if simulation_index >= max_repairs:
                error_message = "Maximum simulation repair attempts reached"
                break

            try:
                template = rtl_template if classification.category == "rtl" else tb_template
                repaired = backend.generate(_repair_prompt(
                    template, config, rtl, tb, compile_text, run_text, classification))
                backend_name, model_name = repaired.backend_name, repaired.model_name
                entry["repair_backend_name"], entry["repair_model_name"] = repaired.backend_name, repaired.model_name
                if classification.category == "rtl":
                    candidate = extract_rtl(repaired.text, dut_top)
                    require_same_interface(rtl, candidate, dut_top)
                    candidate_path = safe_run_path(
                        runs_root, Path(design_name) / "rtl/candidates" /
                        f"simulation_repair_{simulation_index}" / config.rtl_filename)
                    write_text(candidate_path, candidate)
                    lint = lint_runner(candidate_path, dut_top)
                    if not lint.passed:
                        raise RuntimeError(f"Repaired RTL failed lint: {lint.stderr}")
                    history = safe_run_path(runs_root, Path(design_name) / "rtl/history" /
                                            f"simulation_repair_attempt_{simulation_index}.sv")
                    write_text(history, rtl)
                    write_text(rtl_path, candidate)
                    rtl_repairs += 1
                    entry["repaired_file"] = str(rtl_path.relative_to(runs_root))
                else:
                    candidate = extract_testbench(repaired.text, tb_top, dut_top)
                    require_same_interface(tb, candidate, tb_top)
                    _validate_tb_safety(candidate, tb)
                    history = safe_run_path(runs_root, Path(design_name) / "tb/history" /
                                            f"simulation_repair_attempt_{simulation_index}.sv")
                    write_text(history, tb)
                    write_text(tb_path, candidate)
                    tb_repairs += 1
                    entry["repaired_file"] = str(tb_path.relative_to(runs_root))
            except Exception as exc:
                entry["repair_error"] = str(exc)
                error_message = str(exc)
                break
        if not final_passed and error_message is None:
            error_message = "Simulation repair did not reach PASS"
    except Exception as exc:
        error_message = str(exc)
    finally:
        finished_at = datetime.now(timezone.utc)
        report = {
            "design_name": design_name, "dut_top_module": dut_top,
            "tb_top_module": tb_top, "backend_name": backend_name,
            "model_name": model_name, "repair_success": final_passed,
            "initial_simulation_passed": initial_passed,
            "final_simulation_passed": final_passed,
            "max_repair_attempts": max_repairs,
            "total_attempts": rtl_repairs + tb_repairs,
            "rtl_repair_attempts": rtl_repairs,
            "testbench_repair_attempts": tb_repairs,
            "final_rtl_path": str(rtl_path.relative_to(runs_root)),
            "final_testbench_path": str(tb_path.relative_to(runs_root)),
            "started_at": started_at.isoformat(), "finished_at": finished_at.isoformat(),
            "duration_seconds": time.perf_counter() - started_clock,
            "error_message": error_message, "attempts": attempts,
            "initial_failure_category": initial_category,
            "final_failure_category": final_category,
        }
        write_text(report_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return SimulationRepairResult(
        design_name, final_passed, initial_category, final_category,
        rtl_repairs + tb_repairs, rtl_repairs, tb_repairs, rtl_path, tb_path,
        report_path, error_message)
