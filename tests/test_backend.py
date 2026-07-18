from pathlib import Path

from rtl_agent.backends.codex_cli import CodexCLIBackend, CodexCLIError


PROJECT_DIR = Path(__file__).parent.parent.resolve()


def main() -> None:
    backend = CodexCLIBackend(
        project_dir=PROJECT_DIR,
        timeout_seconds=300,
    )

    result = backend.generate(
        "Return exactly CODEX_OK and nothing else."
    )

    print(f"Codex response: {result.text!r}")

    if result.text.strip() != "CODEX_OK":
        raise AssertionError(
            f"Expected 'CODEX_OK', received {result.text!r}"
        )

    print("PASS: Codex CLI backend is working.")


if __name__ == "__main__":
    try:
        main()

    except (
        CodexCLIError,
        AssertionError,
        ValueError,
    ) as exc:
        print(f"FAIL: {exc}")
        raise SystemExit(1)
