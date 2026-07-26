from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

from .tools.file_manager import read_nonempty_text


class DesignConfigError(ValueError):
    """A design input directory is missing or invalid."""


@dataclass(frozen=True)
class DesignConfig:
    design_name: str
    top_module: str
    rtl_filename: str
    tb_filename: str
    tb_top_module: str
    spec_file: str
    testplan_file: str
    max_repair_attempts: int
    max_simulation_repair_attempts: int
    max_tb_audit_attempts: int
    design_dir: Path
    spec: str
    testplan: str


_REQUIRED = (
    "design_name", "top_module", "rtl_filename", "tb_filename",
    "spec_file", "testplan_file", "max_repair_attempts",
)
_FILE_FIELDS = ("rtl_filename", "tb_filename", "spec_file", "testplan_file")


def _safe_filename(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DesignConfigError(f"{field} must be a non-empty filename")
    path = PurePath(value)
    if path.is_absolute() or ".." in path.parts:
        raise DesignConfigError(f"{field} must not escape the design directory")
    return value


def load_design_config(design_dir: Path | str) -> DesignConfig:
    directory = Path(design_dir).resolve()
    if not directory.is_dir():
        raise DesignConfigError(f"Design directory does not exist: {directory}")
    config_path = directory / "design.json"
    if not config_path.is_file():
        raise DesignConfigError(f"design.json does not exist: {config_path}")
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DesignConfigError(f"Invalid design.json: {exc}") from exc
    if not isinstance(data, dict):
        raise DesignConfigError("design.json must contain a JSON object")
    missing = [field for field in _REQUIRED if field not in data]
    if missing:
        raise DesignConfigError(f"Missing required fields: {', '.join(missing)}")
    for field in ("design_name", "top_module"):
        if not isinstance(data[field], str) or not data[field].strip():
            raise DesignConfigError(f"{field} must be a non-empty string")
    filenames = {field: _safe_filename(data[field], field) for field in _FILE_FIELDS}
    simulation_repairs = data.get("max_simulation_repair_attempts", 3)
    if isinstance(simulation_repairs, bool) or not isinstance(simulation_repairs, int):
        raise DesignConfigError("max_simulation_repair_attempts must be an integer")
    if not 0 <= simulation_repairs <= 10:
        raise DesignConfigError(
            "max_simulation_repair_attempts must be between 0 and 10"
        )
    audit_attempts = data.get("max_tb_audit_attempts", 3)
    if isinstance(audit_attempts, bool) or not isinstance(audit_attempts, int):
        raise DesignConfigError("max_tb_audit_attempts must be an integer")
    if not 1 <= audit_attempts <= 10:
        raise DesignConfigError(
            "max_tb_audit_attempts must be between 1 and 10"
        )
    tb_top_module = data.get("tb_top_module", Path(filenames["tb_filename"]).stem)
    if not isinstance(tb_top_module, str) or not tb_top_module.strip():
        raise DesignConfigError("tb_top_module must be a non-empty string")
    if PurePath(tb_top_module).name != tb_top_module or ".." in PurePath(tb_top_module).parts:
        raise DesignConfigError("tb_top_module must not contain a path")
    spec_path = directory / filenames["spec_file"]
    testplan_path = directory / filenames["testplan_file"]
    try:
        spec = read_nonempty_text(spec_path)
        testplan = read_nonempty_text(testplan_path)
    except (OSError, ValueError) as exc:
        raise DesignConfigError(f"Invalid design input file: {exc}") from exc
    return DesignConfig(
        design_name=data["design_name"].strip(),
        top_module=data["top_module"].strip(),
        tb_top_module=tb_top_module.strip(),
        max_repair_attempts=int(data["max_repair_attempts"]),
        max_simulation_repair_attempts=simulation_repairs,
        max_tb_audit_attempts=audit_attempts,
        design_dir=directory,
        spec=spec,
        testplan=testplan,
        **filenames,
    )
