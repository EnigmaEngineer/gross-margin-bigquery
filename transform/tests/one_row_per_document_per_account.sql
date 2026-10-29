-- fct_gl_posting full outer joins billing to the ledger on three columns. If
-- either side held two rows for one document and account the join would fan out
-- and both the variance and its decomposition would double count.
select
    source_type,
    source_id,
    account,
    count(*) as rows_for_this_document
from {{ ref('fct_gl_posting') }}
group by source_type, source_id, account
having count(*) > 1
