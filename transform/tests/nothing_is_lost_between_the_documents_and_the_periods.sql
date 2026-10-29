-- The period model groups the document model. A row that fails to land in the
-- grid, on either side, would quietly shrink a total rather than raising
-- anything, which is the failure shape this project has already met twice.
with documents as (

    select
        coalesce(sum(billing_cents), 0) as billing_cents,
        coalesce(sum(gl_cents), 0) as ledger_cents
    from {{ ref('fct_gl_posting') }}

),

periods as (

    select
        coalesce(sum(billing_cents), 0) as billing_cents,
        coalesce(sum(ledger_cents), 0) as ledger_cents
    from {{ ref('recon_billing_to_gl') }}

)

select
    documents.billing_cents as document_billing_cents,
    periods.billing_cents as period_billing_cents,
    documents.ledger_cents as document_ledger_cents,
    periods.ledger_cents as period_ledger_cents
from documents
cross join periods
where documents.billing_cents != periods.billing_cents
   or documents.ledger_cents != periods.ledger_cents
