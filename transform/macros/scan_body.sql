{#
  The two halves of the scan pair share this body.

  Not for tidiness. The comparison is only meaningful if the two tables hold the
  same rows in the same columns, so the body has to be written once. Two copies
  of a select drift, and a drifted copy turns a layout measurement into a
  measurement of whatever else changed.

  The first version carried `where vendor_id is not null`, written to keep nulls
  out of the BigQuery cluster key. It removed no rows from this corpus, so it was
  a condition nobody could see working. It is gone and the invariant is now that
  the pair holds every line the fact table holds, which a test can assert.
  BigQuery tolerates a null cluster key and sorts those rows first.
#}
{% macro scan_margin_body() %}

select
    line_id,
    invoice_id,
    invoice_date,
    period,
    vendor_id,
    partner_id,
    product_line,
    region,
    segment,
    channel,
    qty,
    extended_revenue_usd,
    extended_cost_usd,
    invoiced_margin_usd
from {{ ref('fct_invoice_line_margin') }}

{% endmacro %}
