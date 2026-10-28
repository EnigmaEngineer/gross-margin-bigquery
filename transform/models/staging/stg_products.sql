-- A product can be moved to another product line partway through the window, so
-- product_id is not unique here and effective_from is half the key. The version
-- that was in force on a given day is resolved in dim_product_version.
select
    product_id,
    product_line,
    vendor_id,
    {{ as_date('effective_from') }} as effective_from
from {{ source('raw', 'products') }}
