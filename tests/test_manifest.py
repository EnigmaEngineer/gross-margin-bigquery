"""The committed measurement file, re-derived rather than trusted.

Two rules meet here. A figure this repo publishes has to reproduce on another
machine, and a count reproduces exactly or it is broken. So this module rebuilds
the corpus at the seed the file records and compares every figure with no
tolerance at all. There is no timing in the file, which is the other half of the
rule and is enforced by the writer.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from gross_margin import discrepancies, ledger, lag, manifest
from gross_margin.generate import build

REPO_ROOT = Path(__file__).resolve().parent.parent
LAG_FILE = REPO_ROOT / "measurements" / "credit-lag.json"


def _published() -> dict:
    return manifest.read(LAG_FILE)


def check_the_writer_refuses_an_elapsed_time_at_any_depth():
    payload = {"credits": 3, "window": {"build_seconds": 1.2}}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            manifest.write(Path(tmp) / "x.json", payload)
        except ValueError as err:
            if "window.build_seconds" not in str(err):
                raise AssertionError(f"the refusal does not name the path: {err}")
            if (Path(tmp) / "x.json").exists():
                raise AssertionError("the file was written before the refusal")
            return
    raise AssertionError("a payload carrying an elapsed time was written")


def check_a_whole_number_of_days_is_not_mistaken_for_an_elapsed_time():
    """The rule has to let the thing this project actually measures through.

    A lag in days is a property of the calendar and it reproduces exactly. A rule
    that refused it would be refused itself within a day.
    """
    payload = {"observable_lag_days": {"p95": 105}, "avoidable_wait_credit_days": 7}
    if manifest.wall_time_keys(payload):
        raise AssertionError(f"a day count read as a time: {manifest.wall_time_keys(payload)}")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.json"
        manifest.write(path, payload)
        if manifest.read(path) != payload:
            raise AssertionError("the payload did not survive a round trip")


def check_the_committed_measurement_carries_no_elapsed_time():
    offenders = manifest.wall_time_keys(_published())
    if offenders:
        raise AssertionError(f"{LAG_FILE.name} publishes {offenders}")


def check_the_committed_measurement_names_the_command_that_rebuilds_it():
    """The input is gitignored, so the command is the only provenance available."""
    published = _published()
    command = published.get("rebuilt_by", "")
    script = REPO_ROOT / "scripts" / "build_corpus.py"
    if "build_corpus.py" not in command:
        raise AssertionError(f"rebuilt_by is {command!r} and names no script")
    if not script.exists():
        raise AssertionError(f"rebuilt_by names {command} and {script} is not there")


def check_every_published_figure_re_derives_exactly():
    """Rebuilt at the recorded seed and compared with no tolerance band.

    A count has no tolerance. If any figure here moves, either the generator
    changed or the file is stale, and both of those are the same problem for
    anybody reading the README.
    """
    published = _published()
    corpus = build(seed=published["corpus"]["seed"])
    requested = published["corpus"]["injected"]["requested"]
    discrepancies.apply(corpus, requested)
    # The ledger is posted from billing after the discrepancy pass and broken
    # after that, so a re-derivation that skipped it would report every ledger
    # row as missing and name the generator as the cause.
    ledger.post(corpus)
    ledger.inject(corpus, published["corpus"]["injected_into_the_ledger"]["requested"])

    records, unmatched = lag.lag_records(corpus.invoices, corpus.credits)
    rebuilt = lag.summarise(records)
    rebuilt["credits_with_no_matching_invoice"] = unmatched
    rebuilt["window"] = lag.keyed_against_pooled(
        records, published["window"]["coverage_target_pct"] / 100.0
    )
    rebuilt["corpus"] = {
        "seed": published["corpus"]["seed"],
        "window": published["corpus"]["window"],
        "rows": {name: len(rows) for name, rows in corpus.tables().items()},
        "censored_credits": corpus.censored_credits,
        "injected": corpus.defects,
        "injected_into_the_ledger": corpus.gl_defects,
    }
    rebuilt["rebuilt_by"] = published["rebuilt_by"]

    if rebuilt != published:
        left = json.dumps(published, indent=1, sort_keys=True).splitlines()
        right = json.dumps(rebuilt, indent=1, sort_keys=True).splitlines()
        moved = [
            f"published {a.strip()} rebuilt {b.strip()}"
            for a, b in zip(left, right)
            if a != b
        ]
        raise AssertionError(
            f"{LAG_FILE.name} does not re-derive, {len(moved)} lines differ, "
            f"first few {moved[:4]}"
        )
