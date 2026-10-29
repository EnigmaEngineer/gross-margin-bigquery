"""Prints the billing to ledger reconciliation and records it.

    python3 scripts/recon_report.py

Reads the built DuckDB database, so run scripts/run_transform.py first. This is
the close pack view of recon_billing_to_gl: one line per period and account, the
variance split into the part that is one document sitting in two periods and the
part somebody has to go and look at, and then the alert table underneath.

The split is the reason this report exists rather than a plain variance column. A
period total tells a controller that March is short. It does not tell them
whether March is short because an invoice is missing or because an invoice was
billed on the 31st, and those take very different amounts of somebody's week.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import manifest

RECON_MEASUREMENT = REPO_ROOT / "measurements" / "recon-by-period.json"


def report(db_path: Path) -> dict:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "select period, account, billing_cents, ledger_cents, variance_cents, "
            "period_shift_cents, break_cents, billing_documents, ledger_entries, "
            "inside_the_reopen_window "
            "from main_marts.recon_billing_to_gl order by period, account"
        ).fetchall()

        classes = dict(
            con.execute(
                "select match_class, count(*) from main_marts.fct_gl_posting "
                "group by 1 order by 1"
            ).fetchall()
        )

        # Signed totals and absolute totals answer different questions and the
        # pair is the point. The period shift sums to zero because moving a
        # document between two periods cannot create money. Its absolute total
        # is how much of the close a controller would be reading if nothing
        # separated the two terms.
        totals = con.execute(
            "select "
            "sum(variance_cents), sum(period_shift_cents), sum(break_cents), "
            "sum(abs(variance_cents)), sum(abs(period_shift_cents)), "
            "sum(abs(break_cents)), "
            "sum(case when variance_cents <> 0 then 1 else 0 end), "
            "sum(case when break_cents <> 0 then 1 else 0 end), "
            "count(*) "
            "from main_marts.recon_billing_to_gl"
        ).fetchone()

        terms = con.execute(
            "select sum(unposted_cents), sum(ledger_only_cents), "
            "sum(amount_difference_cents) from main_marts.recon_billing_to_gl"
        ).fetchone()

        alerts = con.execute(
            "select credit_id, credit_type, stale_period, posted_in_period, "
            "observable_lag_days, days_past_the_window, reopen_window_days, "
            "cast(round(amount_usd * 100) as bigint) "
            "from main_marts.alert_late_credit_arrival "
            "order by observable_lag_days desc, credit_id"
        ).fetchall()

        label_mismatch = con.execute(
            "select count(*) from main_marts.fct_gl_posting "
            "where gl_period_label is not null and gl_period_label <> gl_period"
        ).fetchone()[0]
    finally:
        con.close()

    gross = int(totals[3])
    return {
        "periods_and_accounts": int(totals[8]),
        "rows_with_a_variance": int(totals[6]),
        "rows_with_a_break": int(totals[7]),
        "signed_cents": {
            "variance": int(totals[0]),
            "period_shift": int(totals[1]),
            "break": int(totals[2]),
        },
        "absolute_cents": {
            "variance": gross,
            "period_shift": int(totals[4]),
            "break": int(totals[5]),
        },
        # The share a reader should take away, derived here rather than typed
        # into the README. It is the fraction of the gross period variance that
        # resolves without anybody touching it.
        "share_of_the_gross_variance_that_is_a_period_shift_pct": round(
            100.0 * int(totals[4]) / gross, 2
        )
        if gross
        else None,
        "break_terms_cents": {
            "unposted": int(terms[0]),
            "ledger_only": int(terms[1]),
            "amount_difference": int(terms[2]),
        },
        "documents_by_match_class": {name: int(count) for name, count in classes.items()},
        "ledger_entries_whose_own_period_label_disagrees_with_its_date": label_mismatch,
        "alerts": [
            {
                "credit_id": credit,
                "credit_type": kind,
                "stale_period": stale,
                "posted_in_period": posted,
                "observable_lag_days": int(lag),
                "days_past_the_window": int(past),
                "reopen_window_days": int(window),
                "amount_cents": int(amount),
            }
            for credit, kind, stale, posted, lag, past, window, amount in alerts
        ],
        "by_period": [
            {
                "period": period,
                "account": account,
                "billing_cents": int(billing),
                "ledger_cents": int(ledger),
                "variance_cents": int(variance),
                "period_shift_cents": int(shift),
                "break_cents": int(break_cents),
                "billing_documents": int(documents),
                "ledger_entries": int(entries),
                "inside_the_reopen_window": bool(inside),
            }
            for period, account, billing, ledger, variance, shift, break_cents,
                documents, entries, inside in rows
        ],
        "rebuilt_by": "python3 scripts/recon_report.py",
    }


def usd(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(REPO_ROOT / "build" / "gross_margin.duckdb"))
    parser.add_argument(
        "--full", action="store_true", help="print every period rather than the breaks"
    )
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    if not db_path.exists():
        print(
            f"{db_path} is not there. Run scripts/run_transform.py first",
            file=sys.stderr,
        )
        return 2

    result = report(db_path)
    manifest.write(RECON_MEASUREMENT, result)

    print(
        f"{result['periods_and_accounts']} period and account rows, "
        f"{result['rows_with_a_variance']} carry a variance and "
        f"{result['rows_with_a_break']} carry a break"
    )
    absolute = result["absolute_cents"]
    print(
        f"gross variance {usd(absolute['variance'])}, of which "
        f"{usd(absolute['period_shift'])} is one document in two periods and "
        f"{usd(absolute['break'])} is a break"
    )
    print(
        f"{result['share_of_the_gross_variance_that_is_a_period_shift_pct']} percent "
        "of the gross variance resolves with nobody touching it"
    )
    signed = result["signed_cents"]
    print(
        f"signed: variance {usd(signed['variance'])}, period shift "
        f"{usd(signed['period_shift'])}, break {usd(signed['break'])}"
    )
    terms = result["break_terms_cents"]
    print(
        f"the break is {usd(terms['unposted'])} billing raised and the ledger never "
        f"received, {usd(terms['ledger_only'])} posted with no document behind it, "
        f"and {usd(terms['amount_difference'])} keyed at a different amount"
    )

    shown = [
        row
        for row in result["by_period"]
        if args.full or row["break_cents"] != 0
    ]
    print()
    print(f"{'period':>8}  {'account':20s}  {'variance':>14}  {'shift':>14}  {'break':>12}")
    for row in shown:
        print(
            f"{row['period']:>8}  {row['account']:20s}  "
            f"{usd(row['variance_cents']):>14}  {usd(row['period_shift_cents']):>14}  "
            f"{usd(row['break_cents']):>12}"
        )
    if not args.full:
        print("rows with no break are hidden, pass --full for all of them")

    print()
    if result["alerts"]:
        window = result["alerts"][0]["reopen_window_days"]
        print(f"{len(result['alerts'])} credit arrived past the {window} day reopen window")
        for alert in result["alerts"]:
            print(
                f"  {alert['credit_id']}  {alert['credit_type']:18s} "
                f"{alert['observable_lag_days']:>4} days, "
                f"{alert['days_past_the_window']:>3} past the window, "
                f"{usd(alert['amount_cents']):>10} against {alert['stale_period']}"
            )
    else:
        print("no credit arrived past the reopen window in this corpus")
    print(f"wrote {RECON_MEASUREMENT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
