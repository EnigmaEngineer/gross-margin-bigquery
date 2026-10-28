-- Query one of three. Margin for one vendor over one quarter.
--
-- The shape finance actually asks for. One vendor, a date range inside a
-- partition, and a group by that the cluster prefix covers. This is the query
-- the clustered layout should help most.
--
-- The relation comes from the scan_relation macro, which resolves to whichever
-- half of the pair the probe is pointed at, so the two runs differ in the table
-- name and in nothing else. Naming that macro inside this comment is what broke
-- the first version of the query. Jinja renders a call in a line comment like
-- any other, and the rendered relation spans three lines, so two of them escaped
-- the comment and the query stopped parsing.
--
-- dbt compile returned zero on it. So did dbt parse. An analysis is rendered and
-- never executed, so nothing here would have caught it until somebody ran the
-- query by hand against a real warehouse.
select
    vendor_id,
    period,
    count(*) as lines,
    sum(extended_revenue_usd) as revenue_usd,
    sum(invoiced_margin_usd) as invoiced_margin_usd
from {{ scan_relation() }}
where invoice_date >= date '2025-10-01'
  and invoice_date < date '2026-01-01'
  and vendor_id = 'V003'
group by 1, 2
order by 2
