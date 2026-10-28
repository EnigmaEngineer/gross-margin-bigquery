-- Line grain. One row per invoice line, at the margin the invoice itself claims.
--
-- No credit touches this table. Credits are what make the invoiced number wrong
-- and allocating them back to a line is its own problem with its own lookback,
-- so this is the before picture and it is deliberately not the answer.
--
-- Every join out of the line is a left join. A line whose product version cannot
-- be resolved and an invoice with no partner both have to survive to the mart,
-- because a row that vanishes between layers is the hardest kind of wrong number
-- to find.
with lines as (

    select * from {{ ref('stg_invoice_lines') }}

),

invoices as (

    select * from {{ ref('stg_invoices') }}

),

versions as (

    select * from {{ ref('dim_product_version') }}

)

select
    lines.line_id,
    lines.invoice_id,
    lines.product_id,
    invoices.invoice_date,
    invoices.period,
    invoices.partner_id,
    invoices.channel,
    invoices.has_partner,
    partners.region,
    partners.segment,
    versions.product_line,
    versions.vendor_id,
    versions.versions > 1 as product_was_reassigned,
    versions.product_id is null as product_version_unresolved,
    lines.qty,
    lines.extended_revenue_usd,
    lines.extended_cost_usd,
    lines.extended_revenue_usd - lines.extended_cost_usd as invoiced_margin_usd
from lines
inner join invoices
    on lines.invoice_id = invoices.invoice_id
left join versions
    on lines.product_id = versions.product_id
    and versions.effective_from <= invoices.invoice_date
    and (versions.effective_to is null or invoices.invoice_date < versions.effective_to)
left join {{ ref('stg_partners') }} as partners
    on invoices.partner_id = partners.partner_id
