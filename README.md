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

Compile the existing RTL and testbench with Verilator `--binary --timing`, then run the generated executable:

```bash
python3 main.py simulate --design designs/counter
```

Verification requires `verilator` on `PATH`. It first runs `verilator --lint-only --Wall`, then uses the configured model backend to repair the complete RTL when lint fails. Repairs are limited by `max_repair_attempts` in `design.json`. Every full lint output is saved under `runs/<design_name>/logs/`; the final result is written to `runs/<design_name>/reports/verification.json`. Previous RTL revisions are kept under `runs/<design_name>/rtl/history/` before a validated model response replaces the active RTL.

Testbench generation writes `runs/<design_name>/tb/<tb_filename>` and `reports/testbench_generation.json`; replaced testbenches are retained under `tb/history/`. Simulation uses `runs/<design_name>/build/verilator`, writes complete compile and runtime logs under `logs/`, and records `reports/simulation.json`. A simulation passes only when the process exits with code zero, stdout contains `TEST_PASS`, and neither stdout nor stderr contains `TEST_FAIL`.

The RTL is written below `runs/<design_name>/rtl/`, with metadata in `runs/<design_name>/reports/generation.json`.

## Current scope

Implemented: a common backend result/interface, Codex CLI backend, validated design loader, reusable RTL and self-checking testbench prompts, safe run-path handling, RTL extraction, generation reporting, Verilator lint with bounded RTL repair, Verilator binary compilation, and functional simulation reporting.

Not implemented yet: simulation-failure-driven RTL repair, testbench repair, and a combined full/auto workflow.

## Adding a design

Create `designs/<name>/` containing `design.json`, a non-empty specification, and a concrete test plan. Follow `designs/counter/design.json` for fields; `tb_top_module` is optional and defaults to the stem of `tb_filename`. Keep filenames local to the design directory, describe the exact module and public port interface in the specification, and state deterministic stimulus, clock-edge sampling, expected values, failure behavior, and a timeout in the test plan. Then pass that directory to each command's `--design` option.
