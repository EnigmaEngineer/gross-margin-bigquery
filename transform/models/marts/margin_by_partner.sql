-- Margin by partner and period. The partner key is nullable here and that is not
-- an oversight.
--
-- The corpus carries invoices with no partner on them. Those lines hold real
-- revenue and real cost, so they get their own row with a null partner_id rather
-- than being dropped. A regional total that silently excludes them is wrong by
-- exactly the amount nobody can see.
--
-- The two credit stages are the same pair margin_by_vendor carries. Named puts a
-- credit on the line it quotes and allocated spreads a rebate over the quarter
-- it was earned in. A rebate crosses partners, so this is the grain where the
-- two answers are furthest apart.
with lines as (

    select
        margin.partner_id,
        margin.region,
        margin.segment,
        margin.period,
        margin.has_partner,
        count(*) as lines,
        count(distinct margin.invoice_id) as invoices,
        sum(margin.extended_revenue_usd) as revenue_usd,
        sum(margin.invoiced_margin_usd) as invoiced_margin_usd,
        sum(rollup.credits_naming_this_line) as credits,
        sum(rollup.named_against_cost_cents) as named_against_cost_cents,
        sum(rollup.named_against_revenue_cents) as named_against_revenue_cents,
        sum(rollup.allocated_against_cost_cents) as allocated_against_cost_cents,
        sum(rollup.allocated_against_revenue_cents) as allocated_against_revenue_cents,
        sum(rollup.allocated_from_a_rebate_cents) as allocated_from_a_rebate_cents
    from {{ ref('fct_invoice_line_margin') }} as margin
    inner join {{ ref('credit_rollup_by_line') }} as rollup
        on margin.line_id = rollup.line_id
    group by 1, 2, 3, 4, 5

)

select
    partner_id,
    region,
    segment,
    period,
    has_partner,
    lines,
    invoices,
    revenue_usd,
    invoiced_margin_usd,
    credits,
    invoiced_margin_usd
        + {{ cents_to_usd('named_against_cost_cents') }}
        - {{ cents_to_usd('named_against_revenue_cents') }}
        as margin_after_named_credits_usd,
    {{ cents_to_usd('allocated_from_a_rebate_cents') }} as allocated_from_a_rebate_usd,
    invoiced_margin_usd
        + {{ cents_to_usd('allocated_against_cost_cents') }}
        - {{ cents_to_usd('allocated_against_revenue_cents') }}
        as margin_after_allocated_credits_usd,
    allocated_against_cost_cents
        - allocated_against_revenue_cents
        - named_against_cost_cents
        + named_against_revenue_cents as margin_moved_cents
from lines
