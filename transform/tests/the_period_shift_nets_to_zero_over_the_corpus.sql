-- Timing is a reallocation of money between periods. It cannot create or destroy
-- any, so the signed total over every period and account has to be exactly zero.
--
-- This is the test that would have caught the first version of the cross period
-- term. That version moved the billing amount rather than the ledger amount, so
-- a document posted in the wrong period AND keyed at the wrong amount left its
-- difference in the timing column, where it reads as something that clears next
-- month. The variance still added up, so the decomposition test passed. Nothing
-- in the corpus had both defects at once, which is exactly why a second
-- invariant was needed rather than a bigger corpus.
select
    sum(period_shift_cents) as period_shift_cents_over_the_corpus,
    count(*) as rows_summed
from {{ ref('recon_billing_to_gl') }}
having sum(period_shift_cents) != 0
