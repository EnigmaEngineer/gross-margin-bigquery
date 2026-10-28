-- The layout comparison is only a layout comparison if the two tables hold the
-- same rows. This is the check that makes the bytes scanned number mean
-- something, and it is the one check in this project that would still matter if
-- every other one were deleted.
with unclustered as (

    select line_id, invoiced_margin_usd from {{ ref('scan_margin_lines_unclustered') }}

),

clustered as (

    select line_id, invoiced_margin_usd from {{ ref('scan_margin_lines_clustered') }}

)

select
    coalesce(unclustered.line_id, clustered.line_id) as line_id,
    unclustered.invoiced_margin_usd as unclustered_margin,
    clustered.invoiced_margin_usd as clustered_margin
from unclustered
full outer join clustered
    on unclustered.line_id = clustered.line_id
where unclustered.line_id is null
   or clustered.line_id is null
   or unclustered.invoiced_margin_usd != clustered.invoiced_margin_usd
