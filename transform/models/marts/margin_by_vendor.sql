-- Margin by vendor and period, at three stages of the same number.
--
-- invoiced_margin_usd is what the billing system says before any credit.
--
-- margin_after_named_credits_usd applies each credit to the single line it
-- quotes. It is the figure a vendor statement would reconcile to and it is not
-- the economics, because a quarterly rebate lands on whichever line the billing
-- system attached it to.
--
-- margin_after_allocated_credits_usd applies fct_credit_allocation instead, so a
-- rebate is spread across the quarter it was earned in. The two totals agree
-- over the whole corpus and they disagree per vendor and period, and the size of
-- that disagreement is the day's result.
--
-- Neither is the true margin. Credits that name no line this system holds are in
-- neither, and the count of them is published rather than buried.
--
-- The credit rollup arrives at line grain from one model. The three marts used
-- to aggregate the credit fact separately, which is three group bys that have to
-- agree and nothing checking that they do.
--
-- The duplicate postings are counted and not removed. One vendor document posted
-- twice is two rows of real money in the ledger and picking the survivor needs a
-- rule nobody has written down here yet.
with lines as (

    select
        margin.vendor_id,
        margin.period,
        count(*) as lines,
        sum(margin.extended_revenue_usd) as revenue_usd,
        sum(margin.extended_cost_usd) as cost_usd,
        sum(margin.invoiced_margin_usd) as invoiced_margin_usd,
        sum(rollup.credits_naming_this_line) as credits,
        sum(rollup.named_against_cost_cents) as named_against_cost_cents,
        sum(rollup.named_against_revenue_cents) as named_against_revenue_cents,
        sum(rollup.allocated_against_cost_cents) as allocated_against_cost_cents,
        sum(rollup.allocated_against_revenue_cents) as allocated_against_revenue_cents,
        sum(rollup.allocated_from_a_rebate_cents) as allocated_from_a_rebate_cents,
        sum(rollup.duplicate_postings) as duplicate_postings,
        sum(rollup.credits_over_their_line) as credits_over_their_line,
        max(rollup.slowest_credit_days) as slowest_credit_days
    from {{ ref('fct_invoice_line_margin') }} as margin
    inner join {{ ref('credit_rollup_by_line') }} as rollup
        on margin.line_id = rollup.line_id
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
    lines.credits,
    {{ cents_to_usd('lines.named_against_cost_cents') }} as named_against_cost_usd,
    {{ cents_to_usd('lines.named_against_revenue_cents') }} as named_against_revenue_usd,
    lines.invoiced_margin_usd
        + {{ cents_to_usd('lines.named_against_cost_cents') }}
        - {{ cents_to_usd('lines.named_against_revenue_cents') }}
        as margin_after_named_credits_usd,
    {{ cents_to_usd('lines.allocated_from_a_rebate_cents') }} as allocated_from_a_rebate_usd,
    lines.invoiced_margin_usd
        + {{ cents_to_usd('lines.allocated_against_cost_cents') }}
        - {{ cents_to_usd('lines.allocated_against_revenue_cents') }}
        as margin_after_allocated_credits_usd,
    -- The signed difference between the two, in cents, so the question of
    -- whether allocation moved this row is an integer comparison.
    lines.allocated_against_cost_cents
        - lines.allocated_against_revenue_cents
        - lines.named_against_cost_cents
        + lines.named_against_revenue_cents as margin_moved_cents,
    lines.duplicate_postings,
    lines.credits_over_their_line,
    lines.slowest_credit_days
from lines
left join {{ ref('stg_vendors') }} as vendors
    on lines.vendor_id = vendors.vendor_id
