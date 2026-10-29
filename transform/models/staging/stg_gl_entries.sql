-- The ledger as it lands. Cast and labelled, nothing dropped and nothing fixed.
--
-- `period` is the ledger's own label and `posted_date` is the date behind it. A
-- real ledger can disagree with itself about those two, so the staging layer
-- keeps both and derives a third from the date, and the reconciliation uses the
-- derived one. Trusting the label would hide an entry filed under a period its
-- own date does not fall in.
select
    je_id,
    period as period_label,
    {{ period_of(as_date('posted_date')) }} as period,
    account,
    source_type,
    source_id,
    {{ money('amount_usd') }} as amount_usd,
    {{ to_cents(money('amount_usd')) }} as amount_cents,
    {{ as_date('posted_date') }} as posted_date,
    period != {{ period_of(as_date('posted_date')) }} as period_label_disagrees_with_its_date
from {{ source('raw', 'gl_entries') }}
