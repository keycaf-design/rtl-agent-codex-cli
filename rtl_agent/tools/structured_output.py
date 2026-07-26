from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class StructuredOutputError(ValueError):
    """A model response is not valid strict TB-audit JSON."""


_FIELDS = {
    "decision",
    "summary",
    "findings",
    "missing_testcases",
    "unsafe_patterns",
    "required_changes",
}
_ARRAY_FIELDS = (
    "findings",
    "missing_testcases",
    "unsafe_patterns",
    "required_changes",
)


@dataclass(frozen=True)
class TBAuditResponse:
    decision: str
    summary: str
    findings: tuple[str, ...]
    missing_testcases: tuple[str, ...]
    unsafe_patterns: tuple[str, ...]
    required_changes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "summary": self.summary,
            "findings": list(self.findings),
            "missing_testcases": list(self.missing_testcases),
            "unsafe_patterns": list(self.unsafe_patterns),
            "required_changes": list(self.required_changes),
        }


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StructuredOutputError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise StructuredOutputError(f"Non-standard JSON value is not allowed: {value}")


def parse_tb_audit_response(raw_text: str) -> TBAuditResponse:
    """Parse one exact JSON object and validate the TB-audit response contract."""

    if not isinstance(raw_text, str) or not raw_text.strip():
        raise StructuredOutputError("Audit response is empty")
    try:
        data = json.loads(
            raw_text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except StructuredOutputError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise StructuredOutputError(f"Invalid audit JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise StructuredOutputError("Audit response must be a JSON object")
    missing = sorted(_FIELDS - data.keys())
    extra = sorted(data.keys() - _FIELDS)
    if missing:
        raise StructuredOutputError(
            f"Audit response is missing fields: {', '.join(missing)}"
        )
    if extra:
        raise StructuredOutputError(
            f"Audit response has unknown fields: {', '.join(extra)}"
        )

    decision = data["decision"]
    if not isinstance(decision, str) or decision not in {"APPROVE", "REJECT"}:
        raise StructuredOutputError("decision must be APPROVE or REJECT")
    summary = data["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise StructuredOutputError("summary must be a non-empty string")

    arrays: dict[str, tuple[str, ...]] = {}
    for field in _ARRAY_FIELDS:
        value = data[field]
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise StructuredOutputError(f"{field} must be an array of strings")
        arrays[field] = tuple(value)

    has_reject_basis = any(
        item.strip() for values in arrays.values() for item in values
    )
    if decision == "REJECT" and not has_reject_basis:
        raise StructuredOutputError(
            "REJECT requires at least one finding, missing testcase, "
            "unsafe pattern, or required change"
        )

    return TBAuditResponse(
        decision=decision,
        summary=summary.strip(),
        findings=arrays["findings"],
        missing_testcases=arrays["missing_testcases"],
        unsafe_patterns=arrays["unsafe_patterns"],
        required_changes=arrays["required_changes"],
    )
