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

The RTL is written below `runs/<design_name>/rtl/`, with metadata in `runs/<design_name>/reports/generation.json`.

## Current scope

Implemented: a common backend result/interface, Codex CLI backend, validated design loader, reusable generation prompt, safe run-path handling, RTL extraction, generation reporting, and the `generate` CLI.

Not implemented yet: Verilator integration, linting and repair loops, testbench generation, simulation, and result-driven repair.

## Adding a design

Create `designs/<name>/` containing `design.json`, a non-empty specification, and a test plan. Follow `designs/counter/design.json` for required fields. Keep filenames local to the design directory, describe the exact module and port interface in the specification, then pass that directory to `--design`.
