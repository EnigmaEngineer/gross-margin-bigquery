# The hand checked fixtures

Moved out of the README unchanged. Two six row corpora with every expected
figure worked out on paper and written into the scripts as literals. They are
the strongest argument in the repository and they are also the part a reader
does not need in order to run anything, which is the trade this directory
exists for.

Both are run by `tests/run_all.py` through `tests/test_allocation.py` and
`tests/test_recon.py`, so nothing here is documentation of a thing that is not
executed.

## The allocation on numbers a person can check

Twenty thousand lines cannot be verified by reading them. `scripts/allocation_fixture.py`
builds a six line corpus with the same seven files and the same headers, runs the
real models over it, and compares against figures worked out on paper and written
into the script as literals. It covers all four scopes. There is a rounding tie
that one leftover cent cannot settle evenly and a scope whose basis sums to zero.
One credit has its invoice in one month and its posting in the next.

Then it runs the incremental three times. The second run appends a credit posted
578 days after the invoice it adjusts and asserts that it lands, and the run
refuses to proceed unless that credit's invoice is outside a window of the same
length, so the run is a test of the anchor rather than of the window. The third
appends a credit backdated past the window and asserts it is missed, because a
window that cannot miss anything is not a window.

### The reconciliation on numbers a person can check

`scripts/recon_fixture.py` is the same idea for the reconciliation. Three
invoices, two credits and eight ledger entries. The expected variance, period
shift and break for every period and account were worked out on paper and
written into the script as literals.

It exists because of a defect it caught. The first version of the model moved the
billing amount between periods when the two systems disagreed about which period a
document belonged to. A document that is both in the wrong period and at the
wrong amount then had its amount difference counted as a period shift, which says
it will clear next month, and it will not. The variance still added up, so the
decomposition test passed either way.

No row in the generated corpus carries both defects at once. Under the first
version the full build came back with 101 of 101 checks passing, and the six row
fixture failed on `the_period_shift_nets_to_zero_over_the_corpus`. Twenty
thousand lines said the model was fine and six said it was not.

