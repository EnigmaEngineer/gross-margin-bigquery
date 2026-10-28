-- product_id alone is not the key. The pair is, and a second row for the same
-- pair would make the as of join in fct_invoice_line_margin return two rows per
-- line and double the margin.
select
    product_id,
    effective_from,
    count(*) as rows_for_this_version
from {{ ref('stg_products') }}
group by 1, 2
having count(*) > 1
