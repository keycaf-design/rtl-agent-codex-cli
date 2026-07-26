from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..backends.base import ModelBackend
from ..design_config import DesignConfig, DesignConfigError, load_design_config
from ..tools.file_manager import read_nonempty_text, safe_run_path
from ..tools.rtl_parser import (
    extract_testbench,
    module_interface,
    require_same_interface,
)
from ..tools.structured_output import (
    StructuredOutputError,
    TBAuditResponse,
    parse_tb_audit_response,
)
from ..tools.testbench_contract import validate_testbench_contract
from ..tools.testplan_parser import parse_testplan_if_structured
from .audit_testbench import build_tb_audit_prompt


VERIFIED_APPROVE_EXIT_CODE = 0
VERIFIED_EXHAUSTED_EXIT_CODE = 2
VERIFIED_ERROR_EXIT_CODE = 3


@dataclass(frozen=True)
class CandidateAuditResult:
    status: str
    decision: str | None
    schema_valid: bool
    summary: str
    findings: tuple[str, ...]
    missing_testcases: tuple[str, ...]
    unsafe_patterns: tuple[str, ...]
    required_changes: tuple[str, ...]
    candidate_path: Path
    candidate_sha256: str | None
    report_path: Path
    raw_response_path: Path
    backend_name: str
    model_name: str | None
    error_type: str | None
    error_message: str | None


@dataclass(frozen=True)
class VerifiedTestbenchGenerationResult:
    status: str
    design_name: str
    attempts: int
    max_attempts: int
    approved_attempt: int | None
    final_tb_path: Path
    final_tb_sha256: str | None
    report_path: Path
    error_message: str | None

    @property
    def passed(self) -> bool:
        return self.status == "APPROVED"

    @property
    def exit_code(self) -> int:
        if self.status == "APPROVED":
            return VERIFIED_APPROVE_EXIT_CODE
        if self.status == "EXHAUSTED":
            return VERIFIED_EXHAUSTED_EXIT_CODE
        return VERIFIED_ERROR_EXIT_CODE


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
    )


def _update_json(path: Path, **updates: Any) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Report must contain a JSON object: {path}")
    value.update(updates)
    _write_json(path, value)


def _build_initial_generation_prompt(
    template: str,
    config: DesignConfig,
    rtl: str,
) -> str:
    return (
        f"{template.rstrip()}\n\n"
        f"DESIGN NAME:\n{config.design_name}\n\n"
        f"DUT TOP MODULE:\n{config.top_module}\n\n"
        f"TESTBENCH TOP MODULE:\n{config.tb_top_module}\n\n"
        f"ORIGINAL SPECIFICATION:\n{config.spec.rstrip()}\n\n"
        f"TEST PLAN:\n{config.testplan.rstrip()}\n\n"
        f"CURRENT DUT RTL:\n{rtl.rstrip()}\n"
    )


def _build_regeneration_prompt(
    template: str,
    config: DesignConfig,
    rtl: str,
    previous_candidate: str,
    audit: CandidateAuditResult,
) -> str:
    return (
        f"{template.rstrip()}\n\n"
        f"DESIGN NAME:\n{config.design_name}\n\n"
        f"DUT TOP MODULE:\n{config.top_module}\n\n"
        f"TESTBENCH TOP MODULE:\n{config.tb_top_module}\n\n"
        f"ORIGINAL SPECIFICATION:\n{config.spec.rstrip()}\n\n"
        f"ORIGINAL TEST PLAN:\n{config.testplan.rstrip()}\n\n"
        f"CURRENT DUT RTL:\n{rtl.rstrip()}\n\n"
        f"PREVIOUS CANDIDATE TESTBENCH:\n{previous_candidate.rstrip()}\n\n"
        f"PREVIOUS AUDIT DECISION:\n{audit.decision}\n\n"
        f"PREVIOUS AUDIT SUMMARY:\n{audit.summary}\n\n"
        f"PREVIOUS AUDIT FINDINGS:\n"
        f"{json.dumps(list(audit.findings), ensure_ascii=False)}\n\n"
        f"PREVIOUS AUDIT MISSING TESTCASES:\n"
        f"{json.dumps(list(audit.missing_testcases), ensure_ascii=False)}\n\n"
        f"PREVIOUS AUDIT UNSAFE PATTERNS:\n"
        f"{json.dumps(list(audit.unsafe_patterns), ensure_ascii=False)}\n\n"
        f"PREVIOUS AUDIT REQUIRED CHANGES:\n"
        f"{json.dumps(list(audit.required_changes), ensure_ascii=False)}\n"
    )


def _safe_gate_report(
    design_name: str,
    top_module: str,
    tb_path: Path,
    report_path: Path,
    raw_path: Path,
    project_root: Path,
    error_type: str,
    error_message: str,
) -> None:
    current_hash = (
        _sha256_bytes(tb_path.read_bytes()) if tb_path.is_file() else None
    )
    _atomic_write_text(raw_path, "")
    _write_json(report_path, {
        "design_name": design_name,
        "top_module": top_module,
        "backend_name": None,
        "model_name": None,
        "tb_path": _display_path(tb_path, project_root),
        "tb_sha256": current_hash,
        "decision": None,
        "summary": "Verified testbench generation has not produced an approval",
        "findings": [],
        "missing_testcases": [],
        "unsafe_patterns": [],
        "required_changes": [],
        "raw_response_path": _display_path(raw_path, project_root),
        "timestamp": _timestamp(),
        "schema_valid": False,
        "status": "ERROR",
        "error_type": error_type,
        "error_message": error_message,
    })


def audit_testbench_candidate(
    backend: ModelBackend,
    design_dir: Path | str,
    project_root: Path | str,
    candidate_path: Path | str,
    attempt: int,
    previous_attempt: int | None = None,
) -> CandidateAuditResult:
    root = Path(project_root).resolve()
    runs_root = root if root.name == "runs" else root / "runs"
    config = load_design_config(design_dir)
    candidate = Path(candidate_path).resolve()
    if candidate != runs_root and runs_root not in candidate.parents:
        raise ValueError(f"Candidate path escapes runs directory: {candidate}")
    report_path = safe_run_path(
        runs_root,
        Path(config.design_name) / "reports" / f"tb_attempt_{attempt}_audit.json",
    )
    raw_path = safe_run_path(
        runs_root,
        Path(config.design_name) / "logs" / f"tb_attempt_{attempt}_audit_raw.txt",
    )
    backend_name = backend.__class__.__name__
    model_name: str | None = None
    raw_response = ""
    candidate_sha256: str | None = None
    parsed: TBAuditResponse | None = None
    status = "ERROR"
    summary = "Candidate audit failed safely"
    error_type: str | None = None
    error_message: str | None = None

    try:
        if not candidate.is_file():
            error_type = "tb_missing"
            raise FileNotFoundError(
                f"Testbench candidate does not exist: {candidate}"
            )
        candidate_bytes = candidate.read_bytes()
        candidate_sha256 = _sha256_bytes(candidate_bytes)
        try:
            candidate_text = candidate_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            error_type = "tb_read_error"
            raise ValueError(
                f"Testbench candidate is not valid UTF-8: {candidate}"
            ) from exc
        if not candidate_text.strip():
            error_type = "tb_read_error"
            raise ValueError(f"Testbench candidate is empty: {candidate}")

        rtl_path = safe_run_path(
            runs_root,
            Path(config.design_name) / "rtl" / config.rtl_filename,
        )
        rtl = read_nonempty_text(rtl_path)
        interface = module_interface(rtl, config.top_module)
        template = read_nonempty_text(
            Path(__file__).resolve().parents[1] / "prompts" / "tb_auditor.md"
        )
        prompt = build_tb_audit_prompt(
            template,
            design_name=config.design_name,
            top_module=config.top_module,
            interface=interface,
            specification=config.spec,
            testplan=config.testplan,
            testbench_path=candidate,
            testbench_sha256=candidate_sha256,
            testbench=candidate_text,
        )
        try:
            response = backend.generate(prompt)
        except Exception:
            error_type = "backend_error"
            raise
        backend_name = response.backend_name
        model_name = response.model_name
        raw_response = response.text
        _atomic_write_text(raw_path, raw_response)
        try:
            parsed = parse_tb_audit_response(raw_response)
        except StructuredOutputError:
            error_type = "schema_error"
            raise
        status = parsed.decision
        summary = parsed.summary
    except Exception as exc:
        error_message = str(exc)
        if error_type is None:
            error_type = "audit_error"
    finally:
        _atomic_write_text(raw_path, raw_response)
        audit_values = parsed.to_dict() if parsed else {
            "decision": None,
            "summary": summary,
            "findings": [],
            "missing_testcases": [],
            "unsafe_patterns": [],
            "required_changes": [],
        }
        _write_json(report_path, {
            "attempt": attempt,
            "previous_attempt": previous_attempt,
            "design_name": config.design_name,
            "top_module": config.top_module,
            "backend_name": backend_name,
            "model_name": model_name,
            "candidate_path": _display_path(candidate, root),
            "candidate_sha256": candidate_sha256,
            "tb_path": _display_path(candidate, root),
            "tb_sha256": candidate_sha256,
            **audit_values,
            "raw_response_path": _display_path(raw_path, root),
            "timestamp": _timestamp(),
            "schema_valid": parsed is not None,
            "status": status,
            "error_type": error_type,
            "error_message": error_message,
            "promoted": False,
        })

    values = parsed.to_dict() if parsed else {
        "findings": [],
        "missing_testcases": [],
        "unsafe_patterns": [],
        "required_changes": [],
    }
    return CandidateAuditResult(
        status=status,
        decision=parsed.decision if parsed else None,
        schema_valid=parsed is not None,
        summary=summary,
        findings=tuple(values["findings"]),
        missing_testcases=tuple(values["missing_testcases"]),
        unsafe_patterns=tuple(values["unsafe_patterns"]),
        required_changes=tuple(values["required_changes"]),
        candidate_path=candidate,
        candidate_sha256=candidate_sha256,
        report_path=report_path,
        raw_response_path=raw_path,
        backend_name=backend_name,
        model_name=model_name,
        error_type=error_type,
        error_message=error_message,
    )


def promote_approved_candidate(
    candidate_path: Path | str,
    expected_sha256: str,
    active_tb_path: Path | str,
    history_dir: Path | str,
) -> Path | None:
    candidate = Path(candidate_path)
    active = Path(active_tb_path)
    history_root = Path(history_dir)
    if not candidate.is_file():
        raise FileNotFoundError(f"Approved candidate does not exist: {candidate}")
    candidate_bytes = candidate.read_bytes()
    current_hash = _sha256_bytes(candidate_bytes)
    if current_hash != expected_sha256:
        raise ValueError(
            "Approved candidate changed after audit; refusing promotion"
        )

    history_path: Path | None = None
    if active.is_file():
        index = 0
        while (history_root / f"attempt_{index}.sv").exists():
            index += 1
        history_path = history_root / f"attempt_{index}.sv"
        _atomic_write_bytes(history_path, active.read_bytes())
    _atomic_write_bytes(active, candidate_bytes)
    if _sha256_bytes(active.read_bytes()) != expected_sha256:
        raise RuntimeError("Promoted testbench hash verification failed")
    return history_path


def _install_final_audit(
    audit: CandidateAuditResult,
    active_tb_path: Path,
    final_report_path: Path,
    final_raw_path: Path,
    project_root: Path,
    verified_status: str,
    promoted: bool,
) -> None:
    report = json.loads(audit.report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Candidate audit report must contain a JSON object")
    _atomic_write_bytes(final_raw_path, audit.raw_response_path.read_bytes())
    report.update({
        "tb_path": _display_path(
            active_tb_path if promoted else audit.candidate_path,
            project_root,
        ),
        "raw_response_path": _display_path(final_raw_path, project_root),
        "verified_generation_status": verified_status,
        "promoted": promoted,
        "timestamp": _timestamp(),
    })
    _write_json(final_report_path, report)


def generate_verified_testbench(
    generator_backend: ModelBackend,
    auditor_backend: ModelBackend,
    design_dir: Path | str,
    project_root: Path | str,
) -> VerifiedTestbenchGenerationResult:
    root = Path(project_root).resolve()
    runs_root = root if root.name == "runs" else root / "runs"
    design_name = Path(design_dir).resolve().name or "unknown-design"
    status = "CONFIG_ERROR"
    max_attempts = 0
    approved_attempt: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    attempt_reports: list[dict[str, Any]] = []
    final_tb_sha256: str | None = None
    started_at = _timestamp()
    final_tb_path = safe_run_path(
        runs_root, Path(design_name) / "tb" / "unresolved.sv"
    )
    report_path = safe_run_path(
        runs_root,
        Path(design_name) / "reports" / "tb_verified_generation.json",
    )
    final_audit_path = safe_run_path(
        runs_root, Path(design_name) / "reports" / "tb_audit.json"
    )
    final_audit_raw_path = safe_run_path(
        runs_root, Path(design_name) / "logs" / "tb_audit_raw.txt"
    )

    try:
        config = load_design_config(design_dir)
        design_name = config.design_name
        max_attempts = config.max_tb_audit_attempts
        final_tb_path = safe_run_path(
            runs_root,
            Path(design_name) / "tb" / config.tb_filename,
        )
        report_path = safe_run_path(
            runs_root,
            Path(design_name) / "reports" / "tb_verified_generation.json",
        )
        final_audit_path = safe_run_path(
            runs_root, Path(design_name) / "reports" / "tb_audit.json"
        )
        final_audit_raw_path = safe_run_path(
            runs_root, Path(design_name) / "logs" / "tb_audit_raw.txt"
        )
        _safe_gate_report(
            design_name,
            config.top_module,
            final_tb_path,
            final_audit_path,
            final_audit_raw_path,
            root,
            "verified_generation_in_progress",
            "Verified generation must reach an independent APPROVE",
        )

        rtl_path = safe_run_path(
            runs_root,
            Path(design_name) / "rtl" / config.rtl_filename,
        )
        rtl = read_nonempty_text(rtl_path)
        generation_template = read_nonempty_text(
            Path(__file__).resolve().parents[1] / "prompts" / "tb_generator.md"
        )
        regeneration_template = read_nonempty_text(
            Path(__file__).resolve().parents[1] / "prompts" / "tb_regenerator.md"
        )
        structured_plan = parse_testplan_if_structured(config.testplan)
        required_ids = (
            structured_plan.required_testcase_ids if structured_plan else []
        )
        previous_candidate: str | None = None
        previous_hash: str | None = None
        previous_audit: CandidateAuditResult | None = None
        last_audit: CandidateAuditResult | None = None

        for attempt in range(max_attempts):
            candidate_path = safe_run_path(
                runs_root,
                Path(design_name)
                / "tb"
                / "candidates"
                / f"tb_attempt_{attempt}.sv",
            )
            generation_report_path = safe_run_path(
                runs_root,
                Path(design_name)
                / "reports"
                / f"tb_attempt_{attempt}_generation.json",
            )
            generation_raw_path = safe_run_path(
                runs_root,
                Path(design_name)
                / "logs"
                / f"tb_attempt_{attempt}_generation_raw.txt",
            )
            generation_status = "ERROR"
            generation_error_type: str | None = None
            generation_error: str | None = None
            backend_name = generator_backend.__class__.__name__
            model_name: str | None = None
            raw_generation = ""
            candidate_sha256: str | None = None
            parsed_candidate: str | None = None
            prompt = (
                _build_initial_generation_prompt(
                    generation_template, config, rtl
                )
                if attempt == 0
                else _build_regeneration_prompt(
                    regeneration_template,
                    config,
                    rtl,
                    previous_candidate or "",
                    previous_audit,
                )
            )

            try:
                response = generator_backend.generate(prompt)
                backend_name = response.backend_name
                model_name = response.model_name
                raw_generation = response.text
                _atomic_write_text(generation_raw_path, raw_generation)
                parsed_candidate = extract_testbench(
                    raw_generation,
                    config.tb_top_module,
                    config.top_module,
                )
                if previous_candidate is not None:
                    require_same_interface(
                        previous_candidate,
                        parsed_candidate,
                        config.tb_top_module,
                    )
                if required_ids:
                    contract = validate_testbench_contract(
                        parsed_candidate,
                        required_ids,
                        config.tb_top_module,
                        config.top_module,
                    )
                    if not contract.passed:
                        raise ValueError(
                            "Generated candidate failed deterministic contract: "
                            + "; ".join(contract.errors)
                        )
                _atomic_write_text(candidate_path, parsed_candidate)
                if not candidate_path.is_file():
                    raise FileNotFoundError(
                        f"Candidate file was not saved: {candidate_path}"
                    )
                candidate_sha256 = _sha256_bytes(candidate_path.read_bytes())
                generation_status = "SUCCESS"
            except Exception as exc:
                generation_error = str(exc)
                generation_error_type = (
                    "backend_error"
                    if not raw_generation
                    else "generation_validation_error"
                )
            finally:
                _atomic_write_text(generation_raw_path, raw_generation)
                generation_report = {
                    "attempt": attempt,
                    "previous_attempt": attempt - 1 if attempt else None,
                    "previous_candidate_sha256": previous_hash,
                    "generation_status": generation_status,
                    "candidate_path": _display_path(candidate_path, root),
                    "candidate_sha256": candidate_sha256,
                    "backend_name": backend_name,
                    "model_name": model_name,
                    "generation_timestamp": _timestamp(),
                    "raw_response_path": _display_path(
                        generation_raw_path, root
                    ),
                    "error_type": generation_error_type,
                    "error_message": generation_error,
                    "promoted": False,
                }
                _write_json(generation_report_path, generation_report)

            entry = {
                "attempt": attempt,
                "generation_status": generation_status,
                "audit_status": "NOT_RUN",
                "candidate_path": _display_path(candidate_path, root),
                "candidate_sha256": candidate_sha256,
                "generation_report_path": _display_path(
                    generation_report_path, root
                ),
                "audit_report_path": None,
                "previous_attempt": attempt - 1 if attempt else None,
                "previous_candidate_sha256": previous_hash,
                "promoted": False,
            }
            attempt_reports.append(entry)

            if generation_status != "SUCCESS":
                status = "GENERATION_ERROR"
                error_type = generation_error_type
                error_message = generation_error
                break
            if previous_hash is not None and candidate_sha256 == previous_hash:
                status = "GENERATION_ERROR"
                error_type = "duplicate_candidate"
                error_message = (
                    "Generator returned the same candidate hash consecutively"
                )
                entry["generation_status"] = "ERROR"
                _update_json(
                    generation_report_path,
                    generation_status="ERROR",
                    error_type=error_type,
                    error_message=error_message,
                )
                break

            audit = audit_testbench_candidate(
                auditor_backend,
                design_dir,
                root,
                candidate_path,
                attempt,
                previous_attempt=attempt - 1 if attempt else None,
            )
            last_audit = audit
            entry["audit_status"] = audit.status
            entry["audit_report_path"] = _display_path(
                audit.report_path, root
            )
            _update_json(
                generation_report_path,
                audit_status=audit.status,
                audit_decision=audit.decision,
                audit_summary=audit.summary,
                audit_findings=list(audit.findings),
                audit_missing_testcases=list(audit.missing_testcases),
                audit_unsafe_patterns=list(audit.unsafe_patterns),
                audit_required_changes=list(audit.required_changes),
                audit_report_path=_display_path(audit.report_path, root),
            )
            if audit.status == "ERROR":
                status = "AUDIT_ERROR"
                error_type = audit.error_type
                error_message = audit.error_message
                break
            if audit.status == "APPROVE":
                if audit.candidate_sha256 != candidate_sha256:
                    status = "AUDIT_ERROR"
                    error_type = "audit_hash_mismatch"
                    error_message = (
                        "Audit hash does not match the generated candidate"
                    )
                    break
                try:
                    history_path = promote_approved_candidate(
                        candidate_path,
                        audit.candidate_sha256,
                        final_tb_path,
                        safe_run_path(
                            runs_root, Path(design_name) / "tb" / "history"
                        ),
                    )
                    final_tb_sha256 = _sha256_bytes(
                        final_tb_path.read_bytes()
                    )
                    _install_final_audit(
                        audit,
                        final_tb_path,
                        final_audit_path,
                        final_audit_raw_path,
                        root,
                        "APPROVED",
                        promoted=True,
                    )
                    _update_json(generation_report_path, promoted=True)
                    _update_json(
                        audit.report_path,
                        promoted=True,
                        final_tb_path=_display_path(final_tb_path, root),
                        history_path=(
                            _display_path(history_path, root)
                            if history_path else None
                        ),
                    )
                    entry["promoted"] = True
                    approved_attempt = attempt
                    status = "APPROVED"
                except Exception as exc:
                    status = "AUDIT_ERROR"
                    error_type = "promotion_error"
                    error_message = str(exc)
                break

            previous_candidate = parsed_candidate
            previous_hash = candidate_sha256
            previous_audit = audit
        else:
            status = "EXHAUSTED"

        if status == "EXHAUSTED" and last_audit is not None:
            _install_final_audit(
                last_audit,
                final_tb_path,
                final_audit_path,
                final_audit_raw_path,
                root,
                "EXHAUSTED",
                promoted=False,
            )
            error_message = (
                f"All {max_attempts} candidates were rejected by the auditor"
            )
        elif status not in {"APPROVED", "EXHAUSTED"}:
            _safe_gate_report(
                design_name,
                config.top_module,
                final_tb_path,
                final_audit_path,
                final_audit_raw_path,
                root,
                error_type or "verified_generation_error",
                error_message or "Verified generation failed safely",
            )
    except DesignConfigError as exc:
        status = "CONFIG_ERROR"
        error_type = "config_error"
        error_message = str(exc)
        _safe_gate_report(
            design_name,
            "",
            final_tb_path,
            final_audit_path,
            final_audit_raw_path,
            root,
            error_type,
            error_message,
        )
    except Exception as exc:
        if status == "CONFIG_ERROR":
            status = "GENERATION_ERROR"
        error_type = error_type or "workflow_error"
        error_message = str(exc)
        _safe_gate_report(
            design_name,
            "",
            final_tb_path,
            final_audit_path,
            final_audit_raw_path,
            root,
            error_type,
            error_message,
        )
    finally:
        final_report = {
            "status": status,
            "design_name": design_name,
            "attempts": len(attempt_reports),
            "max_attempts": max_attempts,
            "approved_attempt": approved_attempt,
            "final_tb_path": _display_path(final_tb_path, root),
            "final_tb_sha256": final_tb_sha256,
            "attempt_reports": attempt_reports,
            "error_type": error_type,
            "error_message": error_message,
            "started_at": started_at,
            "finished_at": _timestamp(),
        }
        _write_json(report_path, final_report)

    return VerifiedTestbenchGenerationResult(
        status=status,
        design_name=design_name,
        attempts=len(attempt_reports),
        max_attempts=max_attempts,
        approved_attempt=approved_attempt,
        final_tb_path=final_tb_path,
        final_tb_sha256=final_tb_sha256,
        report_path=report_path,
        error_message=error_message,
    )
