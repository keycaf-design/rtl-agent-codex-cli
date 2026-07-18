from __future__ import annotations

import argparse
from pathlib import Path

from rtl_agent.backends.codex_cli import CodexCLIBackend
from rtl_agent.workflows.generate_rtl import generate_rtl
from rtl_agent.workflows.generate_testbench import generate_testbench
from rtl_agent.workflows.simulate import simulate_design
from rtl_agent.workflows.verify_rtl import verify_rtl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reusable RTL generation automation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="generate RTL from a design directory")
    generate.add_argument("--design", required=True, type=Path, help="design input directory")
    verify = subparsers.add_parser("verify", help="lint and repair generated RTL")
    verify.add_argument("--design", required=True, type=Path, help="design input directory")
    generate_tb = subparsers.add_parser(
        "generate-tb", help="generate a self-checking testbench"
    )
    generate_tb.add_argument("--design", required=True, type=Path, help="design input directory")
    simulate = subparsers.add_parser("simulate", help="compile and run RTL simulation")
    simulate.add_argument("--design", required=True, type=Path, help="design input directory")
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
        if args.command == "verify":
            backend = CodexCLIBackend(project_dir=project_root)
            result = verify_rtl(backend, args.design, project_root)
            rtl_display = result.final_rtl_path.relative_to(project_root)
            print(f"Design: {result.design_name}")
            print(f"RTL: {rtl_display}")
            print(f"Lint attempts: {result.lint_attempts}")
            print(f"Repair attempts: {result.repair_attempts}")
            print(f"Final result: {'PASS' if result.passed else 'FAIL'}")
            if result.error_message:
                print(f"Reason: {result.error_message}")
            return 0 if result.passed else 1
        if args.command == "generate-tb":
            backend = CodexCLIBackend(project_dir=project_root)
            result = generate_testbench(backend, args.design, project_root)
            if result.success:
                print(f"Generated testbench: {result.tb_path}")
                return 0
            print(f"Testbench generation failed: {result.error_message}")
            return 1
        if args.command == "simulate":
            result = simulate_design(args.design, project_root)
            print(f"Design: {result.design_name}")
            print(f"DUT: {result.dut_top_module}")
            print(f"Testbench: {result.tb_top_module}")
            print(f"Compile result: {'PASS' if result.compile_passed else 'FAIL'}")
            print(f"Simulation result: {'PASS' if result.simulation_passed else 'FAIL'}")
            print(f"Final result: {result.final_result}")
            if result.error_message:
                print(f"Reason: {result.error_message}")
            return 0 if result.final_result == "PASS" else 1
    except Exception as exc:
        print(f"RTL command failed: {exc}")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
