from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..backends.base import ModelBackend
from ..design_config import load_design_config
from ..tools.file_manager import read_nonempty_text, safe_run_path, write_text
from ..tools.rtl_parser import extract_rtl


def generate_rtl(
    backend: ModelBackend,
    design_dir: Path | str,
    project_root: Path | str,
) -> Path:
    root = Path(project_root).resolve()
    runs_root = root if root.name == "runs" else root / "runs"
    fallback_name = Path(design_dir).resolve().name or "unknown-design"
    design_name = fallback_name
    top_module = ""
    model_name = None
    output_path: Path | None = None
    report_path = safe_run_path(runs_root, Path(design_name) / "reports/generation.json")
    report = {
        "design_name": design_name,
        "top_module": top_module,
        "backend_name": backend.__class__.__name__,
        "model_name": model_name,
        "output_path": None,
        "generation_success": False,
        "error_message": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        config = load_design_config(design_dir)
        design_name, top_module = config.design_name, config.top_module
        report_path = safe_run_path(runs_root, Path(design_name) / "reports/generation.json")
        prompt_template = read_nonempty_text(
            Path(__file__).resolve().parents[1] / "prompts/rtl_generator.md"
        )
        prompt = f"{prompt_template.rstrip()}\n\nDESIGN SPECIFICATION:\n{config.spec.rstrip()}\n"
        result = backend.generate(prompt)
        model_name = result.model_name
        rtl = extract_rtl(result.text, top_module)
        output_path = safe_run_path(
            runs_root, Path(design_name) / "rtl" / config.rtl_filename
        )
        write_text(output_path, rtl)
        report.update({
            "design_name": design_name,
            "top_module": top_module,
            "backend_name": result.backend_name,
            "model_name": model_name,
            "output_path": str(output_path.relative_to(runs_root)),
            "generation_success": True,
        })
        return output_path
    except Exception as exc:
        report.update({
            "design_name": design_name,
            "top_module": top_module,
            "model_name": model_name,
            "output_path": (
                str(output_path.relative_to(runs_root)) if output_path else None
            ),
            "error_message": str(exc),
        })
        raise
    finally:
        write_text(report_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
