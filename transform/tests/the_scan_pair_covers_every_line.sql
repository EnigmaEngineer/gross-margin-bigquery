-- The pair is meant to be the fact table in two physical layouts. If either half
-- holds fewer rows then the bytes scanned comparison is partly a comparison of
-- how much data each side was given.
with fact as (

    select count(*) as lines from {{ ref('fct_invoice_line_margin') }}

),

pair as (

    select
        (select count(*) from {{ ref('scan_margin_lines_unclustered') }}) as unclustered,
        (select count(*) from {{ ref('scan_margin_lines_clustered') }}) as clustered

)

select
    fact.lines,
    pair.unclustered,
    pair.clustered
from fact
cross join pair
where pair.unclustered != fact.lines
   or pair.clustered != fact.lines
