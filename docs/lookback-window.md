# The lookback window, and why it is a quantile rather than a maximum

Moved out of the README unchanged. It is the longest piece of that file a
reader does not need in order to run anything. The argument itself is load
bearing. `recon_reopen_days` and `allocation_lookback_days` are both set by
it and `tests/test_recon.py` grades the derivation.

## The lookback is on the arrival date, and that is the whole design

The allocation model is incremental over a window. There are two dates it could
measure that window against and they are not interchangeable.

A credit arrives late. The invoice it adjusts can be eighteen months old. A
window on `invoice_date` therefore reprocesses rows whose answers did not change
and misses the rows whose answers did. A window on `posted_date` reaches a credit
on the day it lands, however old the lines are, and the model replaces whole
credits rather than whole partitions so an old line can be rewritten.

`scripts/lookback_probe.py` measures what the same window length costs under each
reading, over this corpus.

| window | credits an invoice_date window misses | allocation rows it leaves wrong |
|---|---|---|
| 30 days | 1,197 | 177,185 |
| 90 days | 333 | 67,036 |
| 105 days | 168 | 33,413 |
| 180 days | 0 | 0 |

The second column is the one that matters. A missed rebate does not only lose its
own amount. Every line it would have been spread over keeps a share it should not
have, so 168 missed credits leave
33,413 rows wrong.

The same probe reports zero misses for the `posted_date` reading at every window,
and that zero is worth nothing. Arrival order is posting order in this corpus
because the generator writes it that way, so the figure is zero by construction
and says nothing about a real feed. What would make it non zero is a backdated
posting or a replayed load, and neither exists here.

## The window was set from a maximum, and a maximum is not a bound

The allocation lookback was first declared at 180 days, and the argument for it
was that this made it a bound rather than a tuned threshold, because the largest credit arrival anywhere
in the corpus was 164 days and 180 is wider than that. The reasoning is the part
that was wrong, and `scripts/window_spread.py` is what shows it. The script
regenerates the whole corpus at five seeds and refits every window figure on each
one.

| seed | pooled 95th | price_protection | return_credit | volume_rebate | largest arrival |
|---|---|---|---|---|---|
| 20260428 | 105 | 20 | 49 | 124 | 164 |
| 20260429 | 108 | 20 | 49 | 125 | 174 |
| 20260430 | 108 | 20 | 51 | 126 | 164 |
| 20260501 | 109 | 20 | 52 | 127 | 216 |
| 20260502 | 105 | 20 | 54 | 122 | 194 |

The quantiles hold still. The pooled 95th centile moves 4 days across those
seeds and the 99th moves 6 days, from 125 to 131. The largest arrival moves 52 days,
from 164 to 216, and it is the figure the window was derived from.

So 180 days is not a bound. It leaves a credit outside itself on two of the five
seeds. That is a small miss in money and it is not a small miss in reasoning,
because the number was chosen specifically to avoid being a threshold and it was
a threshold the whole time. A sample maximum is the largest draw so far. On an
arrival distribution with a long right tail it grows with the number of draws, so
there is no width at which it stops being a draw.

The reconciliation window is derived from the centile instead. `recon_reopen_days`
is 150, which sits 19 days above the worst 99th centile over those five seeds,
and the README and the project file both state it with its coverage rather than
as a bound. `tests/test_recon.py` asserts the derivation rather than the number,
so changing the variable without re-running the refit fails the suite.

The rest of the tail is the alert's job, and that is the honest division of
labour. Nothing covers a tail by being wide enough.
