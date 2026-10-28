-- Margin by vendor and period, with the credits that quote a line summed beside
-- it rather than folded into it.
--
-- Two totals, on purpose. invoiced_margin_usd is what the billing system says.
-- quoted_margin_usd is what it says once the credits that name a line are
-- applied. Neither is the true margin, because a credit that names no line is
-- not in either, and that gap is the whole point of the reconciliation day.
--
-- The duplicate postings are counted and not removed. One vendor document posted
-- twice is two rows of real money in the ledger and picking the survivor needs a
-- rule nobody has written down here yet.
with lines as (

    select
        vendor_id,
        period,
        count(*) as lines,
        sum(extended_revenue_usd) as revenue_usd,
        sum(extended_cost_usd) as cost_usd,
        sum(invoiced_margin_usd) as invoiced_margin_usd
    from {{ ref('fct_invoice_line_margin') }}
    group by 1, 2

),

credits as (

    select
        vendor_id,
        invoice_period as period,
        sum(case when applies_to = 'cost' then amount_usd else 0 end) as credit_against_cost_usd,
        sum(case when applies_to = 'revenue' then amount_usd else 0 end) as credit_against_revenue_usd,
        count(*) as credits,
        sum(case when is_later_posting_of_the_same_doc then 1 else 0 end) as duplicate_postings,
        sum(case when exceeds_the_line_it_adjusts then 1 else 0 end) as credits_over_their_line,
        max(observable_lag_days) as slowest_credit_days
    from {{ ref('fct_credit_memo') }}
    where is_placeable
    group by 1, 2

)

select
    lines.vendor_id,
    vendors.vendor_name,
    vendors.rebate_program,
    lines.period,
    lines.lines,
    lines.revenue_usd,
    lines.cost_usd,
    lines.invoiced_margin_usd,
    coalesce(credits.credits, 0) as credits,
    coalesce(credits.credit_against_cost_usd, 0) as credit_against_cost_usd,
    coalesce(credits.credit_against_revenue_usd, 0) as credit_against_revenue_usd,
    lines.invoiced_margin_usd
        + coalesce(credits.credit_against_cost_usd, 0)
        - coalesce(credits.credit_against_revenue_usd, 0) as quoted_margin_usd,
    coalesce(credits.duplicate_postings, 0) as duplicate_postings,
    coalesce(credits.credits_over_their_line, 0) as credits_over_their_line,
    credits.slowest_credit_days
from lines
left join credits
    on lines.vendor_id = credits.vendor_id
    and lines.period = credits.period
left join {{ ref('stg_vendors') }} as vendors
    on lines.vendor_id = vendors.vendor_id
