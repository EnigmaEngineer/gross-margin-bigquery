-- Document grain, both sides of the reconciliation on one row.
--
-- One row per billing document per account, plus a row for every ledger entry
-- whose document billing does not hold. A full outer join, because the two
-- interesting cases are exactly the ones an inner join removes.
--
-- Two periods and they are the whole point. `billing_period` is the period the
-- billing system booked the document in. `gl_period` is the period the ledger
-- posted it in. They differ for an ordinary reason, which is an invoice billed
-- in the close freeze at the end of a month and posted in the next one, and for
-- a bad reason, which is an entry filed in the wrong period. This model does not
-- try to tell those apart. It records that they differ and leaves the judgement
-- to the period level model, which is where the question is answerable.
--
-- Everything is integer cents. A reconciliation whose residual is a float has a
-- residual nobody can call zero.
with lines as (

    select
        invoice_id,
        period,
        sum({{ to_cents('extended_revenue_usd') }}) as revenue_cents,
        sum({{ to_cents('extended_cost_usd') }}) as cost_cents
    from {{ ref('fct_invoice_line_margin') }}
    group by invoice_id, period

),

-- An invoice reaches the ledger as two entries, revenue and cost. Unpivoted here
-- rather than joined twice downstream so that the document grain is one thing.
invoice_side as (

    select
        'invoice' as source_type,
        invoice_id as source_id,
        '4000-revenue' as account,
        period as billing_period,
        revenue_cents as billing_cents
    from lines

    union all

    select
        'invoice' as source_type,
        invoice_id as source_id,
        '5000-cost-of-goods' as account,
        period as billing_period,
        cost_cents as billing_cents
    from lines

),

-- Straight off staging and not off fct_credit_memo. The ledger posts every
-- credit the billing system raised, including the ones whose line cannot be
-- resolved, so filtering to placeable credits here would invent a break for
-- every credit this project already knows it cannot place.
credit_side as (

    select
        'credit' as source_type,
        credit_id as source_id,
        case
            when applies_to = 'cost' then '5100-vendor-credits'
            else '4100-customer-credits'
        end as account,
        {{ period_of('posted_date') }} as billing_period,
        {{ to_cents('amount_usd') }} as billing_cents
    from {{ ref('stg_credit_memos') }}

),

billing as (

    select * from invoice_side
    union all
    select * from credit_side

),

ledger as (

    select
        source_type,
        source_id,
        account,
        period as gl_period,
        period_label as gl_period_label,
        posted_date as gl_posted_date,
        amount_cents as gl_cents,
        je_id
    from {{ ref('stg_gl_entries') }}

)

select
    coalesce(billing.source_type, ledger.source_type) as source_type,
    coalesce(billing.source_id, ledger.source_id) as source_id,
    coalesce(billing.account, ledger.account) as account,
    ledger.je_id,
    billing.billing_period,
    billing.billing_cents,
    ledger.gl_period,
    ledger.gl_period_label,
    ledger.gl_posted_date,
    ledger.gl_cents,
    coalesce(billing.billing_cents, 0) - coalesce(ledger.gl_cents, 0)
        as difference_cents,
    case
        when ledger.source_id is null then 'in_billing_not_in_ledger'
        when billing.source_id is null then 'in_ledger_not_in_billing'
        when billing.billing_period = ledger.gl_period then 'matched_same_period'
        when ledger.gl_period > billing.billing_period then 'matched_ledger_is_later'
        else 'matched_ledger_is_earlier'
    end as match_class
from billing
full outer join ledger
    on billing.source_type = ledger.source_type
    and billing.source_id = ledger.source_id
    and billing.account = ledger.account
