from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import ModelBackend, ModelResult


class CodexCLIError(RuntimeError):
    """Codex CLI 실행이 실패했을 때 발생하는 예외."""


class CodexCLIBackend(ModelBackend):
    """Codex CLI를 Python에서 호출하는 최소 backend."""

    def __init__(
        self,
        project_dir: Path,
        timeout_seconds: int = 300,
        executable: str = "codex",
    ) -> None:
        self.project_dir = project_dir.resolve()
        self.timeout_seconds = timeout_seconds
        self.executable = executable

        self._validate_environment()

    def _validate_environment(self) -> None:
        if not self.project_dir.is_dir():
            raise ValueError(
                f"프로젝트 디렉터리가 없습니다: {self.project_dir}"
            )

        if shutil.which(self.executable) is None:
            raise CodexCLIError(
                f"'{self.executable}' 명령을 PATH에서 찾을 수 없습니다."
            )

    def generate(self, prompt: str) -> ModelResult:
        """프롬프트를 Codex에 전달하고 최종 응답을 반환한다."""

        if not prompt.strip():
            raise ValueError("프롬프트가 비어 있습니다.")

        command = [
            self.executable,
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "-",
        ]

        try:
            result = subprocess.run(
                command,
                cwd=self.project_dir,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )

        except subprocess.TimeoutExpired as exc:
            raise CodexCLIError(
                f"Codex 실행이 {self.timeout_seconds}초를 초과했습니다."
            ) from exc

        except OSError as exc:
            raise CodexCLIError(
                f"Codex 프로세스를 실행할 수 없습니다: {exc}"
            ) from exc

        if result.returncode != 0:
            raise CodexCLIError(
                "Codex CLI 실행 실패\n\n"
                f"종료 코드: {result.returncode}\n\n"
                f"STDOUT:\n{result.stdout}\n\n"
                f"STDERR:\n{result.stderr}"
            )

        response = result.stdout.strip()

        if not response:
            raise CodexCLIError(
                "Codex가 정상 종료됐지만 최종 응답이 비어 있습니다.\n\n"
                f"STDERR:\n{result.stderr}"
            )

        return ModelResult(
            text=response,
            backend_name="codex-cli",
            model_name=None,
            metadata={"returncode": result.returncode},
        )
