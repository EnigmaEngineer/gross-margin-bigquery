{{
  config(
    materialized='incremental',
    unique_key='credit_id',
    incremental_strategy='delete+insert',
  )
}}

-- Allocation grain. One row per credit per line the credit is spread over, in
-- whole cents that sum back to the credit exactly.
--
-- fct_credit_memo resolves a credit to the line it names and stops there. That
-- is right for price protection and for a return, where the vendor really is
-- adjusting one line. It is wrong for a volume rebate, which is earned across a
-- quarter of purchases and lands quoting whichever line the billing system had
-- to put it against. Reading a rebate as one line's cost makes that line look
-- unprofitable and every other line in the quarter look better than it was.
--
-- So each credit gets a scope and the scope decides what it spreads over. The
-- scopes degrade in one direction, from the narrowest placement to no placement
-- at all, and a credit never silently falls off the end.
--
--   direct            the credit names a line we hold and the kind is a line
--                     level adjustment. One row, the whole amount.
--   vendor_quarter    a volume rebate. Spread across every line billed for that
--                     vendor in the quarter the rebate was earned in.
--   invoice           the credit names a line we do not hold but an invoice we
--                     do. Spread across that invoice's lines.
--   unallocated       neither the line nor the invoice resolves. One row with no
--                     line and the whole amount sitting in it, because a credit
--                     nobody can place is the number a reconciliation exists to
--                     report.
--
-- The unique key is the credit and not the allocation row. A credit that is
-- re-processed has to lose every row it previously wrote, and keying on the pair
-- would strand the rows whose scope shrank.
--
-- TODO: the vendor_quarter basis is extended cost, which assumes a rebate is
-- earned in proportion to spend. A tiered rebate is not, and plans.csv already
-- carries a rate column nothing reads. That needs the plan terms modelled first.

with credits as (

    select * from {{ ref('fct_credit_memo') }}
    {% if is_incremental() %}
    where posted_date >= {{ allocation_lookback_start() }}
    {% endif %}

),

lines as (

    select
        line_id,
        invoice_id,
        invoice_date,
        {{ quarter_start('invoice_date') }} as invoice_quarter_start,
        vendor_id,
        partner_id,
        product_line,
        period,
        extended_revenue_usd,
        extended_cost_usd
    from {{ ref('fct_invoice_line_margin') }}

),

-- Which lines each credit spreads over, one row per candidate pair. A credit
-- appears in exactly one branch, because the branches are cut on is_placeable
-- and credit_type and invoice_id and those are disjoint.
scoped as (

    select
        credits.credit_id,
        'direct' as allocation_scope,
        lines.line_id,
        case when credits.applies_to = 'cost'
            then lines.extended_cost_usd
            else lines.extended_revenue_usd
        end as basis_usd
    from credits
    inner join lines on credits.line_id = lines.line_id
    where credits.is_placeable
      and credits.credit_type <> 'volume_rebate'

    union all

    select
        credits.credit_id,
        'vendor_quarter' as allocation_scope,
        lines.line_id,
        lines.extended_cost_usd as basis_usd
    from credits
    inner join lines as quoted on credits.line_id = quoted.line_id
    inner join lines
        on lines.vendor_id = quoted.vendor_id
        and lines.invoice_quarter_start = quoted.invoice_quarter_start
    where credits.is_placeable
      and credits.credit_type = 'volume_rebate'

    union all

    select
        credits.credit_id,
        'invoice' as allocation_scope,
        lines.line_id,
        case when credits.applies_to = 'cost'
            then lines.extended_cost_usd
            else lines.extended_revenue_usd
        end as basis_usd
    from credits
    inner join lines on credits.quoted_invoice_id = lines.invoice_id
    where not credits.is_placeable

),

-- Credits that reached no branch at all. Written out explicitly rather than
-- left to a left join, because the count of them is a published figure and a
-- row that is absent cannot be counted.
unplaced as (

    select
        credits.credit_id,
        'unallocated' as allocation_scope,
        cast(null as {{ text_type() }}) as line_id,
        cast(0 as {{ exact_type() }}) as basis_usd
    from credits
    left join scoped on credits.credit_id = scoped.credit_id
    where scoped.credit_id is null

),

candidates as (

    select * from scoped
    union all
    select * from unplaced

),

-- Exact cents from here down. The share is computed against the scope total and
-- a scope whose basis sums to zero falls back to an equal split, because a
-- rebate on a quarter of fully discounted lines still has to go somewhere and
-- dividing by zero would drop it.
weighted as (

    select
        candidates.credit_id,
        candidates.allocation_scope,
        candidates.line_id,
        candidates.basis_usd,
        {{ to_cents('candidates.basis_usd') }} as basis_cents,
        sum({{ to_cents('candidates.basis_usd') }})
            over (partition by candidates.credit_id) as scope_basis_cents,
        count(*) over (partition by candidates.credit_id) as scope_lines,
        {{ to_cents('credits.amount_usd') }} as credit_cents,
        credits.credit_type,
        credits.applies_to,
        credits.posted_date,
        credits.raised_date,
        credits.credit_period,
        credits.vendor_id as credit_vendor_id,
        credits.is_later_posting_of_the_same_doc
    from candidates
    inner join credits on candidates.credit_id = credits.credit_id

),

shared as (

    select
        *,
        case
            when allocation_scope = 'unallocated' then cast(credit_cents as {{ exact_type() }})
            when scope_basis_cents > 0
                then cast(credit_cents as {{ exact_type() }})
                     * basis_cents / scope_basis_cents
            else cast(credit_cents as {{ exact_type() }}) / scope_lines
        end as exact_cents
    from weighted

),

-- Largest remainder. Floor every share, then hand the leftover cents out one at
-- a time to the largest fractional parts. line_id breaks a tie so the result does
-- not depend on row order, which is the difference between a figure that
-- reproduces and one that moves between runs.
floored as (

    select
        *,
        cast(floor(exact_cents) as {{ int_type() }}) as floor_cents,
        exact_cents - floor(exact_cents) as remainder
    from shared

),

ranked as (

    select
        *,
        credit_cents - sum(floor_cents) over (partition by credit_id) as leftover_cents,
        row_number() over (
            partition by credit_id
            order by remainder desc, line_id
        ) as remainder_rank
    from floored

)

select
    credit_id,
    line_id,
    allocation_scope,
    credit_type,
    applies_to,
    posted_date,
    raised_date,
    credit_period,
    credit_vendor_id,
    is_later_posting_of_the_same_doc,
    scope_lines,
    credit_cents,
    floor_cents + case when remainder_rank <= leftover_cents then 1 else 0 end
        as allocated_cents,
    {{ cents_to_usd('floor_cents + case when remainder_rank <= leftover_cents then 1 else 0 end') }}
        as allocated_usd
from ranked
