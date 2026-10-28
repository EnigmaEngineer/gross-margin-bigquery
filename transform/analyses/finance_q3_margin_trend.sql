-- Query three of three. The full history trend by product line.
--
-- No date restriction at all, which is the honest worst case. A partitioned
-- table has no pruning to do here and clustering only helps if the engine can
-- skip blocks on the group by key. If the clustered half does not win this one
-- that is a result rather than a failure.
select
    product_line,
    period,
    sum(extended_revenue_usd) as revenue_usd,
    sum(extended_cost_usd) as cost_usd,
    sum(invoiced_margin_usd) as invoiced_margin_usd
from {{ scan_relation() }}
group by 1, 2
order by 2, 1
