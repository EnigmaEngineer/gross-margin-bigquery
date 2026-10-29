-- The alert. One row per credit that arrived too late for the reconciliation to
-- have reopened the period it belongs to.
--
-- This table is the half of the design that does the work the window cannot. The
-- reconciliation reopens a fixed number of days of closed periods, and that
-- number is set from a quantile because the quantile is the stable part of the
-- arrival distribution. The tail is not stable. scripts/window_spread.py refits
-- the corpus at five seeds and the largest arrival moves from 164 days to 216,
-- so there is no width at which this table is guaranteed to be empty, and a
-- version of this project that only widened the window would have been claiming
-- otherwise.
--
-- So a row here is not a defect in the pipeline. It is a credit whose earning
-- period was reconciled before the credit existed, which means that period's
-- margin is stale and somebody has to decide whether to restate it. The
-- `periods_stale` column is what that decision needs.
--
-- Only placeable credits appear. A credit naming a line this system does not
-- hold has no invoice date, so it has no measurable arrival lag and it is
-- already surfaced by fct_credit_memo as unplaceable. Counting it here would be
-- raising one row twice under two names.
with credits as (

    select * from {{ ref('fct_credit_memo') }}
    where is_placeable
      and observable_lag_days > {{ recon_reopen_days() }}

)

select
    credit_id,
    vendor_doc_no,
    credit_type,
    applies_to,
    amount_usd,
    invoice_id,
    vendor_id,
    -- The period whose margin is now wrong. Not the credit's own period, which
    -- is where the ledger put it and is never the period that needs restating.
    invoice_period as stale_period,
    credit_period as posted_in_period,
    invoice_date,
    posted_date,
    observable_lag_days,
    observable_lag_days - {{ recon_reopen_days() }} as days_past_the_window,
    {{ recon_reopen_days() }} as reopen_window_days,
    exceeds_the_line_it_adjusts
from credits
