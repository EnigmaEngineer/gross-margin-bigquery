"""Proves the billing to ledger reconciliation on a corpus small enough to check
by hand.

    python3 scripts/recon_fixture.py

Three invoices, three lines, two credits and seven ledger entries. The ledger is
written out as literals rather than posted by `gross_margin.ledger`, because the
point of the fixture is to control exactly which entry carries which defect. The
expected variance, timing and break for every period and account below were
worked out on paper and are written as literals too.

The corpus holds one of each break the reconciliation is supposed to separate.

    INV1 cost       the ledger holds a different amount, same period
    INV2            billed in the January close freeze, so the ledger posts it
                    in February, and ALSO keyed at the wrong amount
    INV3 revenue    billing raised it and the ledger never received it
    ORPHAN1         a ledger entry with no billing document behind it
    CM2             a credit arriving 230 days after its invoice, which is past
                    the reopen window and is what the alert is for

INV2 is the row that matters most and the reason this file exists. A document
that is both in the wrong period and at the wrong amount is the case that told
the two terms apart. The first version of the model moved the BILLING amount
between periods, which put that document's 40 dollar difference in the timing
column, where it reads as something that clears next month. The variance still
added up, so the decomposition test passed either way. No row in the generated
corpus carries both defects at once, so nothing there could have caught it.

Nothing here touches the real corpus or the real database.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import manifest
from gross_margin.registry import ARTIFACTS
from scripts.load_duckdb import load

TRANSFORM = REPO_ROOT / "transform"
FIXTURE_MEASUREMENT = REPO_ROOT / "measurements" / "recon-fixture.json"

VENDORS = [["V1", "Northwind Supply", "quarterly-volume"]]
PARTNERS = [["P1", "Harbour Systems", "west", "mid-market"]]
PLANS = [["P1", "PLAN-A", "2.5", "2023-01-01"]]
PRODUCTS = [
    ["PR1", "Alpha", "V1", "2023-01-01"],
    ["PR2", "Alpha", "V1", "2023-01-01"],
    ["PR3", "Alpha", "V1", "2023-01-01"],
]

# INV2 is dated the second to last business day of January 2025, which is inside
# the close freeze, so calendar.posting_date sends it to the second business day
# of February. That is 2025-02-04 and it is ordinary timing rather than a defect.
INVOICES = [
    ["INV1", "2025-01-15", "2025-01", "P1", "direct"],
    ["INV2", "2025-01-30", "2025-01", "P1", "direct"],
    ["INV3", "2025-02-10", "2025-02", "P1", "direct"],
]

# Round amounts. The allocation fixture is where awkward fractions belong.
LINES = [
    ["L1", "INV1", "PR1", "1", "1000.00", "800.00"],
    ["L2", "INV2", "PR2", "1", "500.00", "400.00"],
    ["L3", "INV3", "PR3", "1", "200.00", "150.00"],
]

CREDITS = [
    # Ordinary. Nineteen days after its invoice, well inside the window.
    ["CM1", "VD-0001", "INV1", "L1", "price_protection", "cost", "50.00",
     "2025-02-01", "2025-02-03", "2025-02"],
    # 230 days after its invoice, which is 80 days past the reopen window. The
    # period it makes stale is 2025-01 and the ledger puts it in 2025-09.
    ["CM2", "VD-0002", "INV1", "L1", "price_protection", "cost", "25.00",
     "2025-08-25", "2025-09-02", "2025-09"],
]

# The ledger, with four deliberate departures from what billing holds.
#
#   JE2  INV1 cost at 880.00 against billing's 800.00. Same period, so the
#        80 dollar difference is a break in 2025-01 cost.
#   JE4  INV2 cost at 440.00 against billing's 400.00, in February because of the
#        freeze. Wrong period AND wrong amount, which is the case this fixture
#        exists for.
#   --   INV3 revenue is absent. Billing raised 200.00 and the ledger has nothing.
#   JE8  A journal with no billing document behind it.
GL_ENTRIES = [
    ["JE1", "2025-01", "4000-revenue", "invoice", "INV1", "1000.00", "2025-01-15"],
    ["JE2", "2025-01", "5000-cost-of-goods", "invoice", "INV1", "880.00", "2025-01-15"],
    ["JE3", "2025-02", "4000-revenue", "invoice", "INV2", "500.00", "2025-02-04"],
    ["JE4", "2025-02", "5000-cost-of-goods", "invoice", "INV2", "440.00", "2025-02-04"],
    ["JE5", "2025-02", "5000-cost-of-goods", "invoice", "INV3", "150.00", "2025-02-10"],
    ["JE6", "2025-02", "5100-vendor-credits", "credit", "CM1", "50.00", "2025-02-03"],
    ["JE7", "2025-09", "5100-vendor-credits", "credit", "CM2", "25.00", "2025-09-02"],
    ["JE8", "2025-02", "4000-revenue", "invoice", "ORPHAN1", "300.00", "2025-02-12"],
]

ROWS = {
    "vendors.csv": VENDORS,
    "partners.csv": PARTNERS,
    "plans.csv": PLANS,
    "products.csv": PRODUCTS,
    "invoices.csv": INVOICES,
    "invoice_lines.csv": LINES,
    "credit_memos.csv": CREDITS,
    "gl_entries.csv": GL_ENTRIES,
}

# One row per period and account, carrying the variance beside its two terms. All
# in cents and all worked out by hand from the rows above. The arithmetic for the two January rows:
#
#   2025-01 revenue   billing 1000 + 500 = 150000c, ledger holds only INV1 at
#                     100000c. INV2's 50000c is awaiting the ledger, so the whole
#                     50000c gap is timing and there is no break.
#   2025-01 cost      billing 800 + 400 = 120000c, ledger holds INV1 at 88000c.
#                     INV2's ledger amount of 44000c is the timing term, and the
#                     two amount differences are -8000c on INV1 and -4000c on
#                     INV2, so the break is -12000c. 44000 - 12000 = 32000.
EXPECTED = [
    ("2025-01", "4000-revenue", 50000, 50000, 0),
    ("2025-01", "5000-cost-of-goods", 32000, 44000, -12000),
    ("2025-02", "4000-revenue", -60000, -50000, -10000),
    ("2025-02", "5000-cost-of-goods", -44000, -44000, 0),
    ("2025-02", "5100-vendor-credits", 0, 0, 0),
    ("2025-09", "5100-vendor-credits", 0, 0, 0),
]

# One row, and the window it is measured against is read from the project rather
# than written here.
EXPECTED_ALERT = [("CM2", "2025-01", "2025-09", 230)]


class FixtureFailure(AssertionError):
    pass


def write_corpus(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, header in ARTIFACTS.items():
        with (directory / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(ROWS[name])


def dbt(args: list[str], db_path: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DBT_PROFILES_DIR"] = str(TRANSFORM / "profiles")
    env["GMB_DUCKDB_PATH"] = str(db_path)
    env.setdefault("GMB_GCP_PROJECT", "fixture-only-no-such-project")
    env.setdefault("GMB_BQ_DATASET", "gross_margin")
    return subprocess.run(
        ["dbt", *args, "--target", "duckdb", "--no-partial-parse"],
        cwd=TRANSFORM,
        env=env,
        capture_output=True,
        text=True,
    )


def build(db_path: Path) -> None:
    done = dbt(["build", "--full-refresh"], db_path)
    # Both conditions. An exit status of zero with errors in the summary is what
    # a partially failed build looks like, and the allocation fixture learned
    # that the hard way.
    if done.returncode != 0 or "ERROR=0" not in done.stdout:
        raise FixtureFailure(
            f"dbt build did not finish clean, exit {done.returncode}\n"
            + done.stdout[-3000:]
        )


def reopen_window_days() -> int:
    """Read out of dbt_project.yml, so the fixture cannot disagree with the model."""
    text = (TRANSFORM / "dbt_project.yml").read_text(encoding="utf-8")
    found = re.search(r"^\s*recon_reopen_days:\s*(\d+)\s*$", text, re.M)
    if found is None:
        raise FixtureFailure("dbt_project.yml does not declare recon_reopen_days")
    return int(found.group(1))


def recon_rows(db_path: Path) -> list[tuple]:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return [
            (period, account, int(variance), int(timing), int(break_cents))
            for period, account, variance, timing, break_cents in con.execute(
                "select period, account, variance_cents, period_shift_cents, break_cents "
                "from main_marts.recon_billing_to_gl order by period, account"
            ).fetchall()
        ]
    finally:
        con.close()


def alert_rows(db_path: Path) -> list[tuple]:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return [
            (credit, stale, posted, int(lag))
            for credit, stale, posted, lag in con.execute(
                "select credit_id, stale_period, posted_in_period, observable_lag_days "
                "from main_marts.alert_late_credit_arrival order by credit_id"
            ).fetchall()
        ]
    finally:
        con.close()


def term_detail(db_path: Path) -> dict:
    """The four named terms for the two rows the fixture argues about.

    Published so that a later change which keeps the variance right and moves
    money between the terms is visible in the measurement rather than only in
    this file's assertions.
    """
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return {
            f"{period} {account}": {
                "unposted_cents": int(unposted),
                "ledger_only_cents": int(ledger_only),
                "amount_difference_cents": int(difference),
                "awaiting_the_ledger_cents": int(awaiting),
                "from_another_period_cents": int(from_another),
            }
            for period, account, unposted, ledger_only, difference, awaiting,
                from_another in con.execute(
                "select period, account, unposted_cents, ledger_only_cents, "
                "amount_difference_cents, awaiting_the_ledger_cents, "
                "from_another_period_cents from main_marts.recon_billing_to_gl "
                "order by period, account"
            ).fetchall()
        }
    finally:
        con.close()


def expect(label: str, got, want) -> None:
    if got != want:
        raise FixtureFailure(f"{label}\n  got  {got}\n  want {want}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep", action="store_true", help="leave the workspace behind to inspect"
    )
    args = parser.parse_args(argv)

    window = reopen_window_days()
    workspace = Path(tempfile.mkdtemp(prefix="gmb-recon-"))
    try:
        corpus = workspace / "corpus"
        db_path = workspace / "fixture.duckdb"
        write_corpus(corpus)
        load(corpus, db_path)
        build(db_path)

        rows = recon_rows(db_path)
        expect("the reconciliation rows", rows, EXPECTED)
        print(f"run 1  {len(rows)} period and account rows, every hand figure matched")

        # The invariant that separates today's version from the first one. It is
        # asserted here as well as in the dbt test because this corpus is the
        # only place a document carries both defects.
        timing_total = sum(row[3] for row in rows)
        expect("timing over the whole fixture", timing_total, 0)

        detail = term_detail(db_path)
        january_cost = detail["2025-01 5000-cost-of-goods"]
        expect(
            "INV2's amount difference has to be a break and not timing",
            january_cost["amount_difference_cents"],
            -12000,
        )
        expect(
            "the amount moving out of January cost is the ledger's, not billing's",
            january_cost["awaiting_the_ledger_cents"],
            44000,
        )
        print(
            "run 2  the document that is in the wrong period and at the wrong "
            "amount puts its difference in the break"
        )

        alerts = alert_rows(db_path)
        expect("the alert rows", alerts, EXPECTED_ALERT)
        if alerts[0][3] <= window:
            raise FixtureFailure(
                f"CM2 is {alerts[0][3]} days out and the window is {window}, so this "
                "fixture is no longer testing the alert at all"
            )
        print(
            f"run 3  one alert, {alerts[0][3]} days out against a {window} day "
            f"window, and the period it makes stale is {alerts[0][1]}"
        )

        manifest.write(
            FIXTURE_MEASUREMENT,
            {
                "reopen_window_days": window,
                "rows": [
                    {
                        "period": period,
                        "account": account,
                        "variance_cents": variance,
                        "period_shift_cents": timing,
                        "break_cents": break_cents,
                    }
                    for period, account, variance, timing, break_cents in rows
                ],
                "terms": detail,
                "period_shift_cents_over_the_fixture": timing_total,
                "alerts": [
                    {
                        "credit_id": credit,
                        "stale_period": stale,
                        "posted_in_period": posted,
                        "observable_lag_days": lag,
                    }
                    for credit, stale, posted, lag in alerts
                ],
                "rebuilt_by": "python3 scripts/recon_fixture.py",
            },
        )
        print(f"wrote {FIXTURE_MEASUREMENT.relative_to(REPO_ROOT)}")
    finally:
        if args.keep:
            print(f"workspace left at {workspace}")
        else:
            shutil.rmtree(workspace, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
