"""Every number the README states about itself, read back out of the measurement.

A figure in prose has no address and cannot be graded, so each one here is built
from `measurements/credit-lag.json` and then looked for in the file as an exact
string. That way the README cannot drift from the measurement without this failing,
and nobody has to remember which sentences carry numbers.

The table rows are parsed and compared cell by cell rather than searched for, since
a table is the one place in a document where a figure does have an address.
"""

from __future__ import annotations

import re
from pathlib import Path

from gross_margin import manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
LAG_FILE = REPO_ROOT / "measurements" / "credit-lag.json"


def _text() -> str:
    return README.read_text(encoding="utf-8")


def _flat() -> str:
    """The README with every run of whitespace collapsed to one space.

    This file is hard wrapped, so a sentence carrying a figure is usually split
    across a newline and a search for it on one line finds nothing. The first
    version of this module searched the raw text and reported a figure as missing
    that was sitting there in two pieces.
    """
    return " ".join(README.read_text(encoding="utf-8").split())


def _published() -> dict:
    return manifest.read(LAG_FILE)


def check_every_scalar_the_readme_states_comes_from_the_measurement():
    published = _published()
    window = published["window"]
    rows = published["corpus"]["rows"]
    expected = {
        "credit memo count": f"{rows['credit_memos.csv']:,} credit memos",
        "measurable credits": f"{published['credits']:,} of those credits",
        "pooled window": f"{window['pooled_window_days']} days at a target",
        "under covered share": f"{window['coverage_at_the_pooled_window']['volume_rebate']['coverage_pct']} percent on volume rebates",
        "avoidable wait": f"{window['avoidable_wait_credit_days']:,} credit days",
        "shortest window": f"price protection settles in {window['keyed_window_days']['price_protection']}",
        "held past a weekend": f"{published['credits_held_past_a_weekend']:,} credits were held",
        "censored": f"{published['corpus']['censored_credits']:,} credits were scheduled",
    }
    text = _flat()
    missing = {name: fragment for name, fragment in expected.items() if fragment not in text}
    if missing:
        raise AssertionError(f"the README does not carry these derived figures: {missing}")
    if len(expected) < 8:
        raise AssertionError("the expected figure set shrank, so this check grades less")


def check_the_lag_quantile_table_matches_the_measurement():
    published = _published()
    observable = published["observable_lag_days"]
    raise_lag = published["raise_lag_days"]
    calendar_lag = published["calendar_lag_days"]
    wanted = {
        "observable lag, median": observable["p50"],
        "observable lag, ninety fifth centile": observable["p95"],
        "observable lag, largest seen": observable["max"],
        "vendor raise lag, median": raise_lag["p50"],
        "calendar hold, ninety fifth centile": calendar_lag["p95"],
    }
    found = dict(re.findall(r"^\| ([a-z][^|]*?) \| (\d+) \|$", _text(), re.MULTILINE))
    for label, value in wanted.items():
        if label not in found:
            raise AssertionError(f"the README has no row labelled {label!r}")
        if int(found[label]) != value:
            raise AssertionError(
                f"the README says {label} is {found[label]} and the measurement says {value}"
            )


def check_the_per_kind_table_matches_the_measurement():
    published = _published()
    window = published["window"]
    pattern = r"^\| (price_protection|return_credit|volume_rebate) \| (\d+) \| (\d+) \| ([\d.]+) \|$"
    rows = re.findall(pattern, _text(), re.MULTILINE)
    if len(rows) != len(window["keyed_window_days"]):
        raise AssertionError(
            f"the README table holds {len(rows)} credit kinds and the measurement "
            f"holds {len(window['keyed_window_days'])}"
        )
    for kind, credits, days, coverage in rows:
        at_pooled = window["coverage_at_the_pooled_window"][kind]
        if int(credits) != at_pooled["credits"]:
            raise AssertionError(f"{kind} credit count is {credits}, measured {at_pooled['credits']}")
        if int(days) != window["keyed_window_days"][kind]:
            raise AssertionError(
                f"{kind} window is {days}, measured {window['keyed_window_days'][kind]}"
            )
        if float(coverage) != at_pooled["coverage_pct"]:
            raise AssertionError(
                f"{kind} coverage is {coverage}, measured {at_pooled['coverage_pct']}"
            )


def check_the_injected_discrepancy_table_matches_what_landed():
    achieved = _published()["corpus"]["injected"]["achieved"]
    # Three cells exactly. The per kind table above has four, and a pattern that
    # only anchored the first two matched both tables and reported the union.
    rows = dict(
        (name, count)
        for name, count, _rest in re.findall(
            r"^\| ([a-z_]+) \| (\d+) \| ([^|]+) \|$", _text(), re.MULTILINE
        )
    )
    if sorted(rows) != sorted(achieved):
        raise AssertionError(
            f"the README lists discrepancies {sorted(rows)} and the measurement "
            f"records {sorted(achieved)}"
        )
    for name, count in rows.items():
        if int(count) != achieved[name]:
            raise AssertionError(f"{name} is {count} in the README and {achieved[name]} measured")


def check_the_readme_never_claims_the_infrastructure_was_applied():
    """The stack line is the easiest overclaim to miss, so it is graded.

    Nothing here has run against BigQuery. A sentence saying otherwise is the one
    thing in this file a reader could catch in an interview.
    """
    text = _flat().lower()
    forbidden = (
        "terraform apply",
        "deployed to",
        "running in production",
        "in production on",
        "queries run against bigquery",
    )
    hits = [phrase for phrase in forbidden if phrase in text]
    if hits:
        raise AssertionError(f"the README claims the infrastructure is live: {hits}")
    for required in (
        "have not been applied",
        "no bigquery statement in",
    ):
        if required not in text:
            raise AssertionError(
                f"the README no longer states {required!r}, so a reader cannot tell "
                "that the cloud side is unapplied"
            )


def check_the_readme_says_the_distribution_is_censored():
    """The tail is a lower bound and the limitations section has to say so."""
    text = _flat().lower()
    if "censored" not in text:
        raise AssertionError(
            "the README publishes a largest observed lag and never says the corpus "
            "is right censored, so the figure reads as the whole truth"
        )
