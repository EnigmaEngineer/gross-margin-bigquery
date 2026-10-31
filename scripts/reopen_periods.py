"""Recomputes the reconciliation for the periods still inside the window.

    python3 scripts/reopen_periods.py

This is the job `recon_reopen_days` was declared for. The number sat in
`dbt_project.yml` and `recon_billing_to_gl` published
`inside_the_reopen_window` on every row. No process read either one. So the
word window described behaviour the repository did not have.

Three steps and the third is the one that makes it worth running.

It reads the reconciliation out of DuckDB and asks `gross_margin.reopen` which
periods can still move. All of that decision is in the library, where the suite
grades it. This file drives and prints.

It recompiles the reconciliation through dbt and executes it scoped to those
periods. The scope is a predicate on the compiled SQL rather than a change to
the model, which matters: a model that filtered itself to the reopen set would
drop every closed period from the published table and every figure derived from
it would move.

It compares the scoped answer against the rows the full build wrote for the
same periods, cent for cent. On a corpus with no new data the two have to be
identical, and anything else means the scoped path and the full path disagree.
That is the failure worth having a job for, because a reprocessing path that
quietly answers differently from a full rebuild is how a close ends up
depending on which route ran last.

What it does not do is write anything back. A real deployment would materialise
the scoped answer over the reopened partitions and this one prints the
comparison, because partition replacement on DuckDB would be a different
mechanism from the one BigQuery uses and nothing here has run against BigQuery.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import manifest, reopen

TRANSFORM = REPO_ROOT / "transform"
MEASUREMENT = REPO_ROOT / "measurements" / "reopen-plan.json"
RECON_TABLE = "main_marts.recon_billing_to_gl"
PARSE_ONLY_PROJECT = "parse-only-no-such-project"

# The columns the plan reads. Named rather than selected with a star so that a
# column the reconciliation renames fails here instead of arriving as a null
# that the plan reads as settled.
PLAN_COLUMNS = (
    "period",
    "account",
    "inside_the_reopen_window",
    "unposted_cents",
    "awaiting_the_ledger_cents",
    "amount_difference_cents",
    "ledger_only_cents",
    "from_another_period_cents",
)

# What the comparison checks on every reopened period. The two sides plus the
# three derived terms. Dollar columns are left out on purpose, because they are
# a function of the cents and comparing both would report one disagreement
# twice.
COMPARE_COLUMNS = (
    "billing_cents",
    "ledger_cents",
    "billing_documents",
    "ledger_entries",
    "variance_cents",
    "period_shift_cents",
    "break_cents",
)


def declared_window_days() -> int:
    text = (TRANSFORM / "dbt_project.yml").read_text(encoding="utf-8")
    found = re.search(r"^\s*recon_reopen_days:\s*(\d+)\s*$", text, re.M)
    if found is None:
        raise SystemExit("dbt_project.yml does not declare recon_reopen_days")
    return int(found.group(1))


def compile_recon(db_path: Path) -> str:
    """The compiled reconciliation SQL, straight out of dbt's target directory.

    Compiled rather than hand written. A copy of the model's SQL in this file
    would be a second implementation of the thing the job exists to check, and
    it would agree with itself forever.
    """
    env = dict(os.environ)
    env["DBT_PROFILES_DIR"] = str(TRANSFORM / "profiles")
    env["GMB_DUCKDB_PATH"] = str(db_path)
    env.setdefault("GMB_GCP_PROJECT", PARSE_ONLY_PROJECT)
    env.setdefault("GMB_BQ_DATASET", "gross_margin")
    done = subprocess.run(
        ["dbt", "compile", "--select", "recon_billing_to_gl",
         "--target", "duckdb", "--no-partial-parse"],
        cwd=TRANSFORM, env=env, capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise SystemExit(
            "dbt compile failed for recon_billing_to_gl:\n" + done.stdout[-2000:]
        )
    path = (TRANSFORM / "target" / "compiled" / "gross_margin" / "models"
            / "marts" / "recon_billing_to_gl.sql")
    sql = path.read_text(encoding="utf-8")
    if "inside_the_reopen_window" not in sql:
        raise SystemExit(
            "the compiled reconciliation does not carry inside_the_reopen_window, "
            "so the compile reached a different model than the one being scoped"
        )
    return sql


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=str(REPO_ROOT / "build" / "gross_margin.duckdb")
    )
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(
            f"{db_path} does not exist. Run scripts/run_transform.py first, which "
            "is what the DAG does."
        )

    import duckdb

    window_days = declared_window_days()
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        columns = ", ".join(PLAN_COLUMNS)
        raw = con.execute(f"select {columns} from {RECON_TABLE}").fetchall()
    finally:
        con.close()
    rows = [dict(zip(PLAN_COLUMNS, row)) for row in raw]

    plan = reopen.plan(rows)
    print(
        f"window {window_days} days: {plan.periods_in_the_window} periods in reach, "
        f"{plan.periods_outside_the_window} closed"
    )
    print(
        f"reopening {len(plan.reopen)} and leaving "
        f"{plan.settled_inside_the_window} inside the window alone"
    )
    for entry in plan.reopen:
        print(
            f"  {entry.period}  {entry.accounts} accounts  "
            f"{entry.unsettled_cents} unsettled cents  {', '.join(entry.reasons)}"
        )

    # The scoped recomputation. The predicate is built by the library, which is
    # also what makes the empty plan case gradeable.
    sql = compile_recon(db_path)
    predicate = reopen.scope_predicate(plan.periods)
    scoped = (
        f"with full_recon as (\n{sql}\n)\n"
        f"select {', '.join(('period', 'account') + COMPARE_COLUMNS)} "
        f"from full_recon where {predicate}"
    )

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        recomputed = con.execute(scoped).fetchall()
        stored = con.execute(
            f"select {', '.join(('period', 'account') + COMPARE_COLUMNS)} "
            f"from {RECON_TABLE} where {predicate}"
        ).fetchall()
    finally:
        con.close()

    left = {(row[0], row[1]): tuple(int(v) for v in row[2:]) for row in recomputed}
    right = {(row[0], row[1]): tuple(int(v) for v in row[2:]) for row in stored}
    disagreements = sorted(
        {key for key in set(left) | set(right) if left.get(key) != right.get(key)}
    )

    print(
        f"recomputed {len(left)} period and account rows, compared against "
        f"{len(right)} stored, {len(disagreements)} disagree"
    )
    for key in disagreements[:10]:
        print(f"  {key}: recomputed {left.get(key)} stored {right.get(key)}")

    payload = plan.as_measurement()
    payload["declared_window_days"] = window_days
    payload["scope_predicate_when_the_plan_is_empty"] = reopen.scope_predicate([])
    payload["recomputation"] = {
        "rows_recomputed": len(left),
        "rows_stored_for_the_same_periods": len(right),
        "rows_that_disagree": len(disagreements),
        "columns_compared": list(COMPARE_COLUMNS),
    }
    payload["rows_a_full_rebuild_would_touch"] = len(rows)
    payload["rebuilt_by"] = "python3 scripts/reopen_periods.py"
    manifest.write(MEASUREMENT, payload)
    print(f"wrote {MEASUREMENT.relative_to(REPO_ROOT)}")

    if disagreements:
        print(
            "the scoped recomputation disagrees with the stored table, which "
            "means the reopen path and the full build do not answer the same "
            "question",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
