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
from ..tools.file_manager import read_nonempty_text, read_text, safe_run_path, write_text
from ..tools.rtl_parser import extract_rtl
from ..tools.verilator import LintResult, run_verilator_lint

LintRunner = Callable[[Path, str], LintResult]


@dataclass(frozen=True)
class VerificationResult:
    design_name: str
    top_module: str
    passed: bool
    lint_attempts: int
    repair_attempts: int
    final_rtl_path: Path
    report_path: Path
    error_message: str | None


def _lint_log(result: LintResult) -> str:
    command = " ".join(shlex.quote(part) for part in result.command)
    return (
        f"Command: {command}\n"
        f"Return code: {result.return_code}\n"
        f"Duration seconds: {result.duration_seconds:.6f}\n\n"
        f"STDOUT:\n{result.stdout}\n\n"
        f"STDERR:\n{result.stderr}\n"
    )


def verify_rtl(
    backend: ModelBackend,
    design_dir: Path | str,
    project_root: Path | str,
    lint_runner: LintRunner = run_verilator_lint,
) -> VerificationResult:
    started_clock = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    root = Path(project_root).resolve()
    runs_root = root if root.name == "runs" else root / "runs"
    design_name = Path(design_dir).resolve().name or "unknown-design"
    top_module = ""
    max_repairs = 0
    lint_attempts = 0
    repair_attempts = 0
    log_paths: list[str] = []
    backend_name = backend.__class__.__name__
    model_name: str | None = None
    error_message: str | None = None
    passed = False
    rtl_path = safe_run_path(runs_root, Path(design_name) / "rtl/unknown.sv")
    report_path = safe_run_path(
        runs_root, Path(design_name) / "reports/verification.json"
    )

    try:
        config = load_design_config(design_dir)
        design_name, top_module = config.design_name, config.top_module
        max_repairs = config.max_repair_attempts
        if max_repairs < 0:
            raise ValueError("max_repair_attempts must not be negative")
        rtl_path = safe_run_path(
            runs_root, Path(design_name) / "rtl" / config.rtl_filename
        )
        report_path = safe_run_path(
            runs_root, Path(design_name) / "reports/verification.json"
        )
        if not rtl_path.is_file():
            raise FileNotFoundError(f"Generated RTL file does not exist: {rtl_path}")
        repair_template = read_nonempty_text(
            Path(__file__).resolve().parents[1] / "prompts/rtl_repair.md"
        )

        for lint_index in range(max_repairs + 1):
            lint_result = lint_runner(rtl_path, top_module)
            lint_attempts += 1
            log_path = safe_run_path(
                runs_root,
                Path(design_name) / "logs" / f"lint_attempt_{lint_index}.log",
            )
            write_text(log_path, _lint_log(lint_result))
            log_paths.append(str(log_path.relative_to(runs_root)))
            if lint_result.passed:
                passed = True
                break
            if repair_attempts >= max_repairs:
                error_message = (
                    f"Verilator lint still fails after {repair_attempts} repair attempts"
                )
                break

            current_rtl = read_nonempty_text(rtl_path)
            lint_output = _lint_log(lint_result)
            prompt = (
                f"{repair_template.rstrip()}\n\n"
                f"ORIGINAL SPECIFICATION:\n{config.spec.rstrip()}\n\n"
                f"CURRENT RTL:\n{current_rtl.rstrip()}\n\n"
                f"VERILATOR LINT OUTPUT:\n{lint_output.rstrip()}\n"
            )
            model_result = backend.generate(prompt)
            backend_name = model_result.backend_name
            model_name = model_result.model_name
            repaired_rtl = extract_rtl(model_result.text, top_module)

            history_path = safe_run_path(
                runs_root,
                Path(design_name) / "rtl/history" / f"attempt_{repair_attempts}.sv",
            )
            write_text(history_path, current_rtl)
            write_text(rtl_path, repaired_rtl)
            repair_attempts += 1
    except Exception as exc:
        error_message = str(exc)
    finally:
        finished_at = datetime.now(timezone.utc)
        report = {
            "design_name": design_name,
            "top_module": top_module,
            "backend_name": backend_name,
            "model_name": model_name,
            "verification_success": passed,
            "lint_passed": passed,
            "lint_attempts": lint_attempts,
            "repair_attempts": repair_attempts,
            "max_repair_attempts": max_repairs,
            "final_rtl_path": str(rtl_path.relative_to(runs_root)),
            "log_paths": log_paths,
            "error_message": error_message,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": time.perf_counter() - started_clock,
        }
        write_text(report_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    return VerificationResult(
        design_name=design_name,
        top_module=top_module,
        passed=passed,
        lint_attempts=lint_attempts,
        repair_attempts=repair_attempts,
        final_rtl_path=rtl_path,
        report_path=report_path,
        error_message=error_message,
    )
