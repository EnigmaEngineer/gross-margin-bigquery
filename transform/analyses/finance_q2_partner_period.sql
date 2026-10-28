-- Query two of three. The partner league table for one period.
--
-- Restricts on the partition column and groups on the second cluster key rather
-- than the first. A prefix ordered clustering should help this one less than
-- query one, which is the part of the comparison worth having.
select
    partner_id,
    region,
    segment,
    sum(extended_revenue_usd) as revenue_usd,
    sum(invoiced_margin_usd) as invoiced_margin_usd
from {{ scan_relation() }}
where invoice_date >= date '2026-03-01'
  and invoice_date < date '2026-04-01'
group by 1, 2, 3
order by 5 desc
