-- Credit grain. Each credit resolved to the line and the invoice it quotes, and
-- flagged where it cannot be.
--
-- A credit that names a line this system does not hold keeps its row and gets
-- is_placeable = false. The marts below count those separately rather than
-- adding them into a vendor's total, which would put real money against the
-- wrong vendor.
--
-- Nothing here is allocated. A volume rebate names a line only because the
-- generator had to put something there, and spreading it across the quarter it
-- actually belongs to is the reconciliation problem rather than this one.
with credits as (

    select * from {{ ref('stg_credit_memos') }}

),

margin_lines as (

    select
        line_id,
        invoice_id,
        period as invoice_period,
        invoice_date,
        partner_id,
        product_line,
        vendor_id,
        extended_revenue_usd,
        extended_cost_usd
    from {{ ref('fct_invoice_line_margin') }}

)

select
    credits.credit_id,
    credits.vendor_doc_no,
    credits.credit_type,
    credits.applies_to,
    credits.amount_usd,
    credits.raised_date,
    credits.posted_date,
    credits.period as credit_period,
    credits.calendar_hold_days,
    credits.vendor_doc_no_posted_twice,
    credits.is_later_posting_of_the_same_doc,
    credits.line_id,
    -- Two invoice references and they are not the same question. quoted_invoice_id
    -- is what the vendor wrote on the credit. invoice_id is the one reached
    -- through a line this system holds, so it is null exactly when the line is
    -- unknown. Six of the nine credits quoting a line we do not have still quote
    -- an invoice we do, and dropping the vendor's own reference threw away the
    -- only route left to place them.
    credits.invoice_id as quoted_invoice_id,
    margin_lines.invoice_id,
    margin_lines.invoice_period,
    margin_lines.invoice_date,
    margin_lines.partner_id,
    margin_lines.product_line,
    margin_lines.vendor_id,
    margin_lines.line_id is not null as is_placeable,
    {{ dbt.datediff('margin_lines.invoice_date', 'credits.posted_date', 'day') }}
        as observable_lag_days,
    -- A credit worth more than the line it adjusts drives that line's cost
    -- negative once it is applied. Flagged and not capped, because the cap would
    -- hide the only evidence that the row is wrong.
    case
        when margin_lines.line_id is null then null
        when credits.applies_to = 'cost'
            then credits.amount_usd > margin_lines.extended_cost_usd
        else credits.amount_usd > margin_lines.extended_revenue_usd
    end as exceeds_the_line_it_adjusts
from credits
left join margin_lines
    on credits.line_id = margin_lines.line_id
