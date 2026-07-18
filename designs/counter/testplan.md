# Counter Test Plan

- Verify synchronous active-low reset sets the count to zero.
- Verify the count holds while `enable` is low.
- Verify the count increments on each rising edge while `enable` is high.
- Verify the count holds after `enable` is deasserted.
- Verify overflow wraps from `8'hFF` to `8'h00`.
