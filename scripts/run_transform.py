"""Builds the transformation layer on DuckDB and records what it produced.

Three steps and the third is the one worth explaining.

    python3 scripts/run_transform.py

It loads the generated corpus into DuckDB, runs `dbt build` against the duckdb
target, then parses the project a second time against the bigquery target and
records the physical layout each target resolved.

That second parse is the only BigQuery grading available without credentials.
`dbt compile --target bigquery` opens a connection and fails, with and without
`--no-introspect`. `dbt parse` does not connect, so the manifest it writes still
carries the layout the bigquery branch of macros/scan_layout.sql produced. That
answers whether the adapter dispatch works. It does not answer what BigQuery
would charge for a query, and nothing in this repository can answer that yet.

The parse against bigquery needs a project name to render the profile. It is
given a string that is not a project, because a parse never contacts the API. A
real run supplies the real identifiers through the environment and the profile
has no default for either, so a misconfigured run fails by name.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import manifest
from scripts.load_duckdb import load

TRANSFORM = REPO_ROOT / "transform"
SHAPE_MEASUREMENT = REPO_ROOT / "measurements" / "transform-shape.json"

# A parse renders the profile and never opens a connection, so this string is
# never sent anywhere. It is spelled to be obvious in a log.
PARSE_ONLY_PROJECT = "parse-only-no-such-project"

QUERIES = (
    "finance_q1_vendor_quarter",
    "finance_q2_partner_period",
    "finance_q3_margin_trend",
)

PAIR = ("scan_margin_lines_unclustered", "scan_margin_lines_clustered")

MART_TABLES = (
    "main_marts.dim_product_version",
    "main_marts.fct_credit_allocation",
    "main_marts.credit_rollup_by_line",
    "main_marts.fct_invoice_line_margin",
    "main_marts.fct_credit_memo",
    "main_marts.fct_gl_posting",
    "main_marts.recon_billing_to_gl",
    "main_marts.alert_late_credit_arrival",
    "main_marts.margin_by_vendor",
    "main_marts.margin_by_partner",
    "main_marts.margin_by_product_line",
    "main_marts.scan_margin_lines_unclustered",
    "main_marts.scan_margin_lines_clustered",
)


def dbt(args: list[str], db_path: Path, target: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DBT_PROFILES_DIR"] = str(TRANSFORM / "profiles")
    env["GMB_DUCKDB_PATH"] = str(db_path)
    env.setdefault("GMB_GCP_PROJECT", PARSE_ONLY_PROJECT)
    env.setdefault("GMB_BQ_DATASET", "gross_margin")
    return subprocess.run(
        ["dbt", *args, "--target", target, "--no-partial-parse"],
        cwd=TRANSFORM,
        env=env,
        capture_output=True,
        text=True,
    )


def build_result(stdout: str) -> dict:
    """The counts off dbt's own summary line, found by name rather than position.

    This used to print the last line of stdout and call it the result. It read
    correctly for as long as the summary happened to be last, and then a
    deprecation notice moved in behind it and the run started reporting the word
    "parsing." as its outcome. An exit status of zero was the only thing
    actually being checked.
    """
    for line in stdout.splitlines():
        if "Done." in line and "PASS=" in line:
            counts = {}
            for token in line.split("Done.", 1)[1].split():
                if "=" in token:
                    key, _, value = token.partition("=")
                    if value.isdigit():
                        counts[key.lower()] = int(value)
            if "pass" in counts:
                return counts
    raise SystemExit(
        "dbt printed no summary line, so there is no result to report:\n"
        + stdout[-2000:]
    )


def node_counts(manifest_path: Path) -> dict:
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    models = [k for k in doc["nodes"] if doc["nodes"][k]["resource_type"] == "model"]
    tests = [k for k in doc["nodes"] if doc["nodes"][k]["resource_type"] == "test"]
    return {
        "models": len(models),
        "data_tests": len(tests),
        "sources": len(doc.get("sources", {})),
        "analyses": len([k for k in doc.get("nodes", {})
                         if doc["nodes"][k]["resource_type"] == "analysis"]),
    }


def scan_layout_from(manifest_path: Path) -> dict:
    """What each target resolved for the two halves of the scan pair."""
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    out = {}
    for key, node in doc["nodes"].items():
        name = node.get("name", "")
        if not name.startswith("scan_margin_lines"):
            continue
        if node["resource_type"] != "model":
            continue
        config = node["config"]
        out[name] = {
            "partition_by": config.get("partition_by"),
            "cluster_by": config.get("cluster_by"),
        }
    return out


def duckdb_shape(db_path: Path) -> dict:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = {
            table: con.execute(f"select count(*) from {table}").fetchone()[0]
            for table in MART_TABLES
        }
        # The identity the marts have to satisfy. Whole cents, because a
        # difference below one would be an artefact of the group by order.
        margin = {
            "line_grain_cents": int(round(
                con.execute(
                    "select sum(invoiced_margin_usd) * 100 "
                    "from main_marts.fct_invoice_line_margin"
                ).fetchone()[0]
            )),
        }
        for grain, table in (
            ("by_vendor", "main_marts.margin_by_vendor"),
            ("by_partner", "main_marts.margin_by_partner"),
            ("by_product_line", "main_marts.margin_by_product_line"),
        ):
            margin[f"{grain}_cents"] = int(round(
                con.execute(
                    f"select sum(invoiced_margin_usd) * 100 from {table}"
                ).fetchone()[0]
            ))
        kept = con.execute(
            "select count(*), sum(extended_revenue_usd) * 100, "
            "sum(invoiced_margin_usd) * 100 "
            "from main_marts.fct_invoice_line_margin where not has_partner"
        ).fetchone()
        credits = con.execute(
            "select count(*), "
            "sum(case when not is_placeable then 1 else 0 end), "
            "sum(case when is_later_posting_of_the_same_doc then 1 else 0 end), "
            "sum(case when exceeds_the_line_it_adjusts then 1 else 0 end) "
            "from main_marts.fct_credit_memo"
        ).fetchone()
        reassigned = con.execute(
            "select "
            "sum(case when product_was_reassigned then 1 else 0 end), "
            "sum(case when product_version_unresolved then 1 else 0 end) "
            "from main_marts.fct_invoice_line_margin"
        ).fetchone()
        scopes = con.execute(
            "select allocation_scope, count(*), count(distinct credit_id), "
            "sum(allocated_cents) "
            "from main_marts.fct_credit_allocation group by 1 order by 1"
        ).fetchall()
        # The identity that makes the table trustworthy. Every credit's parts
        # add back to the credit, in whole cents, with no tolerance.
        off_by = con.execute(
            "with parts as ("
            "  select credit_id, sum(allocated_cents) as cents "
            "  from main_marts.fct_credit_allocation group by 1), "
            "whole as ("
            "  select credit_id, cast(round(amount_usd * 100) as bigint) as cents "
            "  from main_marts.fct_credit_memo) "
            "select count(*) from parts join whole using (credit_id) "
            "where parts.cents <> whole.cents"
        ).fetchone()[0]
        stages = con.execute(
            "select "
            "cast(round(sum(invoiced_margin_usd) * 100) as bigint), "
            "cast(round(sum(margin_after_named_credits_usd) * 100) as bigint), "
            "cast(round(sum(margin_after_allocated_credits_usd) * 100) as bigint) "
            "from main_marts.margin_by_vendor"
        ).fetchone()
        # Integer comparisons on purpose. The dollar columns are exact now and
        # these questions are still about whether a figure moved at all, which
        # is the shape that broke when the column was a float.
        swing = con.execute(
            "select vendor_id, period, margin_moved_cents, "
            "cast(round(margin_after_named_credits_usd * 100) as bigint) "
            "from main_marts.margin_by_vendor "
            "order by abs(margin_moved_cents) desc, vendor_id, period limit 1"
        ).fetchone()
        moved = con.execute(
            "select count(*), coalesce(sum(abs(margin_moved_cents)), 0) "
            "from main_marts.margin_by_vendor where margin_moved_cents <> 0"
        ).fetchone()
        # The reconciliation, summarised. The absolute total beside the signed
        # one is deliberate. The signed period shift is zero because moving a
        # document between two periods cannot create money, and a reader who
        # saw only that would conclude there was no timing variance at all.
        recon = con.execute(
            "select count(*), "
            "sum(case when variance_cents <> 0 then 1 else 0 end), "
            "sum(case when break_cents <> 0 then 1 else 0 end), "
            "sum(variance_cents), sum(period_shift_cents), sum(break_cents), "
            "sum(abs(variance_cents)), sum(abs(period_shift_cents)), "
            "sum(abs(break_cents)) "
            "from main_marts.recon_billing_to_gl"
        ).fetchone()
        match_classes = con.execute(
            "select match_class, count(*) from main_marts.fct_gl_posting "
            "group by 1 order by 1"
        ).fetchall()
        alerts = con.execute(
            "select count(*), coalesce(max(observable_lag_days), 0), "
            "coalesce(min(days_past_the_window), 0) "
            "from main_marts.alert_late_credit_arrival"
        ).fetchone()
        # Asks for the floats by name rather than listing the exact types that
        # are allowed. An allow list would flag a new decimal width as a defect
        # and the thing being guarded against is a float.
        types = con.execute(
            "select column_name, data_type from information_schema.columns "
            "where table_schema = 'main_marts' "
            "and (column_name like '%usd' or column_name like '%cents') "
            "and data_type in ('DOUBLE', 'FLOAT', 'REAL') "
            "group by 1, 2 order by 1"
        ).fetchall()
    finally:
        con.close()

    return {
        "rows": rows,
        "margin": margin,
        "rows_an_inner_join_would_drop": {
            "lines_with_no_partner": kept[0],
            "their_revenue_cents": int(round(kept[1])),
            "their_margin_cents": int(round(kept[2])),
        },
        "credits": {
            "total": credits[0],
            "unplaceable": credits[1],
            "later_posting_of_a_duplicated_document": credits[2],
            "exceeding_the_line_they_adjust": credits[3],
        },
        "reconciliation": {
            "periods_and_accounts": recon[0],
            "rows_with_a_variance": recon[1],
            "rows_with_a_break": recon[2],
            "signed_cents": {
                "variance": int(recon[3]),
                "period_shift": int(recon[4]),
                "break": int(recon[5]),
            },
            "absolute_cents": {
                "variance": int(recon[6]),
                "period_shift": int(recon[7]),
                "break": int(recon[8]),
            },
            "documents_by_match_class": {
                name: count for name, count in match_classes
            },
            "late_arrival_alerts": {
                "credits": alerts[0],
                "largest_observable_lag_days": int(alerts[1]),
                "smallest_margin_past_the_window_days": int(alerts[2]),
            },
        },
        "as_of_join": {
            "lines_on_a_reassigned_product": reassigned[0],
            "lines_with_no_product_version_in_force": reassigned[1],
        },
        "allocation": {
            "by_scope": {
                scope: {
                    "rows": rows,
                    "credits": credits,
                    "cents": int(cents),
                }
                for scope, rows, credits, cents in scopes
            },
            "credits_whose_parts_do_not_sum_to_the_credit": off_by,
            "margin_cents": {
                "invoiced": int(stages[0]),
                "after_named_credits": int(stages[1]),
                "after_allocated_credits": int(stages[2]),
            },
            "vendor_periods_that_moved": moved[0],
            "absolute_margin_moved_cents": int(moved[1] or 0),
            "money_columns_that_are_not_exact": [
                {"column": name, "type": kind} for name, kind in types
            ],
            "largest_single_move": {
                "vendor_id": swing[0],
                "period": swing[1],
                "cents": int(swing[2]),
                "that_rows_named_margin_cents": int(swing[3]),
            },
        },
    }


def run_the_finance_queries(db_path: Path) -> dict:
    """Compile each finance query for both halves of the pair and execute it.

    This exists because of a defect it caught on the day it was written. A jinja
    call sitting inside a SQL line comment renders like any other, and the
    relation it produced spanned three lines, so two of them escaped the comment
    and the query no longer parsed. `dbt compile` exited zero on it and so did
    `dbt parse`, because an analysis is rendered and never run.

    So the queries get executed against DuckDB on every build. That does not make
    them BigQuery queries and it does not measure anything about layout. It
    proves they parse and return rows, which is the part that was silently
    untrue.

    Every compile happens before the connection opens. DuckDB allows one writer,
    and dbt opens the database read write even to compile, so a read only handle
    held across a compile blocks it with a lock error.
    """
    import duckdb

    rendered: dict[str, dict[str, str]] = {}
    for half in PAIR:
        compiled = dbt(
            ["compile", "--vars", json.dumps({"scan_model": half})], db_path, "duckdb"
        )
        if compiled.returncode != 0:
            raise SystemExit(f"dbt compile failed for {half}:\n{compiled.stdout[-2000:]}")
        rendered[half] = {}
        for query in QUERIES:
            path = (TRANSFORM / "target" / "compiled" / "gross_margin"
                    / "analyses" / f"{query}.sql")
            sql = path.read_text(encoding="utf-8")
            if half not in sql:
                raise SystemExit(
                    f"{query} compiled without naming {half}, so the var did not "
                    "reach it and both halves would be measured on one table"
                )
            rendered[half][query] = sql

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return {
            half: {query: len(con.execute(sql).fetchall())
                   for query, sql in queries.items()}
            for half, queries in rendered.items()
        }
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(REPO_ROOT / "data"))
    parser.add_argument("--db", default=str(REPO_ROOT / "build" / "gross_margin.duckdb"))
    parser.add_argument(
        "--skip-load",
        action="store_true",
        help="use the database as it is, for a rebuild of the models alone",
    )
    args = parser.parse_args(argv)
    db_path = Path(args.db)

    if not args.skip_load:
        counts = load(Path(args.corpus), db_path)
        print(f"loaded {len(counts)} raw tables, {sum(counts.values())} rows")

    built = dbt(["build"], db_path, "duckdb")
    if built.returncode != 0:
        sys.stderr.write(built.stdout[-4000:])
        return built.returncode
    result = build_result(built.stdout)
    summary = "  ".join(f"{k}={v}" for k, v in sorted(result.items()))
    print(f"dbt build --target duckdb exit {built.returncode}: {summary}")
    if result.get("error") or result.get("warn"):
        sys.stderr.write(built.stdout[-4000:])
        return 1

    # Both parses write the same manifest path, so the duckdb layout has to be
    # read before the bigquery parse runs or it reads back the other target's
    # answer and the comparison says the two agree.
    manifest_path = TRANSFORM / "target" / "manifest.json"
    shape = duckdb_shape(db_path)
    shape["nodes"] = node_counts(manifest_path)
    shape["layout_resolved_by_target"] = {"duckdb": scan_layout_from(manifest_path)}

    parsed = dbt(["parse"], db_path, "bigquery")
    print(f"dbt parse --target bigquery exit {parsed.returncode}")
    if parsed.returncode != 0:
        sys.stderr.write(parsed.stdout[-4000:])
        return parsed.returncode
    shape["layout_resolved_by_target"]["bigquery"] = scan_layout_from(manifest_path)

    shape["finance_queries"] = run_the_finance_queries(db_path)
    shape["bytes_scanned"] = {
        "measured": False,
        "why": (
            "a dry run needs a billing enabled project and this repository has "
            "no credential path to one. See scripts/scan_probe.py"
        ),
    }
    shape["dbt_build"] = result
    shape["rebuilt_by"] = "python3 scripts/run_transform.py"
    manifest.write(SHAPE_MEASUREMENT, shape)
    print(f"wrote {SHAPE_MEASUREMENT.relative_to(REPO_ROOT)}")

    nodes = shape["nodes"]
    print(f"{nodes['models']} models and {nodes['data_tests']} data tests")
    margin = shape["margin"]
    agree = len({v for k, v in margin.items() if k.endswith("_cents")}) == 1
    print(f"margin agrees across all four grains: {agree}")
    queries = shape["finance_queries"]
    for half, results in queries.items():
        print(f"{half:32s} " + "  ".join(f"{q.split('_')[1]}={n}" for q, n in results.items()))
    print("bytes scanned: not measured, see the README limitations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
