-- The treatment. Monthly partitions on invoice_date and clustering on
-- vendor_id then partner_id then product_line, chosen from how the three
-- queries in analyses/ actually filter and group. See macros/scan_layout.sql
-- for why the order is what it is.
--
-- On DuckDB both halves of this pair are plain tables, because DuckDB has
-- neither feature. That is not a shortcut. It is the reason the bytes scanned
-- comparison cannot be answered anywhere but BigQuery.
{%- set layout = scan_layout(clustered=true) -%}

{{ config(
    materialized='table',
    partition_by=layout['partition_by'],
    cluster_by=layout['cluster_by']
) }}

{{ scan_margin_body() }}
