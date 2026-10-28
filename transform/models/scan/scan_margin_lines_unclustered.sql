-- The control. No partitioning and no clustering, so a query restricting on
-- invoice_date reads every block.
{{ config(materialized='table') }}

{{ scan_margin_body() }}
