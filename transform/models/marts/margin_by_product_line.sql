-- Margin by product line and period.
--
-- This is the grain the effective dated product dimension matters to. A product
-- that moved from one line to another partway through the window contributes to
-- both lines, split at the date it moved, and reassigned_lines says how many rows
-- in the period came from a product with more than one version. Without that
-- column a reader has no way to tell a real shift in mix from a dimension edit.
select
    coalesce(product_line, 'unresolved') as product_line,
    period,
    count(*) as lines,
    count(distinct product_id) as products,
    sum(case when product_was_reassigned then 1 else 0 end) as reassigned_lines,
    sum(case when product_version_unresolved then 1 else 0 end) as unresolved_lines,
    sum(extended_revenue_usd) as revenue_usd,
    sum(extended_cost_usd) as cost_usd,
    sum(invoiced_margin_usd) as invoiced_margin_usd
from {{ ref('fct_invoice_line_margin') }}
group by 1, 2
