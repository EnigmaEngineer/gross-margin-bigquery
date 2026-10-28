-- Margin by product line and period.
--
-- This is the grain the effective dated product dimension matters to. A product
-- that moved from one line to another partway through the window contributes to
-- both lines, split at the date it moved, and reassigned_lines says how many rows
-- in the period came from a product with more than one version. Without that
-- column a reader has no way to tell a real shift in mix from a dimension edit.
--
-- Credits arrive here for the first time. They were left out while a credit sat
-- on one line, because a rebate parked on a single product line is a figure that
-- says more about the billing system than about the product. Allocation is what
-- makes the column worth having, and the named total is kept beside it so the
-- difference is visible rather than asserted.
select
    coalesce(margin.product_line, 'unresolved') as product_line,
    margin.period,
    count(*) as lines,
    count(distinct margin.product_id) as products,
    sum(case when margin.product_was_reassigned then 1 else 0 end) as reassigned_lines,
    sum(case when margin.product_version_unresolved then 1 else 0 end) as unresolved_lines,
    sum(margin.extended_revenue_usd) as revenue_usd,
    sum(margin.extended_cost_usd) as cost_usd,
    sum(margin.invoiced_margin_usd) as invoiced_margin_usd,
    sum(rollup.credits_naming_this_line) as credits,
    sum(margin.invoiced_margin_usd)
        + {{ cents_to_usd('sum(rollup.named_against_cost_cents)') }}
        - {{ cents_to_usd('sum(rollup.named_against_revenue_cents)') }}
        as margin_after_named_credits_usd,
    {{ cents_to_usd('sum(rollup.allocated_from_a_rebate_cents)') }}
        as allocated_from_a_rebate_usd,
    sum(margin.invoiced_margin_usd)
        + {{ cents_to_usd('sum(rollup.allocated_against_cost_cents)') }}
        - {{ cents_to_usd('sum(rollup.allocated_against_revenue_cents)') }}
        as margin_after_allocated_credits_usd,
    sum(rollup.allocated_against_cost_cents)
        - sum(rollup.allocated_against_revenue_cents)
        - sum(rollup.named_against_cost_cents)
        + sum(rollup.named_against_revenue_cents) as margin_moved_cents
from {{ ref('fct_invoice_line_margin') }} as margin
inner join {{ ref('credit_rollup_by_line') }} as rollup
    on margin.line_id = rollup.line_id
group by 1, 2
