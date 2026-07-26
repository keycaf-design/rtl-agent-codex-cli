from __future__ import annotations

import argparse
from pathlib import Path

from rtl_agent.backends.base import ModelBackend
from rtl_agent.backends.codex_cli import CodexCLIBackend
from rtl_agent.workflows.generate_rtl import generate_rtl
from rtl_agent.workflows.generate_testbench import generate_testbench
from rtl_agent.workflows.audit_testbench import audit_testbench
from rtl_agent.workflows.repair_simulation import repair_simulation
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
    repair_sim = subparsers.add_parser(
        "repair-sim", help="classify and safely repair simulation failures"
    )
    repair_sim.add_argument("--design", required=True, type=Path, help="design input directory")
    audit_tb = subparsers.add_parser("audit-tb", help="audit semantic testbench coverage")
    audit_tb.add_argument("--design", required=True, type=Path, help="design input directory")
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
            print(f"Design: {result.design_name}")
            print(f"Generation attempts: {result.total_generation_attempts}")
            print(f"Contract repairs: {result.contract_repair_attempts}")
            print(f"Testcase contract: {'PASS' if result.testcase_contract_passed else 'FAIL'}")
            if not result.success:
                print(f"Existing testbench preserved: {'YES' if result.previous_testbench_preserved else 'NO'}")
                print(f"Reason: {result.error_message}")
            else:
                print(f"Generated testbench: {result.tb_path}")
            print(f"Final result: {'PASS' if result.success else 'FAIL'}")
            return 0 if result.success else 1
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
            if not result.compile_passed:
                print(f"Compile log: {result.compile_log_path.relative_to(project_root)}")
                print(f"Verilator return code: {result.compile_return_code}")
                if result.primary_error:
                    print(f"Primary error: {result.primary_error}")
                print("Full compiler output is saved in the compile log.")
            return 0 if result.final_result == "PASS" else 1
        if args.command == "repair-sim":
            backend = CodexCLIBackend(project_dir=project_root)
            result = repair_simulation(backend, args.design, project_root)
            print(f"Design: {result.design_name}")
            print(f"Initial simulation: {'PASS' if result.initial_failure_category is None and result.passed else 'FAIL'}")
            if result.initial_failure_category:
                print(f"Failure category: {result.initial_failure_category}")
            print(f"Repair attempts: {result.total_attempts}")
            print(f"RTL repairs: {result.rtl_repair_attempts}")
            print(f"Testbench repairs: {result.testbench_repair_attempts}")
            print(f"Final simulation: {'PASS' if result.passed else 'FAIL'}")
            print(f"Final result: {'PASS' if result.passed else 'FAIL'}")
            if result.error_message:
                print(f"Reason: {result.error_message}")
            return 0 if result.passed else 1
        if args.command == "audit-tb":
            try:
                backend: ModelBackend = CodexCLIBackend(project_dir=project_root)
            except Exception as exc:
                initialization_error = str(exc)

                class FailedCodexBackend(ModelBackend):
                    def generate(self, prompt: str):
                        del prompt
                        raise RuntimeError(initialization_error)

                backend = FailedCodexBackend()
            result = audit_testbench(backend, args.design, project_root)
            print(f"TB_AUDIT_{result.status}")
            print(f"report: {result.report_path.relative_to(project_root)}")
            return result.exit_code
    except Exception as exc:
        print(f"RTL command failed: {exc}")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
