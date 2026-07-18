# Counter Test Plan

- Apply synchronous active-low reset and verify `count` is `8'h00` after the appropriate rising clock edge.
- With `enable` low, advance clock edges and verify the value holds.
- With `enable` high, verify `count` increments after every rising clock edge.
- Deassert `enable`, advance another rising edge, and verify the value holds again.
- Drive enough enabled clock cycles to verify `8'hFF` wraps to `8'h00` on the following rising edge.
- Compare expected and actual values only after an appropriate clock edge and settling interval.
- Include a finite timeout watchdog that prints `TEST_FAIL` and terminates with failure if the test does not complete.
