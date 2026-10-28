-- Line grain, and the one place credits are rolled up.
--
-- Each of the three margin marts used to aggregate fct_credit_memo for itself.
-- Three copies of one group by is three chances for them to drift, and the
-- allocation table makes it four, so the rollup happens once here and the marts
-- sum this instead.
--
-- Two sets of columns, because there are two defensible answers and a reader has
-- to be able to see both.
--
--   named_*      the credit sits entirely on the line it quotes. This is what the
--                billing system says and it is what a vendor would recognise.
--   allocated_*  the credit is spread by fct_credit_allocation. A volume rebate
--                stops distorting one line and lands across the quarter it was
--                earned in.
--
-- Everything is cents and everything is an integer. A sum of integers is exact
-- and does not depend on the order the rows arrive in, and the dollars are
-- derived once at the top of each mart. The first version of this model summed
-- dollars, which on DuckDB were floats, and the comparison between the two
-- answers returned a different count on consecutive builds of the same rows.
with named as (

    select
        line_id,
        count(*) as credits_naming_this_line,
        sum(case when applies_to = 'cost'
            then {{ to_cents('amount_usd') }} else 0 end) as named_against_cost_cents,
        sum(case when applies_to = 'revenue'
            then {{ to_cents('amount_usd') }} else 0 end) as named_against_revenue_cents,
        sum(case when is_later_posting_of_the_same_doc then 1 else 0 end)
            as duplicate_postings,
        sum(case when exceeds_the_line_it_adjusts then 1 else 0 end)
            as credits_over_their_line,
        max(observable_lag_days) as slowest_credit_days
    from {{ ref('fct_credit_memo') }}
    where is_placeable
    group by 1

),

allocated as (

    select
        line_id,
        count(*) as allocations_touching_this_line,
        sum(case when applies_to = 'cost' then allocated_cents else 0 end)
            as allocated_against_cost_cents,
        sum(case when applies_to = 'revenue' then allocated_cents else 0 end)
            as allocated_against_revenue_cents,
        sum(case when allocation_scope = 'vendor_quarter' then allocated_cents else 0 end)
            as allocated_from_a_rebate_cents
    from {{ ref('fct_credit_allocation') }}
    where line_id is not null
    group by 1

),

-- A line can appear in one side and not the other. A rebate reaches lines that
-- no credit names, and a line whose only credit is a rebate loses its named
-- amount to the rest of the quarter. So the spine is the line table and both
-- sides are left joined onto it.
lines as (

    select line_id from {{ ref('fct_invoice_line_margin') }}

)

select
    lines.line_id,
    coalesce(named.credits_naming_this_line, 0) as credits_naming_this_line,
    coalesce(named.named_against_cost_cents, 0) as named_against_cost_cents,
    coalesce(named.named_against_revenue_cents, 0) as named_against_revenue_cents,
    coalesce(allocated.allocations_touching_this_line, 0)
        as allocations_touching_this_line,
    coalesce(allocated.allocated_against_cost_cents, 0) as allocated_against_cost_cents,
    coalesce(allocated.allocated_against_revenue_cents, 0)
        as allocated_against_revenue_cents,
    coalesce(allocated.allocated_from_a_rebate_cents, 0) as allocated_from_a_rebate_cents,
    -- What allocation did to this line, signed. A credit against cost lifts the
    -- margin and one against revenue lowers it, so the two readings subtract in
    -- the same direction and the difference is the whole quantity the day is
    -- about.
    coalesce(allocated.allocated_against_cost_cents, 0)
        - coalesce(allocated.allocated_against_revenue_cents, 0)
        - coalesce(named.named_against_cost_cents, 0)
        + coalesce(named.named_against_revenue_cents, 0) as margin_moved_cents,
    coalesce(named.duplicate_postings, 0) as duplicate_postings,
    coalesce(named.credits_over_their_line, 0) as credits_over_their_line,
    named.slowest_credit_days
from lines
left join named on lines.line_id = named.line_id
left join allocated on lines.line_id = allocated.line_id
