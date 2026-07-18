from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .simulator import SimulationResult


class ClassificationError(ValueError):
    """A classification response is malformed or unsafe."""


@dataclass(frozen=True)
class FailureClassification:
    source: str
    category: str
    confidence: float
    summary: str
    evidence: list[str]
    target_file: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_CATEGORIES = {"environment", "rtl", "testbench", "ambiguous"}
_TARGETS = {"none", "rtl", "testbench"}


def parse_classification(text: str, source: str = "model") -> FailureClassification:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClassificationError(f"Classifier returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ClassificationError("Classification must be a JSON object")
    required = {"category", "confidence", "summary", "evidence", "target_file"}
    missing = required - data.keys()
    if missing:
        raise ClassificationError(f"Classification fields missing: {', '.join(sorted(missing))}")
    category, target = data["category"], data["target_file"]
    confidence = data["confidence"]
    if category not in _CATEGORIES or target not in _TARGETS:
        raise ClassificationError("Classification category or target_file is invalid")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ClassificationError("Classification confidence must be between 0.0 and 1.0")
    if not isinstance(data["summary"], str) or not data["summary"].strip():
        raise ClassificationError("Classification summary must be non-empty")
    if not isinstance(data["evidence"], list) or not all(isinstance(x, str) for x in data["evidence"]):
        raise ClassificationError("Classification evidence must be a string array")
    expected_target = {"rtl": "rtl", "testbench": "testbench"}.get(category, "none")
    if target != expected_target:
        raise ClassificationError("Classification category and target_file disagree")
    return FailureClassification(source, category, float(confidence), data["summary"].strip(), data["evidence"], target)


def deterministic_classification(
    result: SimulationResult | None,
    rtl_file: Path,
    tb_file: Path,
    error_message: str | None = None,
) -> FailureClassification | None:
    combined = "\n".join(filter(None, [
        error_message,
        result.compile_stdout if result else None,
        result.compile_stderr if result else None,
        result.run_stdout if result else None,
        result.run_stderr if result else None,
        result.failure_reason if result else None,
    ]))
    lowered = combined.lower()
    environment_terms = (
        "not found on path", "no such file or directory", "permission denied",
        "exceeded", "timeout", "timed out", "build tool", "executable is missing",
    )
    if any(term in lowered for term in environment_terms):
        return FailureClassification("deterministic", "environment", 1.0,
            "Simulation infrastructure failed", [combined[:1000]], "none")
    if result and not result.compile_passed:
        errors = "\n".join(line for line in combined.splitlines() if "%Error" in line)
        if str(Path(rtl_file).resolve()) in errors:
            return FailureClassification("deterministic", "rtl", 0.99,
                "Compiler error points to the RTL source", [errors[:1000]], "rtl")
        if str(Path(tb_file).resolve()) in errors:
            return FailureClassification("deterministic", "testbench", 0.99,
                "Compiler error points to the testbench source", [errors[:1000]], "testbench")
    return None
