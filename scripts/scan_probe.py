"""Measures bytes scanned for the three finance queries, on each half of the pair.

THIS CANNOT RUN YET. It needs a billing enabled GCP project and the OIDC trust
between this repository and that project, which is the one thing nobody here
holds. It exits 2 with the reason rather than printing a number.

TODO: no GCP project, no dataset and no workload identity pool exist for this
repository, so there is nothing to dry run against. The three values this needs
are the same three carrying no default in infra/variables.tf, and infra/README.md
says what creating the trust involves. When they exist, the run is:

    export GMB_GCP_PROJECT=...
    export GMB_BQ_DATASET=...
    python3 scripts/run_transform.py            build the pair on BigQuery
    python3 scripts/scan_probe.py               dry run both halves

Why a dry run and not a real one. BigQuery reports the bytes a query would scan
before charging for it, which is the quantity the clustered and unclustered
halves differ on, and it costs nothing. A real query would add slot time and
cache effects to a number that is supposed to be about physical layout.

Why `bq` and not a python client. The dry run is three flags on a command line
and adding a cloud SDK to this repository's dependencies for one script that
cannot run is worse than shelling out to a tool the operator already has
authenticated.

What this file is NOT allowed to do, and the reason is in the project rules. It
does not estimate. It does not substitute a DuckDB row count or a parquet file
size for bytes scanned, because those are different quantities measured on a
different engine. Until it runs there is no figure, and the README says so.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSFORM = REPO_ROOT / "transform"
COMPILED = TRANSFORM / "target" / "compiled" / "gross_margin" / "analyses"

PAIR = ("scan_margin_lines_unclustered", "scan_margin_lines_clustered")
QUERIES = (
    "finance_q1_vendor_quarter",
    "finance_q2_partner_period",
    "finance_q3_margin_trend",
)

REQUIRED_ENV = ("GMB_GCP_PROJECT", "GMB_BQ_DATASET")


def blockers() -> list[str]:
    """Everything standing between this file and a number."""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    reasons = [f"{name} is not set" for name in missing]
    if shutil.which("bq") is None:
        reasons.append("the bq command is not on PATH")
    return reasons


def compiled_sql(query: str, model: str) -> Path:
    """Where dbt leaves the rendered query for one half of the pair.

    dbt compiles an analysis once per invocation, so the probe compiles the
    project with `--vars scan_model: <half>` and reads the result before
    compiling the other half. The path is the same both times.
    """
    return COMPILED / f"{query}.sql"


def compile_for(model: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DBT_PROFILES_DIR"] = str(TRANSFORM / "profiles")
    return subprocess.run(
        [
            "dbt", "compile",
            "--target", "bigquery",
            "--vars", json.dumps({"scan_model": model}),
            "--no-partial-parse",
        ],
        cwd=TRANSFORM,
        env=env,
        capture_output=True,
        text=True,
    )


def dry_run(sql: str, project: str) -> int:
    """Bytes this query would scan. Raises if bq does not answer with a number."""
    result = subprocess.run(
        [
            "bq", "--project_id", project, "--format", "json",
            "query", "--dry_run", "--use_legacy_sql=false", sql,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"bq refused the dry run: {result.stderr.strip()[:400]}")
    payload = json.loads(result.stdout)
    bytes_scanned = payload.get("statistics", {}).get("query", {}).get(
        "totalBytesProcessed"
    )
    if bytes_scanned is None:
        raise SystemExit(
            "bq answered without totalBytesProcessed, so there is no figure here"
        )
    return int(bytes_scanned)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "measurements" / "bytes-scanned.json"),
        help="where the measurement would be written",
    )
    args = parser.parse_args(argv)

    stop = blockers()
    if stop:
        print("this probe cannot run and no figure is being written")
        for reason in stop:
            print(f"  {reason}")
        print("the access this waits on is described in infra/README.md")
        return 2

    project = os.environ["GMB_GCP_PROJECT"]
    measured: dict[str, dict[str, int]] = {}
    for model in PAIR:
        compiled = compile_for(model)
        if compiled.returncode != 0:
            sys.stderr.write(compiled.stdout[-4000:])
            return compiled.returncode
        measured[model] = {}
        for query in QUERIES:
            path = compiled_sql(query, model)
            measured[model][query] = dry_run(path.read_text(encoding="utf-8"), project)

    from gross_margin import manifest

    manifest.write(
        Path(args.out),
        {
            "bytes_scanned": measured,
            "queries": list(QUERIES),
            "pair": list(PAIR),
            "rebuilt_by": "python3 scripts/scan_probe.py",
        },
    )
    for query in QUERIES:
        unclustered = measured[PAIR[0]][query]
        clustered = measured[PAIR[1]][query]
        print(f"{query:28s} {unclustered:>14d} -> {clustered:>14d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
