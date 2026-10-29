"""Proves the allocation and its lookback incremental on a corpus small enough to
check by hand.

    python3 scripts/allocation_fixture.py

Twenty thousand lines cannot be verified by reading them. Six can. This builds a
six line corpus with the same seven files and the same headers the generator
writes, loads it through scripts/load_duckdb.py, and runs the real dbt models
over it. The expected numbers below were worked out on paper and are written as
literals, so a model change that quietly alters the arithmetic fails here rather
than moving a total nobody can recompute.

The fixture covers all four allocation scopes, a tie in the largest remainder
pass that one leftover cent cannot settle evenly, a scope whose basis sums to
zero, a credit crossing a month boundary, and a rebate spanning three invoices
in one quarter.

Then it runs the incremental three times to answer the question the model
actually turns on. The lookback is on posted_date, the date a credit arrived, and
not on the invoice date it adjusts. Run two appends a credit that arrives four
months after its invoice and asserts the incremental picks it up. Run three
appends one that arrives outside the window and asserts it is missed, because a
window that cannot miss anything is not a window and saying so is cheaper than
discovering it in a reconciliation.

Nothing here touches the real corpus or the real database. Both live under a
temporary directory that is removed on the way out.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import ledger, manifest
from gross_margin.registry import ARTIFACTS
from scripts.load_duckdb import load

TRANSFORM = REPO_ROOT / "transform"
FIXTURE_MEASUREMENT = REPO_ROOT / "measurements" / "allocation-fixture.json"

# One vendor, one partner, one product line. Everything that is not the subject
# of the fixture is held constant so a failure points at the allocation.
VENDORS = [["V1", "Northwind Supply", "quarterly-volume"]]
PARTNERS = [["P1", "Harbour Systems", "west", "mid-market"]]
PLANS = [["P1", "PLAN-A", "2.5", "2023-01-01"]]
PRODUCTS = [
    ["PR1", "Alpha", "V1", "2023-01-01"],
    ["PR2", "Alpha", "V1", "2023-01-01"],
    ["PR3", "Alpha", "V1", "2023-01-01"],
]

# INV0 sits a year before the rest. It is the old invoice the third run uses to
# show what the window cannot reach.
INVOICES = [
    ["INV0", "2024-01-15", "2024-01", "P1", "direct"],
    ["INV1", "2025-01-30", "2025-01", "P1", "direct"],
    ["INV2", "2025-02-10", "2025-02", "P1", "direct"],
    ["INV3", "2025-03-05", "2025-03", "P1", "direct"],
    # Second quarter, so it stays out of the rebate above. Its two lines are
    # deliberately identical.
    ["INV4", "2025-06-02", "2025-06", "P1", "direct"],
    # Third quarter. Both of its lines are free replacements, so they carry cost
    # and no revenue at all.
    ["INV5", "2025-09-01", "2025-09", "P1", "direct"],
]

# Costs chosen so the rebate share does not divide evenly and two lines tie on
# the fractional part. A fixture where every share lands on a whole cent would
# never exercise the rounding at all.
LINES = [
    ["L0", "INV0", "PR1", "1", "150.00", "100.00"],
    ["L1", "INV1", "PR1", "1", "150.00", "100.00"],
    ["L2", "INV1", "PR2", "1", "260.00", "200.00"],
    ["L3", "INV2", "PR3", "1", "900.00", "700.00"],
    ["L4", "INV2", "PR2", "1", "300.00", "200.00"],
    ["L5", "INV3", "PR1", "1", "400.00", "300.00"],
    ["L6", "INV4", "PR1", "1", "500.00", "400.00"],
    ["L7", "INV4", "PR2", "1", "500.00", "400.00"],
    ["L8", "INV5", "PR1", "1", "0.00", "50.00"],
    ["L9", "INV5", "PR2", "1", "0.00", "50.00"],
]

BASE_CREDITS = [
    # A quarterly rebate quoting L1 and earned across the whole of 2025 Q1.
    # Q1 cost is 100 + 200 + 700 + 200 + 300 = 1500.00. That gives a share of
    # 666.67 cents on L1 and 1333.33 on L2 and 4666.67 on L3. L4 takes 1333.33
    # and L5 takes 2000 exactly. Floors sum to 9998 and the two leftover cents
    # go to the largest fractions, which are L1 and L3 tied at .6667 and
    # separated by line_id.
    ["CM0001", "VD-0001", "INV1", "L1", "volume_rebate", "cost", "100.00",
     "2025-04-05", "2025-04-07", "2025-04"],
    # Price protection on a January invoice, posted in February. The allocation
    # belongs to the line's period and not to the credit's.
    ["CM0002", "VD-0002", "INV1", "L1", "price_protection", "cost", "12.34",
     "2025-02-01", "2025-02-03", "2025-02"],
    # The vendor quotes a line this system does not hold and an invoice it does.
    # INV2 revenue is 900 + 300 = 1200.00, so 1001 cents split 750.75 and 250.25,
    # and the single leftover cent goes to L3.
    ["CM0003", "VD-0003", "INV2", "L9999", "return_credit", "revenue", "10.01",
     "2025-03-01", "2025-03-03", "2025-03"],
    # Neither reference resolves. One row, no line, the whole amount.
    ["CM0004", "VD-0004", "INV9999", "L9998", "price_protection", "cost", "5.00",
     "2025-03-10", "2025-03-12", "2025-03"],
    # The case that makes the tie break mean something. INV4's two lines carry
    # the same revenue, so 101 cents splits 50.5 and 50.5 and there is exactly
    # one leftover cent for two lines that cannot be separated by their
    # remainder. Whichever rule breaks the tie decides, and without this row a
    # reversed tie break changes no number anywhere in the fixture.
    ["CM0007", "VD-0007", "INV4", "L9997", "return_credit", "revenue", "1.01",
     "2025-07-01", "2025-07-03", "2025-07"],
    # A revenue credit against an invoice whose lines billed no revenue. The
    # basis sums to zero, so a pro rata share is undefined and the model falls
    # back to an equal split. Without this row that branch is never reached and
    # deleting it changes no number in the fixture.
    ["CM0008", "VD-0008", "INV5", "L9996", "return_credit", "revenue", "3.00",
     "2025-07-03", "2025-07-05", "2025-07"],
]

# The credit that separates the two anchors, and the reason INV0 is in the
# fixture at all. It arrives on the newest date in the corpus and it adjusts the
# oldest invoice. A window measured on posted_date reaches it the day it lands. A
# window of the same length measured on invoice_date does not come close, and the
# run asserts that gap rather than describing it.
LATE_CREDIT = ["CM0005", "VD-0005", "INV0", "L0", "price_protection", "cost",
               "1.11", "2025-08-13", "2025-08-15", "2025-08"]

# Backdated past the window on either anchor. This one is meant to be missed.
BACKDATED_CREDIT = ["CM0006", "VD-0006", "INV0", "L0", "price_protection", "cost",
                    "9.99", "2024-12-18", "2024-12-20", "2024-12"]

ROWS = {
    "vendors.csv": VENDORS,
    "partners.csv": PARTNERS,
    "plans.csv": PLANS,
    "products.csv": PRODUCTS,
    "invoices.csv": INVOICES,
    "invoice_lines.csv": LINES,
}

# Worked out on paper from the costs above. credit, line, cents.
EXPECTED_AFTER_FIRST_RUN = [
    ("CM0001", "L1", 667),
    ("CM0001", "L2", 1333),
    ("CM0001", "L3", 4667),
    ("CM0001", "L4", 1333),
    ("CM0001", "L5", 2000),
    ("CM0002", "L1", 1234),
    ("CM0003", "L3", 751),
    ("CM0003", "L4", 250),
    ("CM0004", None, 500),
    ("CM0007", "L6", 51),
    ("CM0007", "L7", 50),
    ("CM0008", "L8", 150),
    ("CM0008", "L9", 150),
]

EXPECTED_SCOPES = {
    "CM0001": "vendor_quarter",
    "CM0002": "direct",
    "CM0003": "invoice",
    "CM0004": "unallocated",
    "CM0007": "invoice",
    "CM0008": "invoice",
}

# L0 is in 2024 Q1 on its own, so the rebate above never reaches it and it holds
# nothing until the backdated credit lands.
EXPECTED_ROLLUP_BY_PERIOD = {
    # period: allocated cost cents. The February credit sits in January because
    # that is when its line was billed.
    "2024-01": 0,
    "2025-01": 667 + 1333 + 1234,
    "2025-02": 4667 + 1333,
    "2025-03": 2000,
    # CM0007 is a revenue credit, so it adds nothing to the cost column here.
    "2025-06": 0,
    "2025-09": 0,
}


class FixtureFailure(RuntimeError):
    pass


def as_dicts(name: str, rows: list[list[str]]) -> list[dict]:
    """The fixture holds rows as lists and gross_margin.ledger wants dicts."""
    header = ARTIFACTS[name]
    return [dict(zip(header, row)) for row in rows]


def ledger_for(credits: list[list[str]]) -> list[list[str]]:
    """A ledger posted exactly from these billing rows, with nothing broken.

    This fixture is about the allocation and not about the reconciliation, so the
    ledger it writes agrees with billing everywhere. scripts/recon_fixture.py is
    the one that breaks it on purpose. It exists at all because load_duckdb reads
    the artifact registry and would refuse a corpus missing a registered file.
    """
    shim = SimpleNamespace(
        seed=0,
        invoices=as_dicts("invoices.csv", ROWS["invoices.csv"]),
        lines=as_dicts("invoice_lines.csv", ROWS["invoice_lines.csv"]),
        credits=as_dicts("credit_memos.csv", credits),
        gl_entries=[],
    )
    header = ARTIFACTS["gl_entries.csv"]
    return [[entry[column] for column in header] for entry in ledger.post(shim)]


def write_corpus(directory: Path, credits: list[list[str]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = dict(ROWS)
    payload["credit_memos.csv"] = credits
    payload["gl_entries.csv"] = ledger_for(credits)
    for name, header in ARTIFACTS.items():
        with (directory / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(payload[name])


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


def build(db_path: Path, full_refresh: bool) -> None:
    args = ["build"]
    if full_refresh:
        args.append("--full-refresh")
    done = dbt(args, db_path)
    if done.returncode != 0 or "ERROR=0" not in done.stdout:
        raise FixtureFailure(
            f"dbt {' '.join(args)} did not finish clean, exit {done.returncode}\n"
            + done.stdout[-3000:]
        )


def allocation_rows(db_path: Path) -> list[tuple]:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(
            "select credit_id, line_id, allocated_cents, allocation_scope "
            "from main_marts.fct_credit_allocation "
            "order by credit_id, line_id"
        ).fetchall()
    finally:
        con.close()


def rollup_by_period(db_path: Path) -> dict[str, int]:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "select margin.period, "
            "sum(rollup.allocated_against_cost_cents) "
            "from main_marts.fct_invoice_line_margin as margin "
            "join main_marts.credit_rollup_by_line as rollup "
            "  on margin.line_id = rollup.line_id "
            "group by 1 order by 1"
        ).fetchall()
    finally:
        con.close()
    return {period: int(cents) for period, cents in rows}


def date_of(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def lookback_days() -> int:
    """The window the project declares, read off the project file.

    Typing it here would make this record a second place the number lives, and
    the two would part company the first time one of them changed.
    """
    text = (TRANSFORM / "dbt_project.yml").read_text(encoding="utf-8")
    match = re.search(r"^\s*allocation_lookback_days:\s*(\d+)\s*$", text, re.M)
    if not match:
        raise FixtureFailure("dbt_project.yml does not declare allocation_lookback_days")
    return int(match.group(1))


def expect(label: str, got, want) -> None:
    if got != want:
        raise FixtureFailure(f"{label}\n  expected {want}\n  got      {got}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the temporary corpus and database in place for inspection",
    )
    args = parser.parse_args(argv)

    workspace = Path(tempfile.mkdtemp(prefix="gmb-fixture-"))
    corpus = workspace / "data"
    db_path = workspace / "fixture.duckdb"
    results: dict = {"runs": []}

    try:
        # Run one. The whole fixture from empty, with the incremental built as a
        # table for the first time.
        write_corpus(corpus, BASE_CREDITS)
        load(corpus, db_path)
        build(db_path, full_refresh=True)

        rows = allocation_rows(db_path)
        expect(
            "allocation after the first build",
            [(c, line, cents) for c, line, cents, _ in rows],
            EXPECTED_AFTER_FIRST_RUN,
        )
        expect(
            "allocation scopes",
            {c: scope for c, _, _, scope in rows},
            EXPECTED_SCOPES,
        )
        expect(
            "every credit allocates to its own total",
            sum(cents for _, _, cents, _ in rows),
            10000 + 1234 + 1001 + 500 + 101 + 300,
        )
        expect(
            "the February credit lands in its line's January period",
            rollup_by_period(db_path),
            EXPECTED_ROLLUP_BY_PERIOD,
        )
        results["runs"].append({
            "run": "full refresh over four credits",
            "allocation_rows": len(rows),
            "cents_allocated": sum(cents for _, _, cents, _ in rows),
        })
        print(f"run 1  {len(rows)} allocation rows, every hand figure matched")

        # Run two. A credit that arrives after every posted_date already in the
        # table and adjusts the oldest invoice in it.
        cutoff = max(
            date_of(credit[8]) for credit in BASE_CREDITS
        ) - dt.timedelta(days=lookback_days())
        late_invoice_date = date_of(
            next(row[1] for row in INVOICES if row[0] == LATE_CREDIT[2])
        )
        if not date_of(LATE_CREDIT[8]) >= cutoff:
            raise FixtureFailure(
                "the late credit is outside the posted_date window, so run two "
                "would prove nothing"
            )
        if late_invoice_date >= cutoff:
            raise FixtureFailure(
                f"the late credit's invoice at {late_invoice_date} is inside the "
                f"same window at {cutoff}, so an invoice_date anchor would reach "
                "it too and this run cannot tell the two anchors apart"
            )
        write_corpus(corpus, BASE_CREDITS + [LATE_CREDIT])
        load(corpus, db_path)
        build(db_path, full_refresh=False)

        after_late = allocation_rows(db_path)
        expect(
            "the late credit reached the table",
            [(c, line, cents) for c, line, cents, _ in after_late
             if c == "CM0005"],
            [("CM0005", "L0", 111)],
        )
        expect(
            "nothing else moved",
            [(c, line, cents) for c, line, cents, _ in after_late
             if c != "CM0005"],
            EXPECTED_AFTER_FIRST_RUN,
        )
        days_late = (date_of(LATE_CREDIT[8]) - late_invoice_date).days
        results["runs"].append({
            "run": "incremental, a credit arriving long after the invoice it adjusts",
            "allocation_rows": len(after_late),
            "days_between_invoice_and_posting": days_late,
            "invoice_date_anchor_would_have_missed_it": True,
            "picked_up": True,
        })
        print(f"run 2  picked up a credit posted {days_late} days after its invoice, "
              "which an invoice_date window of the same length would have missed")

        # Run three. A credit backdated outside the window. The incremental does
        # not see it and a full refresh does, which is the honest shape of every
        # lookback and the reason the figure is published rather than assumed.
        write_corpus(corpus, BASE_CREDITS + [LATE_CREDIT, BACKDATED_CREDIT])
        load(corpus, db_path)
        build(db_path, full_refresh=False)

        after_backdated = allocation_rows(db_path)
        expect(
            "the backdated credit is outside the window and is missed",
            [c for c, _, _, _ in after_backdated if c == "CM0006"],
            [],
        )

        build(db_path, full_refresh=True)
        after_rebuild = allocation_rows(db_path)
        expect(
            "a full refresh recovers it",
            [(c, line, cents) for c, line, cents, _ in after_rebuild
             if c == "CM0006"],
            [("CM0006", "L0", 999)],
        )
        results["runs"].append({
            "run": "incremental, credit backdated past the lookback",
            "picked_up": False,
            "recovered_by_full_refresh": True,
        })
        print("run 3  the backdated credit was missed and a full refresh recovered it")

        results["lookback_days"] = lookback_days()
        results["checked"] = len(EXPECTED_AFTER_FIRST_RUN) + 6
        results["rebuilt_by"] = "python3 scripts/allocation_fixture.py"
        manifest.write(FIXTURE_MEASUREMENT, results)
        print(f"wrote {FIXTURE_MEASUREMENT.relative_to(REPO_ROOT)}")
        return 0
    except FixtureFailure as failure:
        print(f"FIXTURE FAILED\n{failure}", file=sys.stderr)
        return 1
    except Exception as broken:
        # A query that will not bind exits non zero too, and a caller reading
        # only the status cannot tell that from an assertion catching something.
        # It happened once while the rollup columns were being renamed, and a
        # mutation sweep reported eight kills against a harness that never ran.
        print(f"FIXTURE COULD NOT RUN\n{broken!r}", file=sys.stderr)
        return 2
    finally:
        if args.keep:
            print(f"left behind {workspace}")
        else:
            shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
