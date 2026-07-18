from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..backends.base import ModelBackend
from ..design_config import load_design_config
from ..tools.file_manager import read_nonempty_text, safe_run_path, write_text
from ..tools.rtl_parser import extract_testbench


@dataclass(frozen=True)
class TestbenchGenerationResult:
    design_name: str
    dut_top_module: str
    tb_top_module: str
    success: bool
    tb_path: Path
    report_path: Path
    backend_name: str
    model_name: str | None
    error_message: str | None


def generate_testbench(
    backend: ModelBackend,
    design_dir: Path | str,
    project_root: Path | str,
) -> TestbenchGenerationResult:
    started_clock = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    root = Path(project_root).resolve()
    runs_root = root if root.name == "runs" else root / "runs"
    design_name = Path(design_dir).resolve().name or "unknown-design"
    dut_top_module = ""
    tb_top_module = ""
    backend_name = backend.__class__.__name__
    model_name: str | None = None
    success = False
    error_message: str | None = None
    rtl_path = safe_run_path(runs_root, Path(design_name) / "rtl/unknown.sv")
    tb_path = safe_run_path(runs_root, Path(design_name) / "tb/unknown.sv")
    report_path = safe_run_path(
        runs_root, Path(design_name) / "reports/testbench_generation.json"
    )
    testplan_path = Path(design_dir).resolve() / "unknown-testplan"

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
        report_path = safe_run_path(
            runs_root, Path(design_name) / "reports/testbench_generation.json"
        )
        testplan_path = config.design_dir / config.testplan_file
        current_rtl = read_nonempty_text(rtl_path)
        template = read_nonempty_text(
            Path(__file__).resolve().parents[1] / "prompts/tb_generator.md"
        )
        prompt = (
            f"{template.rstrip()}\n\n"
            f"DESIGN NAME:\n{design_name}\n\n"
            f"DUT TOP MODULE:\n{dut_top_module}\n\n"
            f"TESTBENCH TOP MODULE:\n{tb_top_module}\n\n"
            f"ORIGINAL SPECIFICATION:\n{config.spec.rstrip()}\n\n"
            f"TEST PLAN:\n{config.testplan.rstrip()}\n\n"
            f"CURRENT DUT RTL:\n{current_rtl.rstrip()}\n"
        )
        model_result = backend.generate(prompt)
        backend_name = model_result.backend_name
        model_name = model_result.model_name
        testbench = extract_testbench(
            model_result.text, tb_top_module, dut_top_module
        )

        if tb_path.exists():
            previous = read_nonempty_text(tb_path)
            history_dir = safe_run_path(runs_root, Path(design_name) / "tb/history")
            index = 0
            while (history_dir / f"attempt_{index}.sv").exists():
                index += 1
            write_text(history_dir / f"attempt_{index}.sv", previous)
        write_text(tb_path, testbench)
        success = True
    except Exception as exc:
        error_message = str(exc)
    finally:
        finished_at = datetime.now(timezone.utc)
        report = {
            "design_name": design_name,
            "dut_top_module": dut_top_module,
            "tb_top_module": tb_top_module,
            "backend_name": backend_name,
            "model_name": model_name,
            "generation_success": success,
            "rtl_path": str(rtl_path.relative_to(runs_root)),
            "testplan_path": str(testplan_path),
            "testbench_path": str(tb_path.relative_to(runs_root)),
            "error_message": error_message,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": time.perf_counter() - started_clock,
        }
        write_text(report_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    return TestbenchGenerationResult(
        design_name, dut_top_module, tb_top_module, success, tb_path,
        report_path, backend_name, model_name, error_message,
    )
