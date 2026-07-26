# RTL Agent

RTL Agent is a Python foundation for turning reusable design specifications into synthesizable SystemVerilog through a model backend. The current first stage connects to Codex CLI and extracts one requested top module into a controlled run directory.

## Areas and layout

- `rtl_agent/`: reusable backend, loader, prompt, parsing, file, and workflow code. It contains no counter-specific behavior.
- `designs/`: per-design JSON configuration, specification, and test plan inputs.
- `runs/`: generated RTL and reports. Its contents are ignored except for `.gitkeep`.
- `tests/`: automation-engine tests, including an explicitly invoked Codex CLI integration check.

```text
rtl_agent/{backends,prompts,tools,workflows}
designs/counter/{design.json,spec.md,testplan.md}
runs/
tests/
main.py
```

## Usage

Codex CLI must already be installed and authenticated outside this repository. This project neither copies nor modifies Codex authentication files.

Run the backend integration check (this invokes a real `codex exec`):

```bash
python3 -m tests.test_backend
```

Run the opt-in end-to-end Counter TB auditor integration test:

```bash
RUN_CODEX_TB_AUDIT_INTEGRATION=1 \
  python3 -m unittest tests.test_tb_auditor_integration -v
```

Generate the counter RTL:

```bash
python3 main.py generate --design designs/counter
```

Lint an existing generated RTL file and automatically repair lint failures:

```bash
python3 main.py verify --design designs/counter
```

Generate a deterministic self-checking testbench from the specification, test plan, and current RTL:

```bash
python3 main.py generate-tb --design designs/counter
```

After an approved TB audit, compile the existing RTL and testbench with Verilator `--binary --timing`, then run the generated executable:

```bash
python3 main.py simulate --design designs/counter
```

Classify and safely repair a failed functional simulation:

```bash
python3 main.py repair-sim --design designs/counter
```

Independently audit the generated testbench against the specification and test plan without modifying files:

```bash
python3 main.py audit-tb --design designs/counter
```

Verification requires `verilator` on `PATH`. It first runs `verilator --lint-only --Wall`, then uses the configured model backend to repair the complete RTL when lint fails. Repairs are limited by `max_repair_attempts` in `design.json`. Every full lint output is saved under `runs/<design_name>/logs/`; the final result is written to `runs/<design_name>/reports/verification.json`. Previous RTL revisions are kept under `runs/<design_name>/rtl/history/` before a validated model response replaces the active RTL.

Testbench generation writes `runs/<design_name>/tb/<tb_filename>` and `reports/testbench_generation.json`; replaced testbenches are retained under `tb/history/`. Simulation uses `runs/<design_name>/build/verilator`, writes complete compile and runtime logs under `logs/`, and records `reports/simulation.json`. A simulation passes only when the process exits with code zero, stdout contains `TEST_PASS`, and neither stdout nor stderr contains `TEST_FAIL`.

`audit-tb` starts a separate Codex CLI invocation from testbench generation. Every backend call launches a new `codex exec --ephemeral` process in the read-only sandbox. The auditor receives the design name, DUT module declaration, complete `spec.md`, complete `testplan.md`, complete testbench, testbench path, and SHA-256 hash. It does not receive the RTL implementation body and cannot edit the testbench. The exact model response is saved at `runs/<design_name>/logs/tb_audit_raw.txt`; the validated result is saved at `runs/<design_name>/reports/tb_audit.json`.

The audit CLI prints `TB_AUDIT_APPROVE` and exits 0 only for a schema-valid `APPROVE`. A schema-valid model rejection prints `TB_AUDIT_REJECT` and exits 2. Missing files, backend failures, malformed JSON, extra text, or an invalid schema print `TB_AUDIT_ERROR` and exit 3. REJECT and ERROR both fail closed and retain a report; they are distinct so automation can tell a testbench defect from an audit execution failure.

`simulate` is gated before Verilator is invoked. The current testbench is eligible only when the latest `tb_audit.json` exists, has `schema_valid: true`, has `decision: "APPROVE"`, names the exact current testbench path, and contains the current file's SHA-256 hash. Editing or regenerating the testbench changes its hash and invalidates the approval, so run `python3 main.py audit-tb --design designs/<design_name>` again before simulation. A missing, rejected, malformed, stale, or path-mismatched audit report blocks simulation and prints that command.

Testbench generation validates every model candidate before touching the active TB. If the deterministic contract fails, the exact errors, required IDs, structured testplan, specification, and current candidate are returned to the backend for a bounded testbench-only correction. `max_testbench_generation_attempts` is the maximum number of repair retries after the initial generation; it defaults to 3 and accepts values from 1 through 10. A repair attempt is counted when its backend request is made, and rejected or malformed candidates remain in the report with their rejection reason forwarded to the next retry. Only a contract-PASS candidate replaces the active TB; otherwise the existing TB remains unchanged.

Simulation repair first classifies failures as `environment`, `rtl`, `testbench`, or `ambiguous`. Deterministic rules handle clear toolchain, timeout, permission, and source-located compiler errors; only inconclusive failures use the model classifier's strict JSON response. Model confidence below 0.75 becomes `ambiguous` and no file is changed. Environment failures are never sent to repair because source changes cannot fix missing or broken infrastructure.

Validated RTL revisions are stored under `rtl/history/simulation_repair_attempt_N.sv`; validated testbench revisions use the corresponding `tb/history/` path. The full decision and repair trail is written to `reports/simulation_repair.json`. Testbench repairs must retain the module/DUT identity, TEST_PASS/TEST_FAIL protocol, fatal failure checks, and at least the existing number of failure checks. Classification is conservative but cannot guarantee a correct diagnosis; an ambiguous result requires human review.

Structured testplans define required cases with level-two headings such as `## TP_RESET - Reset behavior`, followed by a non-empty description of preconditions, stimulus, observation timing, expected result, and failure condition. IDs must be unique `TP_*` identifiers. Generated testbenches implement each ID as one `run_<ID>` task, call it once, and emit `TESTCASE_BEGIN`, `TESTCASE_PASS`, or `TESTCASE_FAIL` markers.

The deterministic contract checks task declarations, calls, marker sets, DUT identity, timeout, and global failure behavior. Runtime coverage independently requires every testcase to begin and pass exactly once before a global PASS is accepted. These structural markers cannot prove that stimulus and comparisons are meaningful, so `audit-tb` uses a strict-JSON semantic auditor to check testcase presence, stimulus and expected-value checks, reset semantics, enable/hold/boundary/wraparound behavior, testcase isolation, finite timeout behavior, and safe global PASS control. JSON is accepted only when the complete response matches the fixed schema. Future mutation testing beyond the included Counter fault-injection fixtures is still needed to demonstrate that tests detect a broader set of representative DUT faults.

The RTL is written below `runs/<design_name>/rtl/`, with metadata in `runs/<design_name>/reports/generation.json`.

## Current scope

Implemented: a common backend result/interface, Codex CLI backend, validated design loader, reusable RTL and self-checking testbench prompts, safe run-path handling, RTL extraction, generation reporting, Verilator lint with bounded RTL repair, Verilator binary compilation, and functional simulation reporting.

Not implemented yet: a combined full/auto workflow and formal semantic proof that a generated testbench preserves every testplan assertion. Automated classification is evidence-based but remains fallible.

## Adding a design

Create `designs/<name>/` containing `design.json`, a non-empty specification, and a concrete test plan. Follow `designs/counter/design.json` for fields; `tb_top_module` is optional and defaults to the stem of `tb_filename`. Keep filenames local to the design directory, describe the exact module and public port interface in the specification, and state deterministic stimulus, clock-edge sampling, expected values, failure behavior, and a timeout in the test plan. Then pass that directory to each command's `--design` option.
