# 8-bit Counter Specification

Implement a module named `counter` with inputs `clk`, `rst_n`, and `enable`, and one output `logic [7:0] count`.

- `rst_n` is an active-low synchronous reset. On a rising edge of `clk`, when `rst_n` is low, set `count` to `8'h00`.
- Otherwise, when `enable` is high, increment `count` by one on each rising edge.
- When `enable` is low, retain the current value.
- Incrementing `8'hFF` naturally wraps to `8'h00`.
