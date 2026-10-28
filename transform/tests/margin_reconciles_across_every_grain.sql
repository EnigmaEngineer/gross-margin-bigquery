-- The three marts aggregate the same fact table on three different keys, so
-- their totals have to agree with it and with each other.
--
-- The partner mart is the one that can quietly disagree, because its key is
-- nullable. If a run ever drops the rows with no partner this check is what
-- notices, and it notices by the amount rather than by the row count.
--
-- Rounded to whole cents before comparing. The sums are exact decimals and a
-- difference below a cent would be an artefact of the group by order rather
-- than a lost row.
with line_total as (

    select round(sum(invoiced_margin_usd), 2) as margin
    from {{ ref('fct_invoice_line_margin') }}

),

by_vendor as (

    select round(sum(invoiced_margin_usd), 2) as margin from {{ ref('margin_by_vendor') }}

),

by_partner as (

    select round(sum(invoiced_margin_usd), 2) as margin from {{ ref('margin_by_partner') }}

),

by_product_line as (

    select round(sum(invoiced_margin_usd), 2) as margin
    from {{ ref('margin_by_product_line') }}

)

select
    line_total.margin as line_margin,
    by_vendor.margin as vendor_margin,
    by_partner.margin as partner_margin,
    by_product_line.margin as product_line_margin
from line_total
cross join by_vendor
cross join by_partner
cross join by_product_line
where by_vendor.margin != line_total.margin
   or by_partner.margin != line_total.margin
   or by_product_line.margin != line_total.margin
