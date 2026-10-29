"""The reconciliation's published figures, re-derived from the measurements.

These read the committed measurement files rather than a warehouse, so they grade
on a clone that has never built anything. The arithmetic they check is the
arithmetic the README quotes, which is the part that goes stale.

The check worth reading first is
`check_the_reopen_window_sits_above_the_worst_quantile_over_every_seed`. It is the
one that makes the window's justification machine checked. The earlier window was
chosen and its argument written into a comment, and the argument turned out to
rest on a figure that does not hold across seeds. A comment cannot fail. This can.
"""

from __future__ import annotations

import re
from pathlib import Path

from gross_margin import manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
RECON_FILE = REPO_ROOT / "measurements" / "recon-by-period.json"
SPREAD_FILE = REPO_ROOT / "measurements" / "window-spread.json"
FIXTURE_FILE = REPO_ROOT / "measurements" / "recon-fixture.json"
PROJECT_FILE = REPO_ROOT / "transform" / "dbt_project.yml"


def _recon() -> dict:
    return manifest.read(RECON_FILE)


def _spread() -> dict:
    return manifest.read(SPREAD_FILE)


def declared_reopen_days() -> int:
    text = PROJECT_FILE.read_text(encoding="utf-8")
    found = re.search(r"^\s*recon_reopen_days:\s*(\d+)\s*$", text, re.M)
    if found is None:
        raise AssertionError("dbt_project.yml does not declare recon_reopen_days")
    return int(found.group(1))


def check_the_reopen_window_sits_above_the_worst_quantile_over_every_seed():
    """The window is derived from the 99th centile because that is the part of the
    arrival distribution that holds still. This asserts the derivation rather
    than the number, so changing the var without re-measuring fails here."""
    spread = _spread()
    worst = max(fit["observable_lag_days"]["p99"] for fit in spread["fits"])
    declared = declared_reopen_days()
    if declared <= worst:
        raise AssertionError(
            f"recon_reopen_days is {declared} and the worst 99th centile over "
            f"{len(spread['fits'])} seeds is {worst}. A window at or inside its own "
            "input is not a window"
        )


def check_the_window_is_not_published_as_a_bound():
    """The finding from the seed refit, asserted so it cannot be quietly reversed.

    If some later seed set made every candidate hold everywhere, the honest
    conclusion would be that the tail had been bounded by measurement, and this
    check would be the place that argument has to be made rather than assumed.
    """
    spread = _spread()
    declared = declared_reopen_days()
    worst_outside = max(
        fit["credits_outside_each_candidate_window"][str(window)]
        for fit in spread["fits"]
        for window in spread["candidate_windows"]
        if window <= declared
    )
    if worst_outside == 0:
        raise AssertionError(
            "no credit falls outside any candidate at or below the declared window "
            "on any seed, so the alert table can never fire and the claim that the "
            "tail is unbounded needs re-arguing"
        )


def check_the_largest_arrival_moves_more_than_the_quantile_does():
    """The reason the window comes from a centile and not from a maximum. This is
    the seed refit finding and it is asserted rather than described."""
    spread = _spread()
    quantile = spread["spread"]["pooled_window_days"]["range"]
    largest = spread["spread"]["largest_observable_lag_days"]["range"]
    if largest <= quantile:
        raise AssertionError(
            f"the largest arrival moves {largest} days over these seeds and the "
            f"pooled window moves {quantile}. The README argues the opposite"
        )


def check_a_single_seed_is_refused_as_a_spread():
    from scripts.window_spread import main as spread_main

    try:
        spread_main(["--seeds", "1"])
    except SystemExit as stop:
        if stop.code in (0, None):
            raise AssertionError("one seed was accepted as a spread")
        return
    raise AssertionError("one seed was accepted as a spread")


def check_the_variance_splits_exactly_in_every_published_row():
    """The same identity the dbt test asserts in the warehouse, re-derived here
    from the committed file so a clone grades it with nothing built."""
    offenders = [
        (row["period"], row["account"])
        for row in _recon()["by_period"]
        if row["variance_cents"] != row["period_shift_cents"] + row["break_cents"]
    ]
    if offenders:
        raise AssertionError(f"the split does not add up on {offenders[:5]}")


def check_the_period_shift_nets_to_zero_in_the_published_measurement():
    recon = _recon()
    total = sum(row["period_shift_cents"] for row in recon["by_period"])
    if total != 0:
        raise AssertionError(
            f"the period shift sums to {total} cents. Moving a document between "
            "two periods cannot create money, so this is a defect and not a figure"
        )
    if recon["signed_cents"]["period_shift"] != 0:
        raise AssertionError(
            f"the measurement reports a signed period shift of "
            f"{recon['signed_cents']['period_shift']} cents"
        )


def check_the_published_totals_re_derive_from_the_rows():
    recon = _recon()
    rows = recon["by_period"]
    for key, column in (
        ("variance", "variance_cents"),
        ("period_shift", "period_shift_cents"),
        ("break", "break_cents"),
    ):
        want = sum(row[column] for row in rows)
        if recon["signed_cents"][key] != want:
            raise AssertionError(
                f"signed {key} is published as {recon['signed_cents'][key]} and the "
                f"rows sum to {want}"
            )
        absolute = sum(abs(row[column]) for row in rows)
        if recon["absolute_cents"][key] != absolute:
            raise AssertionError(
                f"absolute {key} is published as {recon['absolute_cents'][key]} and "
                f"the rows sum to {absolute}"
            )


def check_the_headline_share_re_derives():
    """The one figure in the README a reader is most likely to quote back."""
    recon = _recon()
    absolute = recon["absolute_cents"]
    want = round(100.0 * absolute["period_shift"] / absolute["variance"], 2)
    got = recon["share_of_the_gross_variance_that_is_a_period_shift_pct"]
    if got != want:
        raise AssertionError(f"the share is published as {got} and re-derives as {want}")


def check_the_break_terms_add_up_to_the_break():
    recon = _recon()
    terms = recon["break_terms_cents"]
    want = terms["unposted"] + terms["amount_difference"] - terms["ledger_only"]
    if recon["signed_cents"]["break"] != want:
        raise AssertionError(
            f"the break is {recon['signed_cents']['break']} cents and its three "
            f"terms come to {want}"
        )


def check_every_alert_is_outside_the_declared_window():
    declared = declared_reopen_days()
    for alert in _recon()["alerts"]:
        if alert["observable_lag_days"] <= declared:
            raise AssertionError(
                f"{alert['credit_id']} is {alert['observable_lag_days']} days out and "
                f"the window is {declared}, so it should not be in the alert table"
            )
        if alert["reopen_window_days"] != declared:
            raise AssertionError(
                f"{alert['credit_id']} was measured against a window of "
                f"{alert['reopen_window_days']} and the project declares {declared}"
            )


def check_the_fixture_pins_the_case_the_generated_corpus_does_not_hold():
    """The fixture is the only place a document is both in the wrong period and at
    the wrong amount. If its figures stopped covering that, the model's split
    would be ungraded everywhere and every test would still pass."""
    fixture = manifest.read(FIXTURE_FILE)
    january = fixture["terms"].get("2025-01 5000-cost-of-goods")
    if january is None:
        raise AssertionError(
            "the fixture no longer publishes the January cost row, which is the "
            "only row in this repository where the two defects meet"
        )
    if january["amount_difference_cents"] == 0:
        raise AssertionError(
            "the fixture's January cost row carries no amount difference, so the "
            "case that separated the period shift from the break is gone"
        )
    if january["awaiting_the_ledger_cents"] == january["amount_difference_cents"]:
        raise AssertionError(
            "the fixture's two terms are equal, which means the row no longer "
            "distinguishes the two formulations"
        )
    if fixture["period_shift_cents_over_the_fixture"] != 0:
        raise AssertionError(
            "the fixture's period shift does not net to zero, which is the "
            "invariant that caught the first formulation"
        )


def check_the_match_classes_account_for_every_document():
    recon = _recon()
    classes = recon["documents_by_match_class"]
    known = {
        "matched_same_period",
        "matched_ledger_is_later",
        "matched_ledger_is_earlier",
        "in_billing_not_in_ledger",
        "in_ledger_not_in_billing",
    }
    unknown = sorted(set(classes) - known)
    if unknown:
        raise AssertionError(f"the reconciliation produced match classes {unknown}")
    if classes.get("in_billing_not_in_ledger", 0) == 0:
        raise AssertionError(
            "no document is in billing and absent from the ledger, so the unposted "
            "term is reasoned about and never exercised"
        )
    if classes.get("in_ledger_not_in_billing", 0) == 0:
        raise AssertionError("no orphan ledger entry, so that term is never exercised")
    if classes.get("matched_ledger_is_later", 0) == 0:
        raise AssertionError("nothing posts late, so the period shift is never exercised")


def check_the_measurement_names_the_command_that_rebuilds_it():
    for path in (RECON_FILE, SPREAD_FILE, FIXTURE_FILE):
        payload = manifest.read(path)
        if not payload.get("rebuilt_by", "").startswith("python3 scripts/"):
            raise AssertionError(f"{path.name} does not say how to rebuild it")


def _readme_flat() -> str:
    """The README with whitespace collapsed, for the reason the other two README
    modules give. The file is hard wrapped and a figure usually straddles a
    newline."""
    return " ".join((REPO_ROOT / "README.md").read_text(encoding="utf-8").split())


def check_the_readme_reconciliation_figures_come_from_the_measurement():
    """Every reconciliation figure in the README, rebuilt from the file and then
    looked for as an exact string. Typed figures are what this repository has
    already been wrong about twice."""
    recon = _recon()
    absolute = recon["absolute_cents"]
    terms = recon["break_terms_cents"]

    def money(cents: int) -> str:
        return f"{cents / 100:,.2f}"

    expected = {
        "period and account rows": f"{recon['periods_and_accounts']} period and account rows",
        "gross variance": f"${money(absolute['variance'])}",
        "period shift": f"${money(absolute['period_shift'])} is period shift",
        "break": f"${money(absolute['break'])} is a break",
        "the share": (
            f"{recon['share_of_the_gross_variance_that_is_a_period_shift_pct']} percent "
            "of the gross variance resolves"
        ),
        "rows with a variance": (
            f"{recon['rows_with_a_variance']} rows carry a variance and "
            f"{recon['rows_with_a_break']} carry a break"
        ),
        "unposted": f"${money(terms['unposted'])} billing raised",
        "orphaned": f"${money(terms['ledger_only'])} posted with no billing document",
        "keyed wrong": f"-${money(abs(terms['amount_difference']))} keyed at",
    }
    text = _readme_flat()
    missing = {name: want for name, want in expected.items() if want not in text}
    if missing:
        raise AssertionError(f"the README does not carry these figures: {missing}")


def check_the_readme_alert_row_matches_the_alert_table():
    alerts = _recon()["alerts"]
    if not alerts:
        raise AssertionError(
            "the alert table is empty and the README describes a row in it"
        )
    alert = alerts[0]
    text = _readme_flat()
    expected = {
        "the credit": alert["credit_id"],
        "its amount": f"${alert['amount_cents'] / 100:,.2f}",
        "its lag": f"{alert['observable_lag_days']} days after its invoice",
        "its margin": f"{alert['days_past_the_window']} days past the window",
        "the stale period": alert["stale_period"],
        "where the ledger put it": alert["posted_in_period"],
    }
    missing = {name: want for name, want in expected.items() if want not in text}
    if missing:
        raise AssertionError(f"the README's alert sentence is stale: {missing}")


def check_the_readme_seed_table_matches_the_refit():
    """The table has an address, so it is compared cell by cell."""
    spread = _spread()
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for fit in spread["fits"]:
        keyed = fit["keyed_window_days"]
        row = (
            f"| {fit['seed']} | {fit['pooled_window_days']} | "
            f"{keyed['price_protection']} | {keyed['return_credit']} | "
            f"{keyed['volume_rebate']} | {fit['observable_lag_days']['max']} |"
        )
        if row not in text:
            raise AssertionError(f"the seed table has no row reading {row!r}")


def check_the_readme_states_the_spread_the_window_argument_rests_on():
    spread = _spread()
    pooled = spread["spread"]["pooled_window_days"]
    largest = spread["spread"]["largest_observable_lag_days"]
    p99 = [fit["observable_lag_days"]["p99"] for fit in spread["fits"]]
    text = _readme_flat()
    expected = {
        "the pooled range": f"moves {pooled['range']} days across those",
        "the p99 range": f"the 99th moves {max(p99) - min(p99)} days, from {min(p99)} to {max(p99)}",
        "the largest arrival range": (
            f"largest arrival moves {largest['range']} days, from {largest['min']} "
            f"to {largest['max']}"
        ),
        "the declared window": f"`recon_reopen_days` is {declared_reopen_days()}",
        "the margin above the centile": f"{declared_reopen_days() - max(p99)} days above the worst",
    }
    missing = {name: want for name, want in expected.items() if want not in text}
    if missing:
        raise AssertionError(f"the README does not carry these figures: {missing}")
