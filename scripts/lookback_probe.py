"""Measures what a lookback window would miss, on each of the two anchors.

    python3 scripts/lookback_probe.py

The allocation incremental reprocesses credits inside a window. There are two
dates it could measure that window against and they are not interchangeable.

    posted_date   when the credit arrived
    invoice_date  when the line it adjusts was billed

A window of thirty days means two different things under those two readings, and
this prints how different. It reads the built DuckDB database, so it describes
this corpus and nothing else. Run scripts/run_transform.py first.

What it does not do is choose a number. The reconciliation work is where a
window becomes an alert threshold, and a threshold picked from one corpus at one
seed is a property of that seed. Two of the figures here already carry that
warning in the repository's limitations.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import manifest

PROBE_MEASUREMENT = REPO_ROOT / "measurements" / "lookback-anchors.json"

# Round numbers a person would propose in a planning meeting, plus the three the
# credit lag measurement produced. Nothing here is a recommendation.
WINDOWS = (7, 30, 60, 90, 105, 124, 180, 365)


def probe(db_path: Path) -> dict:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        placed = con.execute(
            "select count(*) from main_marts.fct_credit_memo where is_placeable"
        ).fetchone()[0]

        # Arrival order is posted_date order in this corpus, so a credit is
        # never older than the high water mark when it lands and the posted
        # anchor misses nothing at any window. That zero is a property of the
        # generator and not a result. It is computed rather than asserted so
        # that it stops being zero the day a backdated posting is generated.
        out_of_order = con.execute(
            """
            with arrivals as (
                select
                    credit_id,
                    posted_date,
                    max(posted_date) over (
                        order by posted_date, credit_id
                        rows between unbounded preceding and 1 preceding
                    ) as high_water
                from main_marts.fct_credit_memo
            )
            select count(*) from arrivals
            where high_water is not null and posted_date < high_water
            """
        ).fetchone()[0]

        by_window = []
        for window in WINDOWS:
            row = con.execute(
                """
                select
                    count(*),
                    coalesce(sum(credit.amount_usd), 0),
                    count(distinct credit.vendor_id || '|' || credit.credit_type)
                from main_marts.fct_credit_memo as credit
                where credit.is_placeable
                  and credit.observable_lag_days > ?
                """,
                [window],
            ).fetchone()

            # A missed rebate does not only lose its own amount. Every line it
            # would have been spread over keeps a share it should not have, so
            # the wrong rows are the whole scope and not the one credit.
            rows_wrong = con.execute(
                """
                select coalesce(count(*), 0)
                from main_marts.fct_credit_allocation as allocation
                join main_marts.fct_credit_memo as credit
                  on allocation.credit_id = credit.credit_id
                where credit.is_placeable
                  and credit.observable_lag_days > ?
                """,
                [window],
            ).fetchone()[0]

            by_window.append({
                "window_days": window,
                "invoice_date_anchor": {
                    "credits_missed": row[0],
                    "amount_missed_cents": int(round(row[1] * 100)),
                    "allocation_rows_left_wrong": rows_wrong,
                },
                "posted_date_anchor": {
                    "credits_missed": out_of_order,
                    "why": "no credit in this corpus is posted before one already loaded",
                },
            })

        lags = con.execute(
            """
            select
                min(observable_lag_days),
                max(observable_lag_days),
                cast(round(median(observable_lag_days)) as bigint),
                cast(round(quantile_cont(observable_lag_days, 0.95)) as bigint)
            from main_marts.fct_credit_memo
            where is_placeable
            """
        ).fetchone()
    finally:
        con.close()

    return {
        "placeable_credits": placed,
        "invoice_to_posting_lag_days": {
            "min": lags[0],
            "median": int(lags[2]),
            "p95": int(lags[3]),
            "max": lags[1],
        },
        "credits_posted_out_of_arrival_order": out_of_order,
        "by_window": by_window,
        "rebuilt_by": "python3 scripts/lookback_probe.py",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=str(REPO_ROOT / "build" / "gross_margin.duckdb")
    )
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    if not db_path.exists():
        print(
            f"{db_path} is not there. Run scripts/run_transform.py first",
            file=sys.stderr,
        )
        return 2

    result = probe(db_path)
    manifest.write(PROBE_MEASUREMENT, result)

    lag = result["invoice_to_posting_lag_days"]
    print(
        f"invoice to posting lag: median {lag['median']}, "
        f"p95 {lag['p95']}, max {lag['max']} days"
    )
    print(f"{'window':>8}  {'invoice anchor misses':>22}  {'rows left wrong':>16}"
          f"  {'posted anchor misses':>21}")
    for entry in result["by_window"]:
        invoice = entry["invoice_date_anchor"]
        print(
            f"{entry['window_days']:>8}  {invoice['credits_missed']:>22}"
            f"  {invoice['allocation_rows_left_wrong']:>16}"
            f"  {entry['posted_date_anchor']['credits_missed']:>21}"
        )
    print(
        "the posted anchor column is zero by construction in this corpus and "
        "says nothing about a real feed"
    )
    print(f"wrote {PROBE_MEASUREMENT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
