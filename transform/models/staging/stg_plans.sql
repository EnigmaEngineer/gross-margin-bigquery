-- rebate_rate_pct is read by nobody yet. It is the input to the rebate
-- allocation and it stays declared here so the column contract is settled
-- before the model that needs it exists.
select
    partner_id,
    plan_code,
    {{ money('rebate_rate_pct') }} as rebate_rate_pct,
    {{ as_date('effective_from') }} as effective_from
from {{ source('raw', 'plans') }}
