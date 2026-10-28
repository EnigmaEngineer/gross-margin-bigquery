"""Grades the credit allocation from the files and the measurements it commits.

Same split as the transform checks. One half reads the dbt project as text, so
it grades on a clone that has never built anything. The other half re-derives
the figures in `measurements/transform-shape.json` and
`measurements/allocation-fixture.json`, both written by scripts after a real
DuckDB build.

The arithmetic itself is not graded here. It is graded by
`scripts/allocation_fixture.py`, which runs the real models over a corpus small
enough to check against numbers worked out on paper. This module's job is to
make sure that harness ran, that its answer was what it should be, and that the
properties the models claim in prose are properties the committed result holds.
"""

from __future__ import annotations

import re
from pathlib import Path

from gross_margin import manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSFORM = REPO_ROOT / "transform"
ALLOCATION = TRANSFORM / "models" / "marts" / "fct_credit_allocation.sql"
ROLLUP = TRANSFORM / "models" / "marts" / "credit_rollup_by_line.sql"
MACRO = TRANSFORM / "macros" / "allocation.sql"
PROJECT = TRANSFORM / "dbt_project.yml"
SHAPE_FILE = REPO_ROOT / "measurements" / "transform-shape.json"
FIXTURE_FILE = REPO_ROOT / "measurements" / "allocation-fixture.json"
PROBE_FILE = REPO_ROOT / "measurements" / "lookback-anchors.json"

SCOPES = ("direct", "vendor_quarter", "invoice", "unallocated")


def _allocation() -> dict:
    return manifest.read(SHAPE_FILE)["allocation"]


def check_the_lookback_is_measured_against_the_arrival_date():
    """The whole design rests on this and it is one word in one predicate.

    A window on invoice_date reprocesses lines whose answer did not change and
    misses the credits whose answer did. Swapping the column is a one character
    edit that no column test would notice, so it is pinned as text.
    """
    body = ALLOCATION.read_text(encoding="utf-8")
    predicate = re.search(
        r"\{%\s*if is_incremental\(\)\s*%\}\s*(.+?)\s*\{%\s*endif\s*%\}",
        body,
        re.S,
    )
    if not predicate:
        raise AssertionError("the allocation model has no incremental branch")
    clause = predicate.group(1)
    if "posted_date" not in clause:
        raise AssertionError(f"the incremental filter does not read posted_date: {clause}")
    if "invoice_date" in clause:
        raise AssertionError(
            f"the incremental filter reads invoice_date, which is the anchor the "
            f"fixture exists to rule out: {clause}"
        )


def check_a_reprocessed_credit_loses_every_row_it_wrote():
    """delete and insert on the credit, never on the pair.

    Keying on the pair leaves behind any row whose scope shrank between runs, and
    a stale allocation row is money attributed to a line twice.
    """
    body = ALLOCATION.read_text(encoding="utf-8")
    if "unique_key='credit_id'" not in body.replace('"', "'"):
        raise AssertionError("the allocation model is not keyed on credit_id alone")
    if "incremental_strategy='delete+insert'" not in body.replace('"', "'"):
        raise AssertionError("the allocation model does not delete before inserting")


def check_every_scope_is_reachable_and_was_reached():
    """A branch nothing exercises is a branch nobody has graded."""
    by_scope = _allocation()["by_scope"]
    missing = [scope for scope in SCOPES if scope not in by_scope]
    if missing:
        raise AssertionError(f"these scopes produced no rows at all: {missing}")
    empty = [scope for scope in SCOPES if by_scope[scope]["rows"] == 0]
    if empty:
        raise AssertionError(f"these scopes are declared and hold nothing: {empty}")


def check_every_credit_allocates_to_its_own_total():
    """The one invariant the table is worth nothing without."""
    off_by = _allocation()["credits_whose_parts_do_not_sum_to_the_credit"]
    if off_by != 0:
        raise AssertionError(
            f"{off_by} credits do not sum back to themselves across their parts"
        )


def check_the_allocated_total_is_the_named_total_plus_what_it_placed():
    """The two margins differ by exactly the credits allocation could place.

    Named credits are the ones sitting on a line this system holds. Allocation
    reaches those and also the ones that quote an invoice we hold and a line we
    do not. So the gap between the two margins has to be the invoice scope, to
    the cent, and anything else means a credit is being counted twice or lost.
    """
    allocation = _allocation()
    margin = allocation["margin_cents"]
    placed = allocation["by_scope"]["invoice"]["cents"]
    gap = margin["after_allocated_credits"] - margin["after_named_credits"]
    if gap != placed:
        raise AssertionError(
            f"the two margins differ by {gap} cents and the invoice scope placed "
            f"{placed}. They have to be the same number"
        )


def check_no_money_column_is_a_float():
    """DuckDB's division operator returns a float whatever it is handed.

    That is how the allocated margin shipped as a DOUBLE for the length of one
    build. The column list is re-derived from the warehouse by
    scripts/run_transform.py so this grades the real types rather than the
    intent.
    """
    floats = _allocation()["money_columns_that_are_not_exact"]
    if floats:
        names = ", ".join(f"{entry['column']} is {entry['type']}" for entry in floats)
        raise AssertionError(f"money columns that are not exact: {names}")


def check_the_hand_computed_fixture_ran_and_covered_the_three_runs():
    """A measurement file nobody wrote is the same as a check nobody ran."""
    fixture = manifest.read(FIXTURE_FILE)
    runs = fixture["runs"]
    if len(runs) != 3:
        raise AssertionError(f"the fixture recorded {len(runs)} runs rather than three")
    late = runs[1]
    if not late.get("picked_up"):
        raise AssertionError("the fixture did not pick up its late credit")
    if not late.get("invoice_date_anchor_would_have_missed_it"):
        raise AssertionError(
            "the fixture's late credit does not separate the two anchors, so it "
            "proves nothing about the one the model uses"
        )
    backdated = runs[2]
    if backdated.get("picked_up"):
        raise AssertionError(
            "the fixture claims the backdated credit was picked up, which would "
            "mean the window is not filtering anything"
        )
    if not backdated.get("recovered_by_full_refresh"):
        raise AssertionError("a full refresh did not recover the backdated credit")


def check_the_fixture_was_run_against_the_declared_window():
    """The fixture's conclusions are only about the window the project declares."""
    declared = re.search(
        r"^\s*allocation_lookback_days:\s*(\d+)\s*$",
        PROJECT.read_text(encoding="utf-8"),
        re.M,
    )
    if not declared:
        raise AssertionError("dbt_project.yml declares no allocation_lookback_days")
    recorded = manifest.read(FIXTURE_FILE)["lookback_days"]
    if int(declared.group(1)) != recorded:
        raise AssertionError(
            f"the project declares {declared.group(1)} days and the fixture was "
            f"run at {recorded}"
        )


def check_the_anchor_probe_says_what_its_zero_means():
    """A column that is zero by construction has to say so where it is read.

    The posted_date anchor misses nothing in this corpus because arrival order
    is posting order, which is a property of the generator. Publishing that zero
    without the reason beside it is the fire rate that was zero by construction.
    """
    probe = manifest.read(PROBE_FILE)
    for entry in probe["by_window"]:
        posted = entry["posted_date_anchor"]
        if posted["credits_missed"] == 0 and not posted.get("why"):
            raise AssertionError(
                f"the {entry['window_days']} day window reports no posted anchor "
                "misses and gives no reason"
            )


def check_the_probe_shows_the_two_anchors_disagreeing():
    """If both anchors answered the same everywhere, the model's choice is moot."""
    probe = manifest.read(PROBE_FILE)
    disagreeing = [
        entry for entry in probe["by_window"]
        if entry["invoice_date_anchor"]["credits_missed"]
        != entry["posted_date_anchor"]["credits_missed"]
    ]
    if not disagreeing:
        raise AssertionError(
            "no window in the probe separates the two anchors, so nothing "
            "measured here supports the one the model uses"
        )


def check_the_marts_read_the_rollup_and_not_the_credit_facts():
    """One rollup, so three marts cannot drift apart on the same group by.

    The dependencies are the ref calls and not every mention of a name. The
    first version matched the whole file and failed on a comment explaining why
    the rollup is there, which would have been fixed by deleting the
    explanation.
    """
    for name in ("margin_by_vendor", "margin_by_partner", "margin_by_product_line"):
        body = (TRANSFORM / "models" / "marts" / f"{name}.sql").read_text(
            encoding="utf-8"
        )
        refs = set(re.findall(r"ref\(\s*['\"]([\w]+)['\"]\s*\)", body))
        leaked = refs & {"fct_credit_memo", "fct_credit_allocation"}
        if leaked:
            raise AssertionError(
                f"{name} selects from {sorted(leaked)} directly. The rollup exists "
                "so that the credit aggregation lives in one place"
            )
        if "credit_rollup_by_line" not in refs:
            raise AssertionError(f"{name} does not select from the credit rollup")


def check_the_rebate_scope_reaches_more_than_the_line_it_quotes():
    """The point of the vendor_quarter scope, stated as a number.

    If a rebate only ever reached its own line the allocation would be an
    expensive copy of the credit fact.
    """
    rebate = _allocation()["by_scope"]["vendor_quarter"]
    if rebate["rows"] <= rebate["credits"]:
        raise AssertionError(
            f"{rebate['credits']} rebates produced {rebate['rows']} rows, so none "
            "of them spread past the line they quote"
        )


def check_the_adapter_split_stays_in_the_macro():
    """No warehouse specific spelling in the allocation model itself."""
    body = ALLOCATION.read_text(encoding="utf-8") + ROLLUP.read_text(encoding="utf-8")
    for spelling in ("decimal(38", "int64", "date_sub", "numeric)", "varchar"):
        if spelling in body.lower():
            raise AssertionError(
                f"{spelling!r} is written into a model. It belongs in "
                f"{MACRO.name}, which is the only file that knows the target"
            )


def _readme_flat() -> str:
    return " ".join((REPO_ROOT / "README.md").read_text(encoding="utf-8").split())


def check_the_readme_lookback_table_comes_from_the_probe():
    """Four rows of a table nothing else re-derives.

    The allocation figures are graded next door against the shape measurement.
    These come from a different file and were just as typed, so they get the
    same treatment.
    """
    probe = manifest.read(PROBE_FILE)
    by_window = {entry["window_days"]: entry for entry in probe["by_window"]}
    text = _readme_flat()
    missing = []
    for window in (30, 90, 105, 180):
        if window not in by_window:
            raise AssertionError(f"the probe recorded no {window} day window")
        anchor = by_window[window]["invoice_date_anchor"]
        row = (
            f"| {window} days | {anchor['credits_missed']:,} | "
            f"{anchor['allocation_rows_left_wrong']:,} |"
        )
        if row not in text:
            missing.append(row)
    if missing:
        raise AssertionError(f"the README lookback table does not carry: {missing}")
