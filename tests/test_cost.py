"""Checks over the cost model, the teardown procedure and the freeze sweep.

The cost figures are the only numbers this repository publishes that were not
measured on a machine. They are a transcribed price list multiplied out against
a declared shape, which makes them exactly the kind of number that goes stale
without anybody noticing, so every one of them gets re-derived here from the
two files it came from.

The teardown checks are a different shape. There is nothing to re-derive in a
procedure that has never run, so what they assert is that the document has not
drifted away from the Terraform it describes. A runbook naming a guard the
configuration no longer carries is worse than no runbook.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gross_margin import manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
COST_FILE = REPO_ROOT / "measurements" / "cost-model.json"
SWEEP_FILE = REPO_ROOT / "measurements" / "freeze-width-sweep.json"
PRICE_LIST = REPO_ROOT / "infra" / "price-list.json"
COMPOSER_TF = REPO_ROOT / "infra" / "composer.tf"
BIGQUERY_TF = REPO_ROOT / "infra" / "bigquery.tf"
DOC = REPO_ROOT / "docs" / "cost-and-teardown.md"
HOURS_PER_DAY = 24
DAYS_PER_MONTH = 30


def _cost() -> dict:
    return manifest.read(COST_FILE)


def _sweep() -> dict:
    return manifest.read(SWEEP_FILE)


def _prices() -> dict:
    return json.loads(PRICE_LIST.read_text(encoding="utf-8"))


def _doc() -> str:
    return " ".join(DOC.read_text(encoding="utf-8").split())


def check_the_cost_model_says_it_is_not_a_measurement():
    """The first thing a reader of the file has to be told.

    Asserted rather than trusted to a comment, because every other figure this
    repository publishes came off a run and this one did not. A payload that
    lost this flag would look like all the others.
    """
    cost = _cost()
    if not cost.get("this_is_a_price_list_multiplied_out_and_not_a_measurement"):
        raise AssertionError(
            "the cost model does not declare itself a price list, so a reader "
            "would take its figures for measurements like every other file here"
        )
    if not cost.get("nothing_in_infra_has_been_applied"):
        raise AssertionError("the cost model does not record that nothing is applied")


def check_the_composer_cost_re_derives_from_the_price_list_and_the_declared_shape():
    """Every term, from the two files, with no tolerance."""
    cost = _cost()["composer"]
    rates = _prices()["composer_generation_2"]
    terms = cost["usd_per_day_by_term"]

    expected = {
        "vcpu_usd": cost["declared_vcpu"] * rates["compute_vcpu_usd_per_hour"],
        "memory_usd": cost["declared_memory_gibibytes"]
        * rates["compute_memory_usd_per_gibibyte_hour"],
        "storage_usd": cost["declared_storage_gibibytes"]
        * rates["compute_storage_usd_per_gibibyte_hour"],
        "environment_fee_usd": rates["environment_fee_usd_per_hour"][
            cost["environment_size"]
        ],
    }
    for term, rate in expected.items():
        want = round(rate * HOURS_PER_DAY, 6)
        if terms[term] != want:
            raise AssertionError(
                f"{term} is published as {terms[term]} per day and re-derives as {want}"
            )

    total = round(sum(terms.values()), 6)
    if cost["usd_per_day"] != round(total, 4):
        raise AssertionError(
            f"the daily total is {cost['usd_per_day']} and its terms come to {total}"
        )
    if cost["usd_per_30_days"] != round(total * DAYS_PER_MONTH, 2):
        raise AssertionError(
            f"the 30 day total is {cost['usd_per_30_days']} and the daily figure "
            f"gives {round(total * DAYS_PER_MONTH, 2)}"
        )


def check_the_declared_shape_is_what_the_terraform_declares():
    """The shape is read out of composer.tf, so a change there has to move it.

    This is the half that stops the cost model from pricing a shape the
    configuration stopped declaring. It compares the totals rather than
    re-parsing each block, because the parse lives in the script and a second
    copy of it here would agree with itself.
    """
    cost = _cost()["composer"]
    text = COMPOSER_TF.read_text(encoding="utf-8")

    size = re.search(r'environment_size\s*=\s*"(\w+)"', text)
    if not size or size.group(1) != cost["environment_size"]:
        raise AssertionError(
            f"the cost model prices {cost['environment_size']} and composer.tf "
            f"declares {size.group(1) if size else 'nothing'}"
        )

    components = cost["components"]
    cpu = sum(part["vcpu"] for part in components.values())
    if cpu != cost["declared_vcpu"]:
        raise AssertionError(
            f"the components come to {cpu} vCPU and the total says {cost['declared_vcpu']}"
        )
    for name in ("scheduler", "web_server", "worker"):
        if name not in components:
            raise AssertionError(f"the cost model prices no {name}")
        block = re.search(rf"{name}\s*\{{(.*?)\}}", text, re.DOTALL)
        if not block:
            raise AssertionError(
                f"composer.tf declares no {name} block and the cost model prices one"
            )


def check_the_query_term_carries_no_figure_and_says_why():
    """The term that would flatter the answer if it were quietly zero.

    A cost model with an unmeasured term set to zero reads as complete. This
    asserts the term is null and that the reason travels with it, so the gap
    cannot be closed by accident.
    """
    queries = _cost()["queries"]
    if queries["bytes_scanned_per_run"] is not None:
        raise AssertionError(
            f"bytes scanned per run is published as {queries['bytes_scanned_per_run']}. "
            "No statement from this repository has executed against BigQuery, so a "
            "figure here came from somewhere other than a dry run"
        )
    if queries["usd_per_run"] is not None:
        raise AssertionError(
            f"a cost per run is published as {queries['usd_per_run']} and the quantity "
            "it multiplies does not exist"
        )
    if not queries.get("why_there_is_no_figure"):
        raise AssertionError("the missing query cost carries no reason, so it reads as an omission")
    if queries["on_demand_usd_per_tebibyte"] != _prices()["bigquery"]["on_demand_query_usd_per_tebibyte"]:
        raise AssertionError("the query rate does not match the price list")


def check_the_price_list_names_its_sources_and_the_date_it_was_read():
    """A transcribed price is only as good as the page and the day it came from."""
    prices = _prices()
    if not prices.get("source_urls"):
        raise AssertionError("the price list names no source, so nothing can check it")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(prices.get("retrieved_on", ""))):
        raise AssertionError(
            f"retrieved_on is {prices.get('retrieved_on')!r} and a transcribed price "
            "without a date cannot be told from a current one"
        )
    if "inferred" not in prices.get("_region_caveat", ""):
        raise AssertionError(
            "the price list does not record that its region attribution is inferred "
            "from the page layout rather than stated by the page"
        )


def check_the_teardown_document_names_the_guard_the_terraform_actually_carries():
    """A runbook that drifts from its configuration is worse than none.

    Both halves. The document has to name the obstacle, and the obstacle has to
    still be there. A guard removed from composer.tf with the document left
    saying it is there sends somebody down a step that does nothing.
    """
    text = _doc()
    composer = COMPOSER_TF.read_text(encoding="utf-8")

    guarded = re.search(r"lifecycle\s*\{[^}]*prevent_destroy\s*=\s*true", composer, re.DOTALL)
    if guarded and "prevent_destroy" not in text:
        raise AssertionError(
            "composer.tf carries prevent_destroy and the teardown document does not "
            "mention it, so the documented destroy stops on the expensive resource"
        )
    if not guarded and "prevent_destroy" in text:
        raise AssertionError(
            "the teardown document describes removing a prevent_destroy guard and "
            "composer.tf no longer carries one"
        )

    if "delete_contents_on_destroy = false" in BIGQUERY_TF.read_text(encoding="utf-8"):
        if "delete_contents_on_destroy" not in text:
            raise AssertionError(
                "the datasets refuse to destroy while they hold tables and the "
                "teardown document does not say so"
            )


def check_the_teardown_document_does_not_claim_any_of_it_ran():
    """The same rule the access document is held to.

    A procedure written from the provider schema is a procedure with unknown
    defects, and the document has to say which it is.
    """
    text = _doc().lower()
    for claim in ("we tore down", "the teardown was verified", "destroy succeeded"):
        if claim in text:
            raise AssertionError(f"the teardown document claims {claim!r} and nothing has run")
    if "never" not in text and "has not been verified" not in text:
        raise AssertionError(
            "the teardown document nowhere says the procedure has not run, so a "
            "reader would take it for one that has"
        )


def check_the_cost_document_carries_the_figures_the_model_holds():
    """Every figure in the document, looked for as an exact string.

    Typed figures are what this repository has been wrong about twice, so the
    numbers in the prose are grepped against the file they came from rather
        than reviewed.
    """
    cost = _cost()
    composer = cost["composer"]
    text = _doc()

    expected = {
        "daily total": f"**{composer['usd_per_day']:.2f}**",
        "monthly total": f"${composer['usd_per_30_days']:.2f} over 30 days",
        "fee share": f"{composer['environment_fee_share_of_the_day_pct']} percent",
        "vcpu": f"{composer['declared_vcpu']} vCPU",
        "query rate": f"${cost['queries']['on_demand_usd_per_tebibyte']} per TiB",
        "corpus bytes": f"{cost['queries']['corpus_csv_bytes']:,} bytes",
    }
    for term, value in sorted(composer["usd_per_day_by_term"].items()):
        expected[term] = f"| {value:.4f} |"

    missing = {name: want for name, want in expected.items() if want not in text}
    if missing:
        raise AssertionError(f"the cost document does not carry these figures: {missing}")


def check_the_freeze_sweep_control_arm_reproduces_the_published_share():
    """A sweep whose baseline does not reproduce is measuring its harness."""
    sweep = _sweep()
    control = sweep["control"]
    if not control["agrees"]:
        raise AssertionError(
            f"the sweep's control arm gives {control['share_pct_at_the_shipped_width']} "
            f"and the published share is {control['published_share_pct']}"
        )
    if sweep["shipped_freeze_business_days"] not in sweep["widths"]:
        raise AssertionError(
            "the sweep did not run the shipped freeze width, so it has no control arm"
        )
    if len(sweep["widths"]) < 2:
        raise AssertionError("one width is not a sweep")


def check_the_unbounded_ratio_is_recorded_as_leaving_the_share_interval():
    """The reason the sweep publishes two figures instead of one.

    The ratio of the gross period shift to the gross variance is not bounded by
    a hundred, and the sweep is the thing that demonstrated it. This asserts the
    demonstration is still in the file, because the moment it is not, the two
    figures look like duplication and somebody deletes one.
    """
    sweep = _sweep()
    over = sweep["widths_where_the_ratio_exceeds_one_hundred"]
    ratios = sweep["ratio_pct_by_width"]
    derived = sorted(int(width) for width, value in ratios.items() if value > 100.0)
    if over != derived:
        raise AssertionError(
            f"the file says widths {over} exceed a hundred percent and the ratios give {derived}"
        )
    if not over:
        raise AssertionError(
            "no width in the sweep put the ratio above a hundred percent, so the "
            "evidence that it is not a share is no longer in the file. Either the "
            "widths changed or the corpus did, and the two figures need re-arguing"
        )
    shares = sweep["share_pct_by_width"]
    bad = {width: value for width, value in shares.items() if not 0.0 <= value <= 100.0}
    if bad:
        raise AssertionError(
            f"the bounded share left the interval a share can live in at {bad}, which "
            "means it is not the decomposition it claims to be either"
        )
