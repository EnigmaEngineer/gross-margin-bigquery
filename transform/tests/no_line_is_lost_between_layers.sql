-- The fact table joins out to three dimensions and every one of those joins is
-- a left join for a reason. This fails if the line count moved in either
-- direction, which catches a dropped row and a fan out with the same check.
with staged as (

    select count(*) as lines from {{ ref('stg_invoice_lines') }}

),

modelled as (

    select count(*) as lines from {{ ref('fct_invoice_line_margin') }}

)

select
    staged.lines as staged_lines,
    modelled.lines as modelled_lines
from staged
cross join modelled
where staged.lines != modelled.lines
