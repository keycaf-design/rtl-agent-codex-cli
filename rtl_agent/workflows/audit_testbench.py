from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..backends.base import ModelBackend
from ..design_config import load_design_config
from ..tools.file_manager import read_nonempty_text, safe_run_path, write_text
from ..tools.rtl_parser import module_interface
from ..tools.structured_output import (
    StructuredOutputError,
    TBAuditResponse,
    parse_tb_audit_response,
)
from ..tools.testbench_contract import extract_testcase_task_bodies
from ..tools.testplan_parser import parse_testplan


AUDIT_APPROVE_EXIT_CODE = 0
AUDIT_REJECT_EXIT_CODE = 2
AUDIT_ERROR_EXIT_CODE = 3


@dataclass(frozen=True)
class TestbenchAuditWorkflowResult:
    design_name: str
    top_module: str
    status: str
    decision: str | None
    schema_valid: bool
    summary: str
    tb_path: Path
    report_path: Path
    raw_response_path: Path
    backend_name: str
    model_name: str | None
    error_message: str | None

    @property
    def passed(self) -> bool:
        return self.status == "APPROVE"

    @property
    def testbench_path(self) -> Path:
        return self.tb_path

    @property
    def confidence(self) -> None:
        """Compatibility with the previous semantic-audit result."""

        return None

    @property
    def exit_code(self) -> int:
        if self.status == "APPROVE":
            return AUDIT_APPROVE_EXIT_CODE
        if self.status == "REJECT":
            return AUDIT_REJECT_EXIT_CODE
        return AUDIT_ERROR_EXIT_CODE


@dataclass(frozen=True)
class TBAuditGateResult:
    approved: bool
    reason: str | None
    report_path: Path
    current_tb_sha256: str | None


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def build_tb_audit_prompt(
    template: str,
    *,
    design_name: str,
    top_module: str,
    interface: str,
    specification: str,
    testplan: str,
    testbench_path: Path,
    testbench_sha256: str,
    testbench: str,
) -> str:
    return (
        f"{template.rstrip()}\n\n"
        f"DESIGN NAME:\n{design_name}\n\n"
        f"DUT TOP MODULE:\n{top_module}\n\n"
        f"DUT MODULE INTERFACE:\n{interface}\n\n"
        f"SPECIFICATION (FULL):\n{specification.rstrip()}\n\n"
        f"TEST PLAN (FULL):\n{testplan.rstrip()}\n\n"
        f"TESTBENCH PATH:\n{testbench_path}\n\n"
        f"TESTBENCH SHA-256:\n{testbench_sha256}\n\n"
        f"TESTBENCH UNDER AUDIT (FULL):\n{testbench.rstrip()}\n"
    )


def audit_testbench(
    backend: ModelBackend,
    design_dir: Path | str,
    project_root: Path | str,
) -> TestbenchAuditWorkflowResult:
    root = Path(project_root).resolve()
    runs_root = root if root.name == "runs" else root / "runs"
    design_name = Path(design_dir).resolve().name or "unknown-design"
    top_module = ""
    backend_name = backend.__class__.__name__
    model_name: str | None = None
    tb_path = safe_run_path(
        runs_root, Path(design_name) / "tb" / "unresolved.sv"
    )
    report_path = safe_run_path(
        runs_root, Path(design_name) / "reports" / "tb_audit.json"
    )
    raw_path = safe_run_path(
        runs_root, Path(design_name) / "logs" / "tb_audit_raw.txt"
    )
    tb_sha256: str | None = None
    raw_response = ""
    parsed: TBAuditResponse | None = None
    schema_valid = False
    status = "ERROR"
    summary = "TB audit did not complete"
    error_message: str | None = None
    error_type: str | None = None

    try:
        config = load_design_config(design_dir)
        design_name = config.design_name
        top_module = config.top_module
        tb_path = safe_run_path(
            runs_root, Path(design_name) / "tb" / config.tb_filename
        )
        report_path = safe_run_path(
            runs_root, Path(design_name) / "reports" / "tb_audit.json"
        )
        raw_path = safe_run_path(
            runs_root, Path(design_name) / "logs" / "tb_audit_raw.txt"
        )
        if not tb_path.is_file():
            error_type = "tb_missing"
            raise FileNotFoundError(f"Testbench file does not exist: {tb_path}")

        tb_bytes = tb_path.read_bytes()
        try:
            tb_text = tb_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            error_type = "tb_read_error"
            raise ValueError(f"Testbench is not valid UTF-8: {tb_path}") from exc
        if not tb_text.strip():
            error_type = "tb_read_error"
            raise ValueError(f"Testbench file is empty: {tb_path}")
        tb_sha256 = _sha256_bytes(tb_bytes)

        rtl_path = safe_run_path(
            runs_root, Path(design_name) / "rtl" / config.rtl_filename
        )
        rtl_text = read_nonempty_text(rtl_path)
        interface = module_interface(rtl_text, config.top_module)
        template = read_nonempty_text(
            Path(__file__).resolve().parents[1] / "prompts" / "tb_auditor.md"
        )
        prompt = build_tb_audit_prompt(
            template,
            design_name=design_name,
            top_module=config.top_module,
            interface=interface,
            specification=config.spec,
            testplan=config.testplan,
            testbench_path=tb_path,
            testbench_sha256=tb_sha256,
            testbench=tb_text,
        )
        try:
            response = backend.generate(prompt)
        except Exception:
            error_type = "backend_error"
            raise
        backend_name = response.backend_name
        model_name = response.model_name
        raw_response = response.text
        write_text(raw_path, raw_response)

        try:
            parsed = parse_tb_audit_response(raw_response)
        except StructuredOutputError:
            error_type = "schema_error"
            raise
        schema_valid = True
        status = parsed.decision
        summary = parsed.summary
    except Exception as exc:
        error_message = str(exc)
        if error_type is None:
            error_type = "audit_error"
        summary = "TB audit failed safely"
    finally:
        write_text(raw_path, raw_response)
        timestamp = datetime.now(timezone.utc).isoformat()
        audit_values = parsed.to_dict() if parsed else {
            "decision": None,
            "summary": summary,
            "findings": [],
            "missing_testcases": [],
            "unsafe_patterns": [],
            "required_changes": [],
        }
        report = {
            "design_name": design_name,
            "top_module": top_module,
            "backend_name": backend_name,
            "model_name": model_name,
            "tb_path": _display_path(tb_path, root),
            "tb_sha256": tb_sha256,
            **audit_values,
            "raw_response_path": _display_path(raw_path, root),
            "timestamp": timestamp,
            "schema_valid": schema_valid,
            "status": status,
            "error_type": error_type,
            "error_message": error_message,
        }
        write_text(
            report_path,
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        )

    return TestbenchAuditWorkflowResult(
        design_name=design_name,
        top_module=top_module,
        status=status,
        decision=parsed.decision if parsed else None,
        schema_valid=schema_valid,
        summary=summary,
        tb_path=tb_path,
        report_path=report_path,
        raw_response_path=raw_path,
        backend_name=backend_name,
        model_name=model_name,
        error_message=error_message,
    )


def check_tb_audit_gate(
    design_name: str,
    tb_path: Path,
    project_root: Path | str,
) -> TBAuditGateResult:
    root = Path(project_root).resolve()
    runs_root = root if root.name == "runs" else root / "runs"
    report_path = safe_run_path(
        runs_root, Path(design_name) / "reports" / "tb_audit.json"
    )
    if not report_path.is_file():
        return TBAuditGateResult(
            False, "TB audit report does not exist", report_path, None
        )
    if not tb_path.is_file():
        return TBAuditGateResult(
            False, "Testbench file does not exist", report_path, None
        )

    current_hash = _sha256_bytes(tb_path.read_bytes())
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return TBAuditGateResult(
            False, f"TB audit report is unreadable or malformed: {exc}",
            report_path, current_hash,
        )
    if not isinstance(report, dict):
        return TBAuditGateResult(
            False, "TB audit report must contain a JSON object",
            report_path, current_hash,
        )
    if report.get("schema_valid") is not True:
        return TBAuditGateResult(
            False, "TB audit response schema is not valid",
            report_path, current_hash,
        )
    if report.get("decision") != "APPROVE":
        return TBAuditGateResult(
            False, "TB audit decision is not APPROVE",
            report_path, current_hash,
        )

    reported_path = report.get("tb_path")
    if not isinstance(reported_path, str) or not reported_path:
        return TBAuditGateResult(
            False, "TB audit report has no valid testbench path",
            report_path, current_hash,
        )
    candidate = Path(reported_path)
    reported_resolved = (
        candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    )
    if reported_resolved != tb_path.resolve():
        return TBAuditGateResult(
            False, "TB audit testbench path does not match the current testbench",
            report_path, current_hash,
        )
    if report.get("tb_sha256") != current_hash:
        return TBAuditGateResult(
            False, "TB audit hash does not match the current testbench",
            report_path, current_hash,
        )
    return TBAuditGateResult(True, None, report_path, current_hash)


# The repair-sim workflow currently consumes the older, testcase-evidence audit
# contract. Keep its prompt builder isolated from the P0 audit-tb contract while
# omitting the DUT implementation body from that auditor as well.
def build_semantic_audit_prompt(
    template: str,
    specification: str,
    testplan_text: str,
    rtl: str,
    testbench: str,
    contract: object,
) -> str:
    del rtl
    plan = parse_testplan(testplan_text)
    requirements = [asdict(case) for case in plan.testcases]
    task_bodies = extract_testcase_task_bodies(testbench)
    return (
        f"{template}\n\nSPECIFICATION:\n{specification}\n\n"
        f"STRUCTURED TESTPLAN JSON:\n"
        f"{json.dumps(requirements, ensure_ascii=False)}\n\n"
        f"TESTCASE TASK BODIES JSON:\n"
        f"{json.dumps(task_bodies, ensure_ascii=False)}\n\n"
        f"FULL TESTBENCH:\n{testbench}\n\n"
        f"CONTRACT RESULT:\n{json.dumps(asdict(contract))}\n"
    )
