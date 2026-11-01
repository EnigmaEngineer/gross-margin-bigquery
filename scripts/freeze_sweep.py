"""Recomputes the period shift share at several close freeze widths.

    python3 scripts/freeze_sweep.py
    python3 scripts/freeze_sweep.py --widths 1 2 3 4

The headline number in the README is that almost all of the gross period
variance is a timing shift rather than a break. That figure is dominated by
invoices billed at the end of a month, and which days count as the end of a
month is `FREEZE_BUSINESS_DAYS` in `gross_margin/calendar.py`. It is 2 because
that is what the module author chose. Nothing measured it.

A caveat beside the figure would have been the cheap answer. The generator is
deterministic and takes the freeze width through a module constant, so the
expensive answer is a loop, and a range is worth more than a sentence saying a
number might move.

What the loop costs is a full rebuild per width, because the freeze changes
posting dates, posting dates change which period a document lands in, and the
period is what the reconciliation groups on. So each width gets its own corpus
and its own database and its own `dbt build`. Nothing is reused and nothing is
cached, which is the only way the arms are comparable.

The committed width is run like any other and its share has to come back equal
to what `measurements/recon-by-period.json` holds. That is the control. A sweep
whose own baseline does not reproduce the published figure is measuring its
harness.

What the loop actually found was worse than a wide range, and it is the reason
two figures come back instead of one. The number published for five days was
the gross period shift over the gross variance. Those are two absolute sums
taken down two columns, and `variance = period_shift + break` holds per row
with the terms free to have opposite signs, so a row's shift can be larger than
its own variance and the ratio has no upper bound. At a three day close it
returns 100.77 percent. It had read as a share for as long as it happened to
stay under a hundred.

So the bounded figure is each gross term against the sum of both, and the ratio
is kept under a name that says it is a ratio. The sweep reports both and names
the widths where the second one leaves the interval a share can live in.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import calendar as cal
from gross_margin import discrepancies, ledger, manifest
from gross_margin.generate import build, write
from scripts.load_duckdb import load
from scripts.run_transform import dbt, build_result

SWEEP_MEASUREMENT = REPO_ROOT / "measurements" / "freeze-width-sweep.json"
RECON_MEASUREMENT = REPO_ROOT / "measurements" / "recon-by-period.json"

# The width the repository ships, first so the control is visibly the published
# one, then one either side. Four would be a fortnight of close on a month with
# twenty business days and nobody runs that.
WIDTHS = (2, 1, 3)
SEED = 20260428


def share_at(width: int, workspace: Path) -> dict:
    """Build the whole corpus and warehouse at this freeze width.

    The constant is rebound on the module rather than threaded through every
    signature between here and the two callers of `posting_date`. That is worth
    a word. `is_frozen` reads the module global when it is called, so rebinding
    it is enough, and a parameter would have to travel through the generator and
    the ledger and every function between them to reach two call sites. The cost
    is that this function is not safe to run in parallel with itself, and it is
    not run that way.
    """
    original = cal.FREEZE_BUSINESS_DAYS
    cal.FREEZE_BUSINESS_DAYS = width
    try:
        corpus = build(seed=SEED)
        discrepancies.apply(corpus)
        ledger.post(corpus)
        ledger.inject(corpus)
        corpus_dir = workspace / f"corpus-{width}"
        write(corpus, corpus_dir)
    finally:
        cal.FREEZE_BUSINESS_DAYS = original

    db_path = workspace / f"freeze-{width}.duckdb"
    load(corpus_dir, db_path)
    built = dbt(["build"], db_path, "duckdb")
    if built.returncode != 0:
        sys.stderr.write(built.stdout[-4000:])
        raise SystemExit(f"dbt build failed at freeze width {width}")
    counts = build_result(built.stdout)

    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        totals = con.execute(
            "select sum(abs(variance_cents)), sum(abs(period_shift_cents)), "
            "sum(abs(break_cents)), sum(variance_cents), sum(period_shift_cents), "
            "count(*), sum(case when break_cents <> 0 then 1 else 0 end) "
            "from main_marts.recon_billing_to_gl"
        ).fetchone()
        # Documents the ledger filed in a period other than the one billing
        # booked them in. This is the mechanism the share is made of, so it
        # moves with the width and it is worth carrying beside the ratio.
        opposed = con.execute(
            "select "
            "sum(case when abs(period_shift_cents) > abs(variance_cents) "
            "  then 1 else 0 end) "
            "from main_marts.recon_billing_to_gl"
        ).fetchone()[0]
        frozen = con.execute(
            "select count(*) from main_marts.fct_gl_posting "
            "where billing_period is not null and gl_period is not null "
            "and billing_period <> gl_period"
        ).fetchone()[0]
    finally:
        con.close()

    gross = int(totals[0])
    return {
        "freeze_business_days": width,
        "gross_variance_cents": gross,
        "period_shift_cents": int(totals[1]),
        "break_cents": int(totals[2]),
        "signed_variance_cents": int(totals[3]),
        "signed_period_shift_cents": int(totals[4]),
        "period_and_account_rows": int(totals[5]),
        "rows_with_a_break": int(totals[6]),
        "documents_posting_outside_their_billing_period": int(frozen),
        "rows_where_the_period_shift_exceeds_the_variance": int(opposed or 0),
        "share_of_the_gross_absolute_terms_that_is_a_period_shift_pct": (
            round(100.0 * int(totals[1]) / (int(totals[1]) + int(totals[2])), 2)
            if (int(totals[1]) + int(totals[2]))
            else None
        ),
        "ratio_of_gross_period_shift_to_gross_variance_pct": (
            round(100.0 * int(totals[1]) / gross, 2) if gross else None
        ),
        "dbt_build": counts,
    }


def sweep(widths: tuple[int, ...]) -> dict:
    workspace = Path(tempfile.mkdtemp(prefix="freeze-sweep-"))
    try:
        arms = [share_at(width, workspace) for width in widths]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    by_width = {arm["freeze_business_days"]: arm for arm in arms}
    shares = {
        width: arm["share_of_the_gross_absolute_terms_that_is_a_period_shift_pct"]
        for width, arm in by_width.items()
    }
    ratios = {
        width: arm["ratio_of_gross_period_shift_to_gross_variance_pct"]
        for width, arm in by_width.items()
    }
    if any(value is None for value in shares.values()):
        raise SystemExit(
            "an arm produced no gross variance at all, so its share is undefined "
            "and the range would be over a smaller set than it claims"
        )

    published = manifest.read(RECON_MEASUREMENT)[
        "share_of_the_gross_absolute_terms_that_is_a_period_shift_pct"
    ]
    control = shares[cal.FREEZE_BUSINESS_DAYS]
    return {
        "arms": sorted(arms, key=lambda arm: arm["freeze_business_days"]),
        "widths": sorted(widths),
        "seed": SEED,
        "shipped_freeze_business_days": cal.FREEZE_BUSINESS_DAYS,
        "share_pct_by_width": {str(k): v for k, v in sorted(shares.items())},
        "share_pct_range": {
            "min": min(shares.values()),
            "max": max(shares.values()),
            "spread": round(max(shares.values()) - min(shares.values()), 2),
        },
        "ratio_pct_by_width": {str(k): v for k, v in sorted(ratios.items())},
        "ratio_pct_range": {
            "min": min(ratios.values()),
            "max": max(ratios.values()),
            "spread": round(max(ratios.values()) - min(ratios.values()), 2),
        },
        # The whole reason the share above is reported separately. This ratio is
        # not bounded by a hundred and at a three day close it is not under one.
        "widths_where_the_ratio_exceeds_one_hundred": sorted(
            width for width, value in ratios.items() if value > 100.0
        ),
        "control": {
            "published_share_pct": published,
            "share_pct_at_the_shipped_width": control,
            "agrees": published == control,
        },
        "rebuilt_by": "python3 scripts/freeze_sweep.py",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--widths", type=int, nargs="+", default=list(WIDTHS))
    args = parser.parse_args(argv)

    widths = tuple(dict.fromkeys(args.widths))
    if len(widths) < 2:
        raise SystemExit("a sweep needs at least two widths. One width is the figure we have")
    if any(width < 1 for width in widths):
        raise SystemExit("a freeze of zero business days is no freeze, so there is nothing to vary")
    if cal.FREEZE_BUSINESS_DAYS not in widths:
        raise SystemExit(
            f"the shipped width {cal.FREEZE_BUSINESS_DAYS} is not among {list(widths)}, so "
            "the sweep would have no control arm to reproduce the published figure with"
        )

    result = sweep(widths)
    manifest.write(SWEEP_MEASUREMENT, result)

    print(
        f"{'freeze':>7}  {'share pct':>10}  {'ratio pct':>10}  {'gross cents':>14}"
        f"  {'shifted docs':>12}  {'shift>var rows':>15}"
    )
    for arm in result["arms"]:
        print(
            f"{arm['freeze_business_days']:>7}"
            f"  {arm['share_of_the_gross_absolute_terms_that_is_a_period_shift_pct']:>10}"
            f"  {arm['ratio_of_gross_period_shift_to_gross_variance_pct']:>10}"
            f"  {arm['gross_variance_cents']:>14}"
            f"  {arm['documents_posting_outside_their_billing_period']:>12}"
            f"  {arm['rows_where_the_period_shift_exceeds_the_variance']:>15}"
        )
    band = result["share_pct_range"]
    print(f"share moves {band['spread']} points over these widths, {band['min']} to {band['max']}")
    ratio_band = result["ratio_pct_range"]
    print(
        f"the unbounded ratio moves {ratio_band['spread']} points, "
        f"{ratio_band['min']} to {ratio_band['max']}"
    )
    over = result["widths_where_the_ratio_exceeds_one_hundred"]
    if over:
        print(
            f"at freeze widths {over} the ratio is above a hundred percent, which is "
            "why it is not reported as a share of anything"
        )
    control = result["control"]
    print(
        f"control at width {result['shipped_freeze_business_days']}: "
        f"{control['share_pct_at_the_shipped_width']} against a published "
        f"{control['published_share_pct']}, agrees {control['agrees']}"
    )
    if not control["agrees"]:
        print("the control arm did not reproduce the published figure, so this sweep")
        print("is measuring its own harness rather than the freeze width")
        return 1
    print(f"wrote {SWEEP_MEASUREMENT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
