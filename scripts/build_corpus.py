"""Builds the corpus and writes the credit lag measurement.

Two modes. The default generates the corpus into `--out` and measures it. With
`--measure-only` it reads a corpus that is already there and rewrites the
measurement without regenerating. The second mode refuses when the corpus is
absent rather than reporting nothing, because a build that reports nothing looks
the same as a build that found nothing wrong.

    python3 scripts/build_corpus.py
    python3 scripts/build_corpus.py --measure-only
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import discrepancies, ledger, lag, manifest
from gross_margin.generate import build, write
from gross_margin.registry import ARTIFACTS, MANIFEST

LAG_MEASUREMENT = REPO_ROOT / "measurements" / "credit-lag.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "data"), help="corpus directory")
    parser.add_argument("--seed", type=int, default=20260428)
    parser.add_argument(
        "--no-defects",
        action="store_true",
        help="generate the clean corpus with nothing injected",
    )
    parser.add_argument(
        "--measure-only",
        action="store_true",
        help="read an existing corpus and rewrite the measurement",
    )
    parser.add_argument("--coverage", type=float, default=0.95)
    return parser


def load_table(out_dir: Path, name: str) -> list[dict]:
    path = out_dir / name
    if not path.exists():
        raise SystemExit(
            f"{path} is absent. Run this without --measure-only to build the corpus first"
        )
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    expected = list(ARTIFACTS[name])
    if rows and list(rows[0]) != expected:
        raise SystemExit(f"{path} header is {list(rows[0])}, expected {expected}")
    return rows


def measure(invoices: list[dict], credits: list[dict], coverage: float) -> dict:
    records, unmatched = lag.lag_records(invoices, credits)
    if not records:
        raise SystemExit("no credit matched an invoice, so there is nothing to measure")
    payload = lag.summarise(records)
    payload["credits_with_no_matching_invoice"] = unmatched
    payload["window"] = lag.keyed_against_pooled(records, coverage)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out)

    if args.measure_only:
        invoices = load_table(out_dir, "invoices.csv")
        credits = load_table(out_dir, "credit_memos.csv")
        corpus_manifest = json.loads((out_dir / MANIFEST).read_text(encoding="utf-8"))
        print(f"read {len(invoices)} invoices and {len(credits)} credits from {out_dir}")
    else:
        corpus = build(seed=args.seed)
        if args.no_defects:
            corpus.defects = {"requested": {}, "achieved": {}}
            ledger.post(corpus)
            corpus.gl_defects = {"requested": {}, "achieved": {}}
        else:
            discrepancies.apply(corpus)
            # Posted from billing as it now stands, then broken. Order matters
            # both ways round and the reasons are in gross_margin/ledger.py.
            ledger.post(corpus)
            ledger.inject(corpus)
        digests = write(corpus, out_dir)
        corpus_manifest = json.loads((out_dir / MANIFEST).read_text(encoding="utf-8"))
        invoices, credits = corpus.invoices, corpus.credits
        for name in sorted(ARTIFACTS):
            print(f"wrote {name:18s} {corpus_manifest['rows'][name]:>7d} rows  {digests[name][:12]}")
        print(f"wrote {MANIFEST}")
        print(f"injected {corpus.defects['achieved']}")
        print(f"injected into the ledger {corpus.gl_defects['achieved']}")
        print(f"censored credits past the window end: {corpus.censored_credits}")

    payload = measure(invoices, credits, args.coverage)
    payload["corpus"] = {
        "seed": corpus_manifest["seed"],
        "window": corpus_manifest["window"],
        "rows": corpus_manifest["rows"],
        "censored_credits": corpus_manifest["censored_credits"],
        "injected": corpus_manifest["injected"],
        "injected_into_the_ledger": corpus_manifest["injected_into_the_ledger"],
    }
    payload["rebuilt_by"] = "python3 scripts/build_corpus.py"
    manifest.write(LAG_MEASUREMENT, payload)

    window = payload["window"]
    print(f"wrote {LAG_MEASUREMENT.relative_to(REPO_ROOT)}")
    print(
        f"pooled window {window['pooled_window_days']} days at "
        f"{window['coverage_target_pct']} percent, keyed {window['keyed_window_days']}"
    )
    if window["types_under_covered_by_the_pooled_window"]:
        print(
            "under covered by the pooled window: "
            + ", ".join(window["types_under_covered_by_the_pooled_window"])
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
