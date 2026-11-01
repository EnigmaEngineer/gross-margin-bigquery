"""Measures how the volume rebate fan out grows when the corpus grows.

    python3 scripts/allocation_profile.py
    python3 scripts/allocation_profile.py --scale 10

`fct_credit_allocation` spreads a volume rebate across every line billed for
that vendor in the quarter it was earned in. The spread is a self join on the
line table keyed by vendor and quarter start, and on the shipped corpus it
turns a few thousand credits into a couple of hundred thousand allocation
rows. The table rebuilds inside the normal `dbt build` with no cost worth
noticing, and that is the problem rather than the reassurance. The corpus holds
twenty thousand lines. A distributor's ledger does not.

So this measures the join at the shipped size and at a corpus scaled up, and
reports the growth. It reports counts and never a wall time, because a figure
this repository publishes has to reproduce on a machine that is not this one
and an elapsed time does not. The counts are exact and they answer the question
a timing would only hint at, which is whether the pair count grows faster than
the input.

It also measures the rewrite. The design answer, if the growth is bad, is to
aggregate each rebate to its vendor and quarter before spreading rather than
after, which changes no published number because the allocation is pro rata on
a basis that the aggregate preserves. Both shapes are counted here so the
decision has a number under it instead of an instinct.
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

from gross_margin import discrepancies, ledger, manifest
from gross_margin.generate import build, write
from scripts.load_duckdb import load
from scripts.run_transform import dbt, build_result

PROFILE_MEASUREMENT = REPO_ROOT / "measurements" / "allocation-growth.json"
SEED = 20260428

# The shipped corpus shape, from gross_margin.generate.build's defaults. These
# are populations to draw from and not row counts, which is the thing that had
# to be found out the hard way. The first version of this script scaled
# `partners` and `products` by ten and the corpus came back 1.02 times larger,
# because invoices are generated per business day from INVOICES_PER_DAY and the
# partner list is only what each one is assigned to. `volume_scale` is the
# argument that moves the row count and it was added for this measurement.
BASE = {"vendors": 14, "partners": 40, "products": 120}

# The join as the model writes it. Credits joined to the line they quote, then
# to every line sharing that line's vendor and quarter. Spelled out here rather
# than read out of the compiled model, and that is a real cost: it is a second
# copy of the shape in the model, and if the model's keys change this measures
# the old shape. A check asserts the keys still match what the model joins on.
SCOPED_PAIRS = """
select count(*)
from main_marts.fct_credit_memo as credits
inner join main_marts.fct_invoice_line_margin as quoted
    on credits.line_id = quoted.line_id
inner join main_marts.fct_invoice_line_margin as lines
    on lines.vendor_id = quoted.vendor_id
    and date_trunc('quarter', lines.invoice_date)
        = date_trunc('quarter', quoted.invoice_date)
where credits.is_placeable
  and credits.credit_type = 'volume_rebate'
"""

# The same answer reached by grouping the lines first. One row per vendor and
# quarter on the right hand side, so the credit meets a group rather than every
# member of it. The allocation still needs the members to divide across, which
# is why this is the measurement of the join and not of the whole model.
AGGREGATED_PAIRS = """
with groups as (
    select
        vendor_id,
        date_trunc('quarter', invoice_date) as quarter_start,
        count(*) as lines_in_group,
        sum(extended_cost_usd) as basis_usd
    from main_marts.fct_invoice_line_margin
    group by 1, 2
)
select count(*)
from main_marts.fct_credit_memo as credits
inner join main_marts.fct_invoice_line_margin as quoted
    on credits.line_id = quoted.line_id
inner join groups
    on groups.vendor_id = quoted.vendor_id
    and groups.quarter_start = date_trunc('quarter', quoted.invoice_date)
where credits.is_placeable
  and credits.credit_type = 'volume_rebate'
"""


def warehouse_at(scale: int, workspace: Path) -> Path:
    """Generate and build a corpus at this scale, returning the database path."""
    corpus = build(seed=SEED, volume_scale=scale, **BASE)
    discrepancies.apply(corpus)
    ledger.post(corpus)
    ledger.inject(corpus)
    corpus_dir = workspace / f"corpus-x{scale}"
    write(corpus, corpus_dir)

    db_path = workspace / f"alloc-x{scale}.duckdb"
    load(corpus_dir, db_path)
    built = dbt(["build", "--exclude", "path:models/scan"], db_path, "duckdb")
    if built.returncode != 0:
        sys.stderr.write(built.stdout[-4000:])
        raise SystemExit(f"dbt build failed at scale {scale}")
    build_result(built.stdout)
    return db_path


def profile_one(scale: int, db_path: Path) -> dict:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        lines = con.execute(
            "select count(*) from main_marts.fct_invoice_line_margin"
        ).fetchone()[0]
        credits = con.execute(
            "select count(*) from main_marts.fct_credit_memo"
        ).fetchone()[0]
        rebates = con.execute(
            "select count(*) from main_marts.fct_credit_memo "
            "where is_placeable and credit_type = 'volume_rebate'"
        ).fetchone()[0]
        allocation_rows = con.execute(
            "select count(*) from main_marts.fct_credit_allocation"
        ).fetchone()[0]
        scoped_pairs = con.execute(SCOPED_PAIRS).fetchone()[0]
        aggregated_pairs = con.execute(AGGREGATED_PAIRS).fetchone()[0]
        groups = con.execute(
            "select count(*) from (select vendor_id, "
            "date_trunc('quarter', invoice_date) from "
            "main_marts.fct_invoice_line_margin group by 1, 2)"
        ).fetchone()[0]
        widest = con.execute(
            "select max(c) from (select count(*) as c from "
            "main_marts.fct_invoice_line_margin group by vendor_id, "
            "date_trunc('quarter', invoice_date))"
        ).fetchone()[0]
    finally:
        con.close()

    return {
        "scale": scale,
        "lines": int(lines),
        "credits": int(credits),
        "placeable_volume_rebates": int(rebates),
        "allocation_rows": int(allocation_rows),
        "vendor_quarter_groups": int(groups),
        "widest_vendor_quarter_lines": int(widest),
        "scoped_join_pairs": int(scoped_pairs),
        "aggregated_join_pairs": int(aggregated_pairs),
        "allocation_rows_per_credit": round(allocation_rows / credits, 2) if credits else None,
        "pairs_per_rebate": round(scoped_pairs / rebates, 2) if rebates else None,
    }


def growth(small: dict, large: dict) -> dict:
    """How each count multiplied, against how much the input multiplied.

    The ratio that matters is the last one. A join whose pair count grows with
    the square of the corpus has a growth factor larger than the line factor,
    and the whole question is whether this one does.
    """
    def factor(key: str) -> float | None:
        base = small[key]
        return round(large[key] / base, 2) if base else None

    return {
        "corpus_scale_factor": round(large["scale"] / small["scale"], 2),
        "line_factor": factor("lines"),
        "credit_factor": factor("credits"),
        "allocation_row_factor": factor("allocation_rows"),
        "scoped_join_pair_factor": factor("scoped_join_pairs"),
        "aggregated_join_pair_factor": factor("aggregated_join_pairs"),
        "widest_vendor_quarter_factor": factor("widest_vendor_quarter_lines"),
        "pairs_grow_faster_than_lines": (
            factor("scoped_join_pairs") > factor("lines")
            if factor("lines") and factor("scoped_join_pairs")
            else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=int, default=10, help="the larger arm")
    args = parser.parse_args(argv)

    if args.scale < 2:
        raise SystemExit("a growth measurement needs a larger arm than the one we have")

    workspace = Path(tempfile.mkdtemp(prefix="alloc-profile-"))
    try:
        arms = []
        for scale in (1, args.scale):
            db_path = warehouse_at(scale, workspace)
            arms.append(profile_one(scale, db_path))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    result = {
        "arms": arms,
        "seed": SEED,
        "base_corpus_shape": BASE,
        "growth": growth(arms[0], arms[1]),
        "what_this_does_not_measure": (
            "no wall time, by the rule that a published figure has to reproduce "
            "off this machine. It also builds on DuckDB and not on BigQuery, so "
            "it says how the pair count grows and nothing about what the join "
            "would cost in slots or bytes scanned"
        ),
        "rebuilt_by": "python3 scripts/allocation_profile.py",
    }
    manifest.write(PROFILE_MEASUREMENT, result)

    print(
        f"{'scale':>6}  {'lines':>9}  {'credits':>8}  {'rebates':>8}"
        f"  {'alloc rows':>11}  {'scoped pairs':>13}  {'agg pairs':>10}  {'widest grp':>10}"
    )
    for arm in arms:
        print(
            f"{arm['scale']:>6}  {arm['lines']:>9}  {arm['credits']:>8}"
            f"  {arm['placeable_volume_rebates']:>8}  {arm['allocation_rows']:>11}"
            f"  {arm['scoped_join_pairs']:>13}  {arm['aggregated_join_pairs']:>10}"
            f"  {arm['widest_vendor_quarter_lines']:>10}"
        )
    g = result["growth"]
    print(
        f"corpus x{g['corpus_scale_factor']}: lines x{g['line_factor']}, "
        f"allocation rows x{g['allocation_row_factor']}, "
        f"scoped pairs x{g['scoped_join_pair_factor']}, "
        f"aggregated pairs x{g['aggregated_join_pair_factor']}"
    )
    print(f"scoped pairs grow faster than lines: {g['pairs_grow_faster_than_lines']}")
    print(f"wrote {PROFILE_MEASUREMENT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
