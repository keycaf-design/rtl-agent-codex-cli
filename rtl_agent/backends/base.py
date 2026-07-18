from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelResult:
    """모든 AI backend가 공통으로 반환하는 결과."""

    text: str
    backend_name: str
    model_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ModelBackend(ABC):
    """Codex CLI, OpenAI API, Ollama가 구현할 공통 인터페이스."""

    @abstractmethod
    def generate(self, prompt: str) -> ModelResult:
        raise NotImplementedError
