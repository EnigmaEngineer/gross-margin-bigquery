-- The product dimension is effective dated, so a margin number by product line
-- depends on which version of the product you joined to. Closing each version
-- with the start of the next one turns that into one answer instead of a choice
-- nobody records.
--
-- The last version of every product is left open rather than given a far future
-- end date. An open interval says the version is current. A sentinel date says
-- somebody picked a year.
select
    product_id,
    product_line,
    vendor_id,
    effective_from,
    lead(effective_from) over (
        partition by product_id
        order by effective_from
    ) as effective_to,
    row_number() over (
        partition by product_id
        order by effective_from
    ) as version_seq,
    count(*) over (partition by product_id) as versions
from {{ ref('stg_products') }}
