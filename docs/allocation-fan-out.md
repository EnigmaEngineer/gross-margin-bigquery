# The allocation fan out, and how it grows

Moved out of the README on the completion pass. The result is on the front page
and the argument is here, which is the split this directory exists for.

`scripts/allocation_profile.py` produces the figures and
`tests/test_allocation.py` grades the table in place.

## The spread is quadratic and this corpus is too small to show it

932 rebates become 189,899 allocation rows, and the table rebuilds inside the
normal `dbt build` with no cost worth noticing. That is the problem rather than
the reassurance. The spread is a self join on the line table keyed by vendor and
quarter, so the pair count grows with the product of the two sides, and 20,446
lines is small enough to hide it.

`scripts/allocation_profile.py` measures the growth at ten times the corpus. It
reports counts and no wall time, because a figure here has to reproduce off this
machine.

| | lines | placeable rebates | allocation rows | scoped join pairs |
|---|---|---|---|---|
| shipped | 20,446 | 932 | 192,572 | 189,899 |
| ten times | 205,713 | 8,964 | 18,266,702 | 18,240,971 |
| factor | 10.06 | 9.62 | 94.86 | 96.06 |

Ten times the lines is ninety six times the pairs. The widest vendor quarter goes
from 338 lines to 3,094, which is where the square comes from. The same table
grouped to vendor and quarter before the credit meets it comes to 8,964 pairs at
the larger size, a factor of 9.62, which is linear.

That rewrite is the fix and it is not in this repository. It changes no published
figure, because the allocation is pro rata on a basis the aggregate preserves,
and it is a rewrite of one CTE in the model that produces every margin number
here. Shipping it on the last day of the project without time to re-derive all of
them is how a correctness problem gets introduced while fixing a performance one.

Finding the growth also needed a change, and it is worth naming because the
assumption was wrong. Scaling the corpus was supposed to be an argument the
generator already took. It is not. `vendors` and `partners` and `products` are
populations to draw from, so multiplying them by ten returned a corpus 1.02 times
larger. Row count comes from invoices per business day, and `volume_scale` was
added for this measurement. At 1 it draws exactly as before, so the committed
corpus still reproduces byte for byte.
