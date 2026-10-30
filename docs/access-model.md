# Who can read what

Two rules. A role reads the regions it owns, and a role that may not read cost
cannot get at cost by any route this warehouse offers. Both are written once,
in `gross_margin/access.py`, and two enforcements are generated from that one
list. Nothing on the BigQuery side has been applied.

## The rule list

`gross_margin/access.py` holds the roles and the relations. It imports nothing
outside the standard library, which is what keeps the check suite runnable on a
machine with no warehouse and no cloud credentials.

| Role | Regions | May read cost |
| --- | --- | --- |
| `finance_analyst` | every region, and the rows that belong to none | yes |
| `partner_ops` | every region | no |
| `region_<name>_analyst` | one | no |

The regional roles are generated from the region list rather than typed, and
`tests/test_access.py` asserts the declared regions are the regions the partner
dimension actually holds. A region added to the generator and not to the list
would otherwise be revenue with no role attached to it.

## Cost is not the column named cost

`fct_invoice_line_margin` carries `extended_cost_usd`, and it also carries
`extended_revenue_usd` and `invoiced_margin_usd`. Margin is revenue minus cost
and the subtraction is exact, so a reader holding revenue and margin holds
cost. Measured rather than argued: the probe builds the configuration that tags
the cost column and keeps margin, then recovers cost from it on 20,446 of
20,446 rows to the cent.

`margin_by_partner` is the sharper case. No column in it is called cost and the
quantity is still there, because `revenue_usd` minus `invoiced_margin_usd` is
the cost of the group.

So a relation declares the column sets that recover the quantity and the
withheld set is the closure over all of them. Exactly one of revenue and margin
has to go.

**It is margin that goes, and the reason is not symmetry.** Revenue is an
input. A role holding revenue from the staging layer, the billing system or the
invoices it already receives can subtract margin and get cost, so withholding
revenue here protects nothing it can get elsewhere. Margin is the one number in
the pair that exists nowhere outside these marts, which makes it the only one
withholding actually withholds.

What that costs is real. A regional role in this design reads revenue and not
margin, so it is not a margin consumer. Regional margin visibility is not a
column mask on this table. It needs a mart at a grain where cost cannot be
backed out of a row.

## The lines that belong to no region

Region arrives on the line by a left join to the partner, and the corpus
carries invoices with no partner on them. Those lines hold real revenue and
real cost and they carry a null region, so no regional predicate matches them.

| | lines | revenue |
| --- | --- | --- |
| whole book | 20,446 | 454,121,962.99 |
| reachable by the four regional roles | 20,353 | 452,097,146.17 |
| reachable by no region | 93 | 2,024,816.82 |

This is not a defect in the predicate and widening it would be wrong. Those
lines belong to no region, so a region scoped role cannot be given them. What
it means is that the four regional reports do not add up to the finance total,
and a reader summing them is short by 2,024,816.82 with nothing on screen
saying so.

`check_the_regional_views_do_not_add_up_to_the_finance_total_and_the_gap_is_named`
asserts the difference is exactly the unreachable set. A row missing for any
other reason fails it.

## The relations nobody reports off

The layout pair, `scan_margin_lines_clustered` and its unclustered half, each
hold every line of the margin table including cost and region. They exist to be
queried twice and compared, and they sit in the same dataset the reporting
roles hold. A control on the line table that does not cover them is a control
on one of three copies, and for two days it was.

They are in the rule list and marked as not a reporting surface. No role gets a
view on them, and on BigQuery they carry one row access policy naming only the
roles that may read cost, which refuses everybody else through the union rule
rather than through anybody remembering to withhold them.

`recon_billing_to_gl` is finance wide for a different reason. The ledger posts
at account grain and carries no partner, so there is nothing on a row to filter
a region on, and the variance identity holds over the whole book rather than
over a slice of it. A filtered reconciliation would add up to nothing a reader
could act on.

`credit_rollup_by_line` is in the list because the survey flags any mart
holding a revenue and a margin column together. Its columns are credit amounts
applied against cost rather than vendor cost, and no arithmetic over them
returns vendor cost, so the restricted quantity is absent. The rebate figures
are commercially sensitive, which is why a regional role is refused the table
rather than given it unfiltered.

## Two enforcements, and where they disagree

### BigQuery, written and unapplied

`infra/access.tf` reads `infra/access-rules.json`, which
`scripts/emit_access_rules.py` generates from the rule list. The Terraform
loops over it and names no role and no region, which
`check_the_terraform_loops_over_the_generated_rules_rather_than_naming_a_role`
asserts.

What it declares:

- a taxonomy with `FINE_GRAINED_ACCESS_CONTROL` turned on
- one policy tag for the restricted quantity
- a masking policy that nullifies that tag
- fine grained reader granted only to the roles that may read cost
- 14 row access policies
- `dataViewer` on the marts dataset for every role, and job user at the project

`terraform validate` passes against hashicorp/google 6.50.0. `terraform fmt
-check` is clean. No statement in that directory has been applied and no policy
tag exists anywhere.

The column tags live on the dbt side, in `transform/models/marts/_models.yml`,
because a policy tag on BigQuery is a column property and dbt owns the columns.
Eleven columns carry one. `persist_docs` for columns is on, because without it
dbt records the tag in the manifest and never sends it, which parses and does
nothing.

### DuckDB, which runs

`scripts/access_probe.py` builds one schema per role holding one view per
granted relation, projecting only the readable columns and filtering rows by
region. Every identifier goes through a quoting function that refuses anything
that is not a bare identifier.

The refusal is a real engine refusal. A select of a withheld column raises
`Binder Error: Referenced column "extended_cost_usd" not found in FROM clause!`
and a select of an ungranted relation raises a catalog error. Neither is a
string this repository checks for itself.

### The disagreement

BigQuery evaluates row access policies as a union of the policies the principal
is a grantee of, so once a table carries one policy a principal with no policy
on that table reads zero rows. A view with no where clause returns everything.

So the two spellings of one rule list diverge exactly on the role that reads
the whole book. `finance_analyst` needs an explicit true predicate on BigQuery
and no predicate at all in the view layer, and leaving the true predicate out
gives finance an empty margin table with nothing failing anywhere.
`check_the_bigquery_predicate_and_the_view_predicate_disagree_where_they_should`
asserts both halves. The local proof cannot cover this, because the engine that
behaves this way has never been connected to.

The masking differs too. The view layer drops the column, so a select of it
errors. A BigQuery masking policy returns null instead. Both refuse the number
and no caller should be written expecting either shape.

## What was proven, and on what

Measured by `scripts/access_probe.py` and recorded in
`measurements/access-enforcement.json`. Counts only.

| | |
| --- | --- |
| views built | 20 |
| answer key queries, all answered | 20 |
| column attacks, all refused | 19 |
| relation attacks, all refused | 28 |
| attacks the control role also refused | 0 |
| row scope checks matching exactly | 6 |

The answer key runs before the attacks. A rule that refuses a query it was
meant to allow gets the whole control switched off by whoever is on call, so
every role reads every relation it is granted first and a failure there stops
the probe.

Every refusal is retried against `finance_analyst`, which is withheld nothing.
A select of a column that does not exist raises the same binder error whether
the column was withheld on purpose or misspelled in the probe, and the control
is the only thing that separates those two. Zero of 47 attacks refused for the
control.

The four protections were each reverted in a scratch copy and the suite was
re-run. Dropping the layout pair from the rule list fails three checks.
Withholding only the column named cost fails two. Removing finance's true
predicate fails two. Pointing a regional role at the wrong region fails two.

## What this does not cover

**The view layer is advisory, because DuckDB has no roles.** There is no grant
to make and no principal to make it to. A connection that can see a role schema
can also see `main_marts` and read the cost column straight out of it, which
the probe measures rather than describes. The DuckDB side proves the rules
are expressible and that the predicates and projections are right. It does not
prove anybody is held to them.

**The same cost sits below the protected layer with nothing on it.**
`main_staging.stg_invoice_lines` carries `extended_cost_usd` and
`unit_cost_usd`, and `raw.invoice_lines` carries `unit_cost_usd`. That is 3
columns over 2 relations, counted by the probe rather than listed by hand. The
whole column control therefore rests on the reporting roles not holding those
two datasets, which is a dataset grant and not a tag. `infra/access.tf` grants every role `dataViewer` on marts
alone and a check fails if a grant on raw or staging ever appears. That closes
it in the written configuration and the written configuration has not been
applied.

**No BigQuery behaviour has been observed.** The row filter semantics, the
masking policy and the fine grained reader denial are read off the
documentation and the provider schema. `dbt parse` resolves the column tags
with no connection. That is the only reason the tag set can be graded at all.
`dbt compile --target bigquery` opens a connection even with `--no-introspect`,
so the compiled BigQuery SQL for these models has never existed.

**The marts survey needs a fresh build to fail.** The check that catches an
unprotected copy reads the committed measurement, so reverting the rule list
without re-running the probe leaves the manifest claiming protection. Three
other checks caught that revert, so the suite is not blind to it, and the
durable fix is running the probe in continuous integration rather than by hand.

**A role with two regions is refused rather than supported.** The predicate
builds an `in` list and would accept one, and nothing in the product asks for
it yet, so a check asserts every regional role carries exactly one region.
