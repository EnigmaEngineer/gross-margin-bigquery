select
    partner_id,
    partner_name,
    region,
    segment
from {{ source('raw', 'partners') }}
