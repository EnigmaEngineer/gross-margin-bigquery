# The share that was not a share

Moved out of the README on the completion pass, which keeps the corrected figure
and the sweep table and sends a reader here for the mechanism.

`scripts/freeze_sweep.py` produces the table and `tests/test_recon.py` and
`tests/test_cost.py` grade it.

## The correction

The figure published for five days was wrong and the correction is worth
the paragraph. The figure published for five days was the gross period shift over
the gross variance, which is 99.63 percent of the gross variance and is not a
share of anything. `variance = period_shift + break` holds on every row and the
two terms are free to disagree in sign, so a row's shift can be larger than its
own variance, and on this corpus 5 rows of 98 are exactly that. Dividing one
absolute column total by the other therefore has no upper bound. It read as a
share for as long as it happened to stay under a hundred.

What found it was a loop rather than a review. The share is dominated by invoices
billed inside the close freeze, and the width of that freeze is
`FREEZE_BUSINESS_DAYS` in `gross_margin/calendar.py`, which is 2 because the
module author chose 2. `scripts/freeze_sweep.py` regenerates the corpus and
rebuilds the warehouse at three widths and refits the figure on each.

| close freeze, business days | share of the two terms | shift over variance | rows where the shift exceeds the variance |
|---|---|---|---|
| 1 | 97.34 | 99.12 | 4 |
| 2 | 98.3 | 99.63 | 5 |
| 3 | 98.82 | 100.77 | 6 |

The third row is the one that settled it. At a three day close the old formula
returns 100.77 percent, and a share above a hundred percent is not a number with
a rounding problem. Both figures are published now, the bounded one as a share
and the other under a name saying it is a ratio, and `tests/test_recon.py`
re-derives each from the rows.
