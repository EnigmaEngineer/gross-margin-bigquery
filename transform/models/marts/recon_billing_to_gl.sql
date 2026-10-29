-- Period and account grain. What billing says, what the ledger says, and the
-- gap broken into the part that clears by itself and the part that does not.
--
-- The variance for a period is not one number with one cause, and treating it as
-- one is how a close ends up chasing an invoice that was never missing. An
-- invoice billed on the last business day of March posts to the ledger in April.
-- March is short by its amount and April is long by the same amount, nobody has
-- lost anything, and it resolves next month with no action. A document the
-- ledger never received looks identical at the period total and does not
-- resolve. So the model carries both terms separately.
--
-- The decomposition is exact rather than approximate and that is worth stating
-- plainly. Every document contributes to exactly one term in exactly one period,
-- or to two terms in two periods when the two sides disagree about the period, so
-- the five terms add back to the variance in whole cents with nothing left over.
-- transform/tests/the_variance_decomposition_adds_up.sql asserts it with no
-- tolerance, which is the only way the word exact earns its place here.
--
-- What this model does NOT do is reopen anything. `inside_the_reopen_window`
-- says whether a period is still within reach of the reconciliation, and the
-- window's width is argued for in transform/macros/reconciliation.sql rather
-- than chosen here.
with postings as (

    select * from {{ ref('fct_gl_posting') }}

),

as_of as (

    select max(gl_posted_date) as newest_posting from postings

),

-- One signed contribution per document per period. Writing it long rather than
-- as five correlated subqueries is what makes the identity below readable, and
-- it is also why the test can be a sum rather than a reimplementation.
contributions as (

    select
        billing_period as period,
        account,
        'unposted' as term,
        billing_cents as cents
    from postings
    where match_class = 'in_billing_not_in_ledger'

    union all

    select
        gl_period as period,
        account,
        'ledger_only' as term,
        gl_cents as cents
    from postings
    where match_class = 'in_ledger_not_in_billing'

    union all

    select
        billing_period as period,
        account,
        'amount_difference' as term,
        difference_cents as cents
    from postings
    where match_class = 'matched_same_period'

    -- Matched, and the two sides put it in different periods. Three rows, and
    -- the reason it is three rather than two is a defect this model had when it
    -- was first written.
    --
    -- The obvious version contributes the billing amount to the billing period
    -- and the ledger amount to the ledger period. The variance still adds up,
    -- so the test passed. But a document that is BOTH posted in the wrong period
    -- AND keyed at the wrong amount then has its amount difference counted as
    -- timing, which says it will clear next month, and it will not. Nothing in
    -- the corpus had both defects at once when this was found, so the whole
    -- timing column summed to zero and looked correct.
    --
    -- So the amount that moves between periods is the LEDGER amount, which is
    -- what is actually going to arrive, and the difference between the two sides
    -- stays a break in the billing period where the rest of the differences sit.
    -- Timing now nets to zero across the corpus by construction rather than by
    -- luck, and transform/tests asserts that.
    union all

    select
        billing_period as period,
        account,
        'awaiting_the_ledger' as term,
        gl_cents as cents
    from postings
    where match_class in ('matched_ledger_is_later', 'matched_ledger_is_earlier')

    union all

    select
        gl_period as period,
        account,
        'from_another_period' as term,
        gl_cents as cents
    from postings
    where match_class in ('matched_ledger_is_later', 'matched_ledger_is_earlier')

    union all

    select
        billing_period as period,
        account,
        'amount_difference' as term,
        difference_cents as cents
    from postings
    where match_class in ('matched_ledger_is_later', 'matched_ledger_is_earlier')

),

terms as (

    select
        period,
        account,
        sum(case when term = 'unposted' then cents else 0 end)
            as unposted_cents,
        sum(case when term = 'ledger_only' then cents else 0 end)
            as ledger_only_cents,
        sum(case when term = 'amount_difference' then cents else 0 end)
            as amount_difference_cents,
        sum(case when term = 'awaiting_the_ledger' then cents else 0 end)
            as awaiting_the_ledger_cents,
        sum(case when term = 'from_another_period' then cents else 0 end)
            as from_another_period_cents
    from contributions
    group by period, account

),

sides as (

    select
        billing_period as period,
        account,
        sum(billing_cents) as billing_cents,
        count(*) as billing_documents
    from postings
    where billing_period is not null
    group by billing_period, account

),

ledger_sides as (

    select
        gl_period as period,
        account,
        sum(gl_cents) as ledger_cents,
        count(*) as ledger_entries
    from postings
    where gl_period is not null
    group by gl_period, account

),

grid as (

    select period, account from terms
    union
    select period, account from sides
    union
    select period, account from ledger_sides

)

select
    grid.period,
    grid.account,
    coalesce(sides.billing_cents, 0) as billing_cents,
    coalesce(ledger_sides.ledger_cents, 0) as ledger_cents,
    coalesce(sides.billing_documents, 0) as billing_documents,
    coalesce(ledger_sides.ledger_entries, 0) as ledger_entries,
    coalesce(sides.billing_cents, 0) - coalesce(ledger_sides.ledger_cents, 0)
        as variance_cents,

    coalesce(terms.unposted_cents, 0) as unposted_cents,
    coalesce(terms.ledger_only_cents, 0) as ledger_only_cents,
    coalesce(terms.amount_difference_cents, 0) as amount_difference_cents,
    coalesce(terms.awaiting_the_ledger_cents, 0) as awaiting_the_ledger_cents,
    coalesce(terms.from_another_period_cents, 0) as from_another_period_cents,

    -- The part of the gap that is one document sitting in two periods. A
    -- document billed here and posted elsewhere, netted against one posted
    -- here and billed elsewhere. It resolves itself and nobody has to chase it.
    --
    -- Named period_shift rather than timing because gross_margin/manifest.py
    -- refuses a measurement key that reads as an elapsed time, and `timing` is
    -- on that list. The guard is right. A column name is the cheaper thing to
    -- change, and loosening the guard to fit one would let the next real wall
    -- time through.
    coalesce(terms.awaiting_the_ledger_cents, 0)
        - coalesce(terms.from_another_period_cents, 0) as period_shift_cents,

    -- The part that does not clear. Somebody has to go and look at these.
    coalesce(terms.unposted_cents, 0)
        + coalesce(terms.amount_difference_cents, 0)
        - coalesce(terms.ledger_only_cents, 0) as break_cents,

    {{ cents_to_usd('coalesce(sides.billing_cents, 0) - coalesce(ledger_sides.ledger_cents, 0)') }}
        as variance_usd,
    {{ cents_to_usd('coalesce(terms.unposted_cents, 0) + coalesce(terms.amount_difference_cents, 0) - coalesce(terms.ledger_only_cents, 0)') }}
        as break_usd,

    grid.period >= {{ period_of(recon_window_start('as_of.newest_posting')) }}
        as inside_the_reopen_window
from grid
left join terms
    on grid.period = terms.period and grid.account = terms.account
left join sides
    on grid.period = sides.period and grid.account = sides.account
left join ledger_sides
    on grid.period = ledger_sides.period and grid.account = ledger_sides.account
cross join as_of
