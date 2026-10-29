-- The claim recon_billing_to_gl makes about itself. Every period and account
-- splits its variance into a part that clears next month and a part that does
-- not, and the two add back to the variance exactly.
--
-- No tolerance. The columns are integer cents all the way through, so a
-- difference of one cent here is a defect in the decomposition and not rounding.
-- This is the check that lets the model's own header use the word exact.
select
    period,
    account,
    variance_cents,
    period_shift_cents,
    break_cents,
    variance_cents - (period_shift_cents + break_cents) as left_over_cents
from {{ ref('recon_billing_to_gl') }}
where variance_cents != period_shift_cents + break_cents
