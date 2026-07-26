Regenerate one complete self-checking SystemVerilog testbench after an
independent auditor rejected the previous candidate. Return the entire corrected
testbench module and nothing else. Do not return a patch, diff, Markdown fence,
or explanation, and do not repeat the previous candidate unchanged.

Use the original specification and test plan as the authority. Address every
audit summary, finding, missing testcase, unsafe pattern, and required change
supplied below. Do not modify the DUT interface, specification, or test plan.
Do not remove or weaken checks merely to satisfy the auditor. Do not derive
expected values from DUT output or add behavior that is not required by the
specification, test plan, or concrete audit feedback.

Preserve every required testcase and its real preconditions, stimulus,
observation timing, independent expected-value comparison, and reachable
failure path. TEST_PASS may be printed only after every required testcase and
check succeeds. Retain a finite timeout that prints TEST_FAIL and terminates
with `$fatal(1)`. Use syntax compatible with Verilator `--binary --timing`.
