-- Two kinds of wrong row live in this table and the staging layer only names
-- them. It does not drop them and it does not repair them.
--
-- vendor_doc_no is the vendor's own document number and it is not unique, because
-- one vendor document can be posted twice under two internal credit ids. The
-- duplicate is a real pair of rows and deciding which one survives is an
-- allocation question rather than a cleaning question.
--
-- line_id can quote a line this system does not hold. That row cannot be
-- attributed to a product or a vendor and it still has to be visible, because a
-- credit nobody can place is the thing a reconciliation exists to surface.
with ranked as (

    select
        credit_id,
        vendor_doc_no,
        nullif(invoice_id, '') as invoice_id,
        nullif(line_id, '') as line_id,
        credit_type,
        applies_to,
        {{ money('amount_usd') }} as amount_usd,
        {{ as_date('raised_date') }} as raised_date,
        {{ as_date('posted_date') }} as posted_date,
        period,
        row_number() over (
            partition by vendor_doc_no
            order by credit_id
        ) as doc_no_seq,
        count(*) over (partition by vendor_doc_no) as doc_no_rows
    from {{ source('raw', 'credit_memos') }}

)

select
    credit_id,
    vendor_doc_no,
    invoice_id,
    line_id,
    credit_type,
    applies_to,
    amount_usd,
    raised_date,
    posted_date,
    period,
    doc_no_rows > 1 as vendor_doc_no_posted_twice,
    doc_no_rows > 1 and doc_no_seq > 1 as is_later_posting_of_the_same_doc,
    {{ dbt.datediff('raised_date', 'posted_date', 'day') }} as calendar_hold_days
from ranked
