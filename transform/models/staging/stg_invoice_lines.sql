select
    line_id,
    invoice_id,
    product_id,
    cast(qty as integer) as qty,
    {{ money('unit_price_usd') }} as unit_price_usd,
    {{ money('unit_cost_usd') }} as unit_cost_usd,
    cast(qty as integer) * {{ money('unit_price_usd') }} as extended_revenue_usd,
    cast(qty as integer) * {{ money('unit_cost_usd') }} as extended_cost_usd
from {{ source('raw', 'invoice_lines') }}
