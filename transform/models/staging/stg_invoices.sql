-- partner_id is nullable on purpose. The corpus carries invoices with no partner
-- on them, which is the real gap between a billing system and a CRM, and an
-- inner join to the partner dimension would make those rows disappear from every
-- margin number without anything reporting a loss. They are kept and flagged.
select
    invoice_id,
    {{ as_date('invoice_date') }} as invoice_date,
    period,
    nullif(partner_id, '') as partner_id,
    channel,
    nullif(partner_id, '') is not null as has_partner
from {{ source('raw', 'invoices') }}
