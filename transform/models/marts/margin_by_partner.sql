-- Margin by partner and period. The partner key is nullable here and that is not
-- an oversight.
--
-- The corpus carries invoices with no partner on them. Those lines hold real
-- revenue and real cost, so they get their own row with a null partner_id rather
-- than being dropped. A regional total that silently excludes them is wrong by
-- exactly the amount nobody can see.
with lines as (

    select
        partner_id,
        region,
        segment,
        period,
        has_partner,
        count(*) as lines,
        count(distinct invoice_id) as invoices,
        sum(extended_revenue_usd) as revenue_usd,
        sum(invoiced_margin_usd) as invoiced_margin_usd
    from {{ ref('fct_invoice_line_margin') }}
    group by 1, 2, 3, 4, 5

),

credits as (

    select
        partner_id,
        invoice_period as period,
        sum(case when applies_to = 'cost' then amount_usd else 0 end) as credit_against_cost_usd,
        sum(case when applies_to = 'revenue' then amount_usd else 0 end) as credit_against_revenue_usd,
        count(*) as credits
    from {{ ref('fct_credit_memo') }}
    where is_placeable
    group by 1, 2

)

select
    lines.partner_id,
    lines.region,
    lines.segment,
    lines.period,
    lines.has_partner,
    lines.lines,
    lines.invoices,
    lines.revenue_usd,
    lines.invoiced_margin_usd,
    coalesce(credits.credits, 0) as credits,
    lines.invoiced_margin_usd
        + coalesce(credits.credit_against_cost_usd, 0)
        - coalesce(credits.credit_against_revenue_usd, 0) as quoted_margin_usd
from lines
left join credits
    on lines.partner_id = credits.partner_id
    and lines.period = credits.period
