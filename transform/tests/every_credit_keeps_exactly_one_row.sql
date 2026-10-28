-- fct_credit_memo left joins credits to lines. If line_id were ever not unique
-- in the fact table this would fan out, and a credit counted twice is money
-- counted twice.
with staged as (

    select count(*) as credits from {{ ref('stg_credit_memos') }}

),

modelled as (

    select count(*) as credits from {{ ref('fct_credit_memo') }}

)

select
    staged.credits as staged_credits,
    modelled.credits as modelled_credits
from staged
cross join modelled
where staged.credits != modelled.credits
