# What a daily run costs, and how to take it down

Two sections and they share one caveat. Nothing in `infra/` has ever been
applied. There is no project, no environment and no bill, so every figure below
is a published list price multiplied out against a declared shape. None of it
was observed.

That distinction is the whole reason this file exists rather than a sentence in
the README. A cost read off a price list and a cost read off an invoice are
different kinds of thing, and a reader who cannot tell which one they are
looking at has been given a number they cannot use.

## Where the figures come from

`infra/price-list.json` carries the rates, transcribed from the two Google Cloud
pricing pages it names, with the date they were read. `scripts/cost_model.py`
reads the shape out of `infra/composer.tf`, multiplies, and writes
`measurements/cost-model.json`. `tests/test_cost.py` re-derives every figure, so
a rate that changes in the price list moves the published number instead of
leaving it stale.

The rates were read with the region selector on Iowa, `us-central1`. The price
rows on those pages are not tagged per region in the page text, so that
attribution comes from the page layout rather than from the page saying so. It
is recorded in the price list file as an inference and not as a fact.

```
python3 scripts/cost_model.py
```

## The daily cost of the terms that have a quantity

The environment `infra/composer.tf` declares is `ENVIRONMENT_SIZE_SMALL` with
1.5 vCPU, 6 GiB of memory and 3 GiB of storage across a scheduler, a web server
and one worker.

| term | USD per day |
|---|---|
| environment fee | 8.4000 |
| vCPU | 1.6200 |
| memory | 0.7200 |
| storage | 0.0144 |
| **total** | **10.75** |

That is $322.63 over 30 days. The environment fee alone is 78.11 percent of it.

Every one of those four terms is a rate on a resource that exists rather than on
work it did. The scheduler keeps running between DAG runs. So does the web
server, and so does the floor worker. The fee is charged for the environment
itself. So the figure does not move when the pipeline fires more often, and it
does not fall to zero on a day the pipeline fires not at all.

## The terms with no quantity

Four, and the first one is the one that matters.

**Bytes scanned per run has never been measured.** The on-demand rate is $6.25
per TiB with the first TiB each month free. The quantity that rate multiplies
does not exist, because a byte count comes from a dry run and a dry run needs a
billing enabled project. `dbt compile --target bigquery` opens a connection even
with introspection off, so no statement from this repository has reached
BigQuery at all. `docs/bytes-scanned.md` has the longer version.

This is the awkward shape of the answer. The part of the pipeline that bills for
existing is priced, and the part that bills for doing the work is not. So the
daily figure above is not an estimate of what a run costs. It is what the
scheduler costs to keep, which happens to be the term this repository can
actually reach.

**Cloud SQL database storage** has a rate of $0.000232877 per GiB hour and the
size of the instance is a property of an environment that was never created.

**Worker scale out** is priced at the floor of one worker. The declared maximum
is 2 and the second worker bills on a day a backfill needs it.

**Network egress** is quoted as VPC network rates and there is no quantity.

The corpus is 2,957,980 bytes of CSV in the landing shape, which is far inside
the 10 GiB free storage tier on any reading. That bounds the storage term below
the free tier and is not the storage term, because BigQuery charges on logical
or physical bytes of the loaded tables and those are a different number.

## Teardown

Written down and never run, like everything else in `infra/`. A procedure that
has never executed is a procedure with unknown defects, and there is one visible
defect in it already.

### The one command does not work

`infra/composer.tf` declares `prevent_destroy = true` on the Composer
environment. The reason is good. An environment deleted by accident takes its
task history with it, and that history is the only record of which run produced
which published figure. The consequence is that `terraform destroy` fails on
that resource rather than skipping it, so the whole destroy stops with nothing
removed and the expensive resource still running.

That matters more than it looks. The cycle 3 cost rule is that no run ends with
billable resources running, and the resource this repository would most want to
remove is the one with a guard on it. A teardown that stops on its first
expensive resource is worse than no teardown, because it reports a failure after
removing some unknown prefix of the plan.

So the order is deliberate.

### The procedure

```
cd infra

# 1. See what exists before removing anything. A destroy plan read by a person
#    is the only check on this file that currently exists.
terraform plan -destroy

# 2. The Composer environment, which needs its guard removed first. Delete the
#    lifecycle block in composer.tf, then take this resource on its own so a
#    failure anywhere else cannot leave the scheduler up.
terraform destroy -target=google_composer_environment.orchestration

# 3. Confirm it is gone before continuing, because step 2 is the only step that
#    costs money by the day.
gcloud composer environments list --locations "$REGION"

# 4. Everything else.
terraform destroy
```

Step 1 is not ceremony. Nothing in this repository has ever produced a plan
against a real project, so the first person to run step 4 is also the first
person to find out whether the configuration destroys cleanly.

### What a teardown does not remove

`infra/bigquery.tf` sets `delete_contents_on_destroy = false` on the datasets, so
a destroy of a dataset that still holds tables fails rather than taking the
tables with it. That is the same shape as the Composer guard and it is
deliberate for the same reason. It also means step 4 has the same failure mode
as step 2 if the pipeline ever ran and left tables behind, and nothing above
handles that, because nothing has ever run.

Logs and the budget alert outlive the resources they watched. A budget is
attached to a billing account rather than to a project, so removing the project
does not remove the alert, and an alert on a project that no longer exists
never fires and never says why.

### What has not been verified

Everything in this section. No step above has run. `terraform validate` grades
the configuration and says nothing about whether a destroy succeeds, and the
difference between those two is the whole content of this file's uncertainty.
