from __future__ import annotations

import argparse
from pathlib import Path

from rtl_agent.backends.codex_cli import CodexCLIBackend
from rtl_agent.workflows.generate_rtl import generate_rtl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reusable RTL generation automation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="generate RTL from a design directory")
    generate.add_argument("--design", required=True, type=Path, help="design input directory")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = Path(__file__).resolve().parent
    try:
        if args.command == "generate":
            backend = CodexCLIBackend(project_dir=project_root)
            output = generate_rtl(backend, args.design, project_root)
            print(f"Generated RTL: {output}")
            return 0
    except Exception as exc:
        print(f"RTL generation failed: {exc}")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
