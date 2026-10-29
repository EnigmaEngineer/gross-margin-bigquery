"""Refits the lookback window over several seeds and reports the spread.

    python3 scripts/window_spread.py
    python3 scripts/window_spread.py --seeds 5 --coverage 0.95

The committed lag measurement publishes a pooled window of 105 days and keyed
windows of 20 and 49 and 124. Every one of those is a single quantile of one corpus at one seed. That was
fine while the argument was only that 20 and 124 are far apart, because a gap
that wide does not depend on a day either way.

Today the window becomes a threshold. An alert that fires when a credit arrives
later than the window has to know how much the window itself moves when nothing
about the business changes, because a threshold sitting inside the sampling error
of its own input fires on noise and calls it a finding.

So this regenerates the corpus at several seeds and refits the same quantile on
each. It reports the spread rather than an interval. A spread over five draws is
five numbers and a range, and calling that a confidence interval would be the
same mistake in a smarter coat.

What it deliberately does not do is average the windows into a better one. The
reconciliation threshold is set from the widest observed arrival, not from the
centre of a distribution, and the reason is in the README.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import discrepancies, lag, manifest
from gross_margin.generate import build

SPREAD_MEASUREMENT = REPO_ROOT / "measurements" / "window-spread.json"

# The seed the committed measurement was built at, then four more. Written out rather than derived from a
# range so that the set is reproducible from this file alone and so that the
# first entry is visibly the published one.
SEEDS = (20260428, 20260429, 20260430, 20260501, 20260502)

# Candidates to price against every seed. 105 and 124 are what the committed lag
# measurement publishes. 180 is what the allocation lookback was first set to, on
# the grounds that it was wider than the largest lag anybody had seen. The three above it are there
# because that reasoning turned out to rest on one draw.
CANDIDATES = (105, 124, 180, 220, 260, 365)


def fit_one(seed: int, coverage: float) -> dict:
    """Build a corpus at this seed and refit every window figure on it."""
    corpus = build(seed=seed)
    discrepancies.apply(corpus)
    records, unmatched = lag.lag_records(corpus.invoices, corpus.credits)
    if not records:
        raise SystemExit(f"seed {seed} produced no credit matching an invoice")

    summary = lag.summarise(records)
    keyed = lag.keyed_against_pooled(records, coverage)

    # What each candidate would have left outside itself on this seed. This is
    # the column that answers whether a window chosen on one corpus is a bound
    # on another, and it is a count of credits rather than a quantile so there
    # is nothing to interpret.
    outside = {
        str(window): sum(
            1 for record in records if record.observable_lag_days > window
        )
        for window in CANDIDATES
    }

    return {
        "seed": seed,
        "credits": summary["credits"],
        "credits_with_no_matching_invoice": unmatched,
        "censored_credits": corpus.censored_credits,
        "pooled_window_days": keyed["pooled_window_days"],
        "keyed_window_days": keyed["keyed_window_days"],
        "observable_lag_days": summary["observable_lag_days"],
        "credits_outside_each_candidate_window": outside,
        "types_under_covered_by_the_pooled_window": keyed[
            "types_under_covered_by_the_pooled_window"
        ],
    }


def spread(values: list[int]) -> dict:
    return {"min": min(values), "max": max(values), "range": max(values) - min(values)}


def probe(seeds: tuple[int, ...], coverage: float) -> dict:
    fits = [fit_one(seed, coverage) for seed in seeds]

    kinds = sorted(fits[0]["keyed_window_days"])
    missing = [
        fit["seed"] for fit in fits if sorted(fit["keyed_window_days"]) != kinds
    ]
    if missing:
        # A seed that produced no credit of some kind would make the keyed
        # spread a comparison across different populations. It has never
        # happened at these sizes and the run refuses rather than averaging
        # over whatever each seed happened to hold.
        raise SystemExit(
            f"seeds {missing} did not produce every credit kind, so the keyed "
            f"spread would compare {kinds} against something else"
        )

    pooled = [fit["pooled_window_days"] for fit in fits]
    maxima = [fit["observable_lag_days"]["max"] for fit in fits]
    keyed_spread = {
        kind: spread([fit["keyed_window_days"][kind] for fit in fits])
        for kind in kinds
    }

    # The question the threshold actually turns on. A window is only a usable
    # alert boundary if the thing it is compared against lies outside the range
    # the window itself occupies across seeds.
    widest_keyed = max(
        keyed_spread[kind]["max"] for kind in kinds
    )

    # The smallest candidate that held on every seed, and the ones that did not.
    # A window is reported as a bound only if nothing fell outside it anywhere,
    # which is the test that was applied to one seed and called a bound.
    outside_by_candidate = {
        str(window): {
            "worst_seed_credits_outside": max(
                fit["credits_outside_each_candidate_window"][str(window)]
                for fit in fits
            ),
            "seeds_with_credits_outside": [
                fit["seed"]
                for fit in fits
                if fit["credits_outside_each_candidate_window"][str(window)] > 0
            ],
        }
        for window in CANDIDATES
    }
    held_everywhere = [
        int(window)
        for window in map(str, CANDIDATES)
        if not outside_by_candidate[window]["seeds_with_credits_outside"]
    ]

    return {
        "candidate_windows": list(CANDIDATES),
        "credits_outside_each_candidate_window": outside_by_candidate,
        "smallest_candidate_that_held_on_every_seed": (
            min(held_everywhere) if held_everywhere else None
        ),
        "coverage_target_pct": round(coverage * 100, 2),
        "seeds": list(seeds),
        "fits": fits,
        "spread": {
            "pooled_window_days": spread(pooled),
            "keyed_window_days": keyed_spread,
            "largest_observable_lag_days": spread(maxima),
        },
        "widest_keyed_window_over_every_seed_days": widest_keyed,
        "largest_observable_lag_over_every_seed_days": max(maxima),
        "published_seed": SEEDS[0],
        "published_pooled_window_days": fits[0]["pooled_window_days"],
        "rebuilt_by": "python3 scripts/window_spread.py",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=len(SEEDS))
    parser.add_argument("--coverage", type=float, default=0.95)
    args = parser.parse_args(argv)

    if not 2 <= args.seeds <= len(SEEDS):
        raise SystemExit(
            f"--seeds has to be between 2 and {len(SEEDS)}, which is how many are "
            "written down. One seed is not a spread"
        )

    result = probe(SEEDS[: args.seeds], args.coverage)
    manifest.write(SPREAD_MEASUREMENT, result)

    print(f"{len(result['seeds'])} seeds at {result['coverage_target_pct']} percent coverage")
    print(f"{'seed':>10}  {'pooled':>7}  {'price_prot':>11}  {'return':>7}"
          f"  {'rebate':>7}  {'max lag':>8}")
    for fit in result["fits"]:
        keyed = fit["keyed_window_days"]
        print(
            f"{fit['seed']:>10}  {fit['pooled_window_days']:>7}"
            f"  {keyed['price_protection']:>11}  {keyed['return_credit']:>7}"
            f"  {keyed['volume_rebate']:>7}  {fit['observable_lag_days']['max']:>8}"
        )

    pooled = result["spread"]["pooled_window_days"]
    print(
        f"pooled window moves {pooled['range']} days over these seeds, "
        f"{pooled['min']} to {pooled['max']}"
    )
    for kind, band in result["spread"]["keyed_window_days"].items():
        print(f"  {kind:18s} {band['min']:>4} to {band['max']:>4}, range {band['range']}")
    lags = result["spread"]["largest_observable_lag_days"]
    print(
        f"largest observable lag moves {lags['range']} days, "
        f"{lags['min']} to {lags['max']}"
    )
    print(f"{'window':>8}  {'worst seed credits outside':>27}  seeds affected")
    for window in result["candidate_windows"]:
        entry = result["credits_outside_each_candidate_window"][str(window)]
        seeds = entry["seeds_with_credits_outside"]
        shown = ", ".join(str(seed) for seed in seeds) if seeds else "none"
        print(f"{window:>8}  {entry['worst_seed_credits_outside']:>27}  {shown}")
    held = result["smallest_candidate_that_held_on_every_seed"]
    print(
        f"smallest candidate that held on every seed: {held}"
        if held
        else "no candidate held on every seed"
    )
    print(f"wrote {SPREAD_MEASUREMENT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
