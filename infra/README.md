# infra

Nothing here has been applied. There is no state file, no plan has ever run, and
the three variables that would identify a real project carry no default.

## What is missing

An OIDC trust between this repository and a billing enabled GCP project. Creating
it is a one time step by somebody holding owner on the project, and it is not
something an automated build should be doing. Until it exists, `terraform plan`
cannot resolve a project and `terraform apply` is not reachable.

Three values are needed and all three are non secret.

| variable | what it is |
|---|---|
| project_id | the project that holds the datasets, the bucket and the build identity |
| bucket_name | a globally unique name for the landing bucket |
| billing_account | the billing account the budget attaches to |

`terraform.tfvars` is gitignored, so filling them in does not commit them.

## What can be checked without any of that

`terraform validate` grades the schema and needs no credentials. It passes against
the google provider resolved from the constraint in `main.tf`.

`terraform fmt -check` passes.

Neither of them catches a configuration that is valid and wrong, which is most of
what can go wrong in here. `tests/test_infra.py` grades ten of those by reading
these files as text, and it runs on a machine with no terraform binary and no
network.

## What an apply would create

Enumerated because a directory nobody can apply hides what is behind it.

- One regional storage bucket with uniform access, no versioning, and a rule that
  deletes an object after thirty days.
- Three BigQuery datasets in one location. Raw and staging expire their tables
  after ninety days and hold the minimum time travel window. Marts expire nothing
  and keep the default week of time travel.
- One service account. It reads the bucket, writes tables inside the three
  datasets, and may run query jobs in the project. It cannot delete a dataset and
  it cannot change who else reads one.
- A workload identity pool and a provider that trusts only this repository.
- A monthly billing budget with alerts at half, all, and twice the amount.

Storage on a corpus this size is cents a month. The cost that matters on BigQuery
is a query scanning a table nobody clustered, on a schedule, which is why the
budget carries a threshold above the budget rather than only at it.

The teardown lands with the transformation layer, when there is something an apply
has actually created. Writing it today would be writing a script nobody can run
against infrastructure nobody can create.
