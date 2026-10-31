# Bytes scanned, and why there is no number

Moved out of the README unchanged, because it is a long section a reader
does not need in order to run anything. The limitation itself stays in the
README. A reader who never opens `docs/` still has to learn that the layout
comparison has no result.

## Bytes scanned, and why there is no number here

The plan for this layer asked for bytes scanned on three finance queries, with
the clustered table against the unclustered one. **That measurement does not
exist and nothing in this file estimates it.**

BigQuery reports the bytes a query would scan through a dry run, which costs
nothing and needs a billing enabled project. This repository has no credential
path to one. `scripts/scan_probe.py` is the measurement, it names every blocker
and it exits 2 rather than printing anything.

A DuckDB row count is not a substitute. Neither is a file size. They are
different quantities on a different engine and publishing one under the name of
the other is the only way this repository could lie.

What is measurable today is narrower and it is real. `dbt parse` never
opens a connection, so the BigQuery branch of the layout macro resolves with no
credentials. Parsed against the bigquery target, the clustered half comes back
with monthly partitions on `invoice_date` and clustering on `vendor_id` then
`partner_id` then `product_line`. Parsed against duckdb, both halves come back
with neither. The adapter dispatch works. What the layout costs is unknown, and
`dbt compile --target bigquery` cannot help because it opens a connection and
fails to authenticate, with and without `--no-introspect`.

The three queries are executed against DuckDB on every build, once per half of
the pair. They return 3 and
41 and 144 rows and the
two halves agree on all three. That proves the SQL parses and returns something,
which is not the same as measuring it, and the first version of query one failed
exactly there. A macro call inside a line comment renders like any other, the
relation it produced ran to three lines, and two of them escaped the comment.
Compile exited zero on it.

The key order is an argument rather than a result. Clustering prunes on a prefix,
and the first query restricts to one vendor while the second groups by partner
across a month, so vendor first should help query one most and query two least.
Query three reads the whole history and may gain nothing. The probe is what would
settle those three predictions.
