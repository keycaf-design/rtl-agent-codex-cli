from __future__ import annotations

from pathlib import Path


def read_text(path: Path | str) -> str:
    return Path(path).read_text(encoding="utf-8")


def read_nonempty_text(path: Path | str) -> str:
    text = read_text(path)
    if not text.strip():
        raise ValueError(f"Input file is empty: {Path(path)}")
    return text


def write_text(path: Path | str, text: str) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination


def safe_run_path(runs_root: Path | str, relative_path: Path | str) -> Path:
    root = Path(runs_root).resolve()
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Result path escapes runs directory: {relative_path}")
    return candidate
