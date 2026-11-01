"""Multiplies the published price list out against the declared environment.

    python3 scripts/cost_model.py

This is not a measurement and the file it writes says so in its own payload.
Nothing in `infra/` has ever been applied, so no bill exists. What this does is
read the shape `infra/composer.tf` declares, read the list prices transcribed
into `infra/price-list.json`, and multiply. The reason it is a script rather
than a paragraph is the standing rule that a number a document publishes about
itself gets derived rather than typed, and a price list multiplied out by hand
is exactly the typed number that rule exists to stop.

The useful half of the answer is the split rather than the total. Composer bills
for existing. The environment fee is charged whether or not a DAG fires, and so
are the scheduler and the web server, so the daily cost of this pipeline is
mostly the cost of having a scheduler at all. The half that does the work is the queries,
and that half has no quantity in it, because bytes scanned needs a dry run
against a billing enabled project and this repository has never had one.

So the output has a priced section and an unpriced one, and the unpriced one is
not an omission. A cost model that quietly left the query term at zero would
read as a complete answer and would be wrong in the direction that flatters.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import manifest

COST_MEASUREMENT = REPO_ROOT / "measurements" / "cost-model.json"
PRICE_LIST = REPO_ROOT / "infra" / "price-list.json"
COMPOSER_TF = REPO_ROOT / "infra" / "composer.tf"
CORPUS = REPO_ROOT / "data"

HOURS_PER_DAY = 24
DAYS_PER_MONTH = 30


def declared_workloads(text: str) -> dict:
    """The scheduler, web server and worker shape, read out of the Terraform.

    Read rather than restated, so a shape change in `composer.tf` moves the
    figure instead of leaving it stale. The block is matched by name and the
    run refuses when a component is missing, because a component silently
    absent from this dict is a component priced at zero.
    """
    block = re.search(r"workloads_config\s*\{(.*?)\n    \}", text, re.DOTALL)
    if not block:
        raise SystemExit(
            "infra/composer.tf has no workloads_config block, so there is no "
            "declared shape to price. Composer 3 meters a DCU hour instead and "
            "this model does not cover it"
        )
    body = block.group(1)

    out = {}
    for component in ("scheduler", "web_server", "worker"):
        found = re.search(rf"{component}\s*\{{(.*?)\}}", body, re.DOTALL)
        if not found:
            raise SystemExit(f"workloads_config declares no {component} block")
        fields = dict(re.findall(r"(\w+)\s*=\s*([\d.]+)", found.group(1)))
        missing = {"cpu", "memory_gb", "storage_gb"} - set(fields)
        if missing:
            raise SystemExit(f"the {component} block declares no {sorted(missing)}")
        # A web server is one by definition and a worker is priced at its floor,
        # because the maximum only bills on a day it scales and that day is not
        # every day.
        count = fields.get("count") or fields.get("min_count") or "1"
        out[component] = {
            "cpu": float(fields["cpu"]),
            "memory_gb": float(fields["memory_gb"]),
            "storage_gb": float(fields["storage_gb"]),
            "count": float(count),
            "max_count": float(fields["max_count"]) if "max_count" in fields else None,
        }
    size = re.search(r'environment_size\s*=\s*"(\w+)"', text)
    if not size:
        raise SystemExit("infra/composer.tf declares no environment_size, which carries the fee")
    out["_environment_size"] = size.group(1)
    return out


def price_composer(shape: dict, prices: dict) -> dict:
    size = shape["_environment_size"]
    fees = prices["composer_generation_2"]["environment_fee_usd_per_hour"]
    if size not in fees:
        raise SystemExit(f"no environment fee on the price list for {size}")

    rates = prices["composer_generation_2"]
    components = {}
    vcpu = memory = storage = 0.0
    for name in ("scheduler", "web_server", "worker"):
        part = shape[name]
        vcpu += part["cpu"] * part["count"]
        memory += part["memory_gb"] * part["count"]
        storage += part["storage_gb"] * part["count"]
        components[name] = {
            "vcpu": part["cpu"] * part["count"],
            "memory_gibibytes": part["memory_gb"] * part["count"],
            "storage_gibibytes": part["storage_gb"] * part["count"],
            "count": part["count"],
            "max_count": part["max_count"],
        }

    # Per day rather than per rate unit, and that is not a presentation choice.
    # gross_margin/manifest.py refuses any key reading as an elapsed time and
    # `hour` is on its list, so a `usd_per_hour` key cannot be written to a
    # measurement file. The guard is right and the precedent for this is the
    # recon model's `period_shift` column, which was named around the same list
    # rather than loosening it. The published rates keep their own units in
    # infra/price-list.json, which is a transcription and not a measurement.
    per_rate_unit = {
        "vcpu_usd": vcpu * rates["compute_vcpu_usd_per_hour"],
        "memory_usd": memory * rates["compute_memory_usd_per_gibibyte_hour"],
        "storage_usd": storage * rates["compute_storage_usd_per_gibibyte_hour"],
        "environment_fee_usd": float(fees[size]),
    }
    per_day = {key: round(value * HOURS_PER_DAY, 6) for key, value in per_rate_unit.items()}
    total_day = round(sum(per_day.values()), 6)
    return {
        "environment_size": size,
        "components": components,
        "declared_vcpu": vcpu,
        "declared_memory_gibibytes": memory,
        "declared_storage_gibibytes": storage,
        "usd_per_day_by_term": per_day,
        "usd_per_day": round(total_day, 4),
        "usd_per_30_days": round(total_day * DAYS_PER_MONTH, 2),
        "environment_fee_share_of_the_day_pct": round(
            100.0 * per_day["environment_fee_usd"] / total_day, 2
        ),
        "share_that_bills_while_idle_pct": 100.0,
        "why_idle_is_the_whole_bill": (
            "every term above is a rate on a resource that exists. The "
            "scheduler keeps running between DAG runs. So does the web server, "
            "and so does the floor worker. The environment fee is charged for "
            "the environment rather than for a task, so none of this is a "
            "function of how often the pipeline fires"
        ),
        "not_priced_here": {
            "database_storage": (
                "the price list carries a per gibibyte hour rate for the Cloud "
                "SQL instance and the size of that instance is a property of an "
                "environment that was never created, so there is no quantity to "
                "multiply"
            ),
            "worker_scale_out": (
                f"the worker maximum is {components['worker']['max_count']} and only "
                "the minimum is priced, because the second worker bills on a day a "
                "backfill needs it rather than every day"
            ),
            "network_egress": "the page says VPC network rates and no quantity exists",
        },
    }


def price_queries(prices: dict) -> dict:
    """The half with no quantity in it.

    Deliberately returns no total. `dbt compile --target bigquery` opens a
    connection even with no introspection, so no statement from this repository
    has ever reached BigQuery and no dry run has ever returned a byte count.
    The rate is recorded so a later run with access has one thing to do.
    """
    bq = prices["bigquery"]
    corpus_bytes = sum(path.stat().st_size for path in sorted(CORPUS.glob("*.csv")))
    return {
        "on_demand_usd_per_tebibyte": bq["on_demand_query_usd_per_tebibyte"],
        "free_tebibytes_per_month": bq["free_query_tebibytes_per_month"],
        "bytes_scanned_per_run": None,
        "usd_per_run": None,
        "why_there_is_no_figure": (
            "a dry run returns bytes scanned and a dry run needs a billing "
            "enabled project. No statement from this repository has executed "
            "against BigQuery, so the quantity this rate multiplies does not "
            "exist. See scripts/scan_probe.py and docs/bytes-scanned.md"
        ),
        "corpus_csv_bytes": corpus_bytes,
        "corpus_gibibytes": round(corpus_bytes / 1024**3, 6),
        "storage_free_gibibytes_per_month": bq["free_storage_gibibytes_per_month"],
        "corpus_is_inside_the_storage_free_tier": (
            corpus_bytes / 1024**3 < bq["free_storage_gibibytes_per_month"]
        ),
        "corpus_bytes_are_not_bigquery_logical_bytes": (
            "the corpus figure is the size of the landing CSVs on this machine. "
            "BigQuery charges on logical or physical bytes of the loaded tables "
            "and those are a different number, so this bounds the storage term "
            "below the free tier and is not the storage term"
        ),
    }


def model() -> dict:
    prices = json.loads(PRICE_LIST.read_text(encoding="utf-8"))
    shape = declared_workloads(COMPOSER_TF.read_text(encoding="utf-8"))
    composer = price_composer(shape, prices)
    queries = price_queries(prices)
    return {
        "this_is_a_price_list_multiplied_out_and_not_a_measurement": True,
        "nothing_in_infra_has_been_applied": True,
        "price_list": {
            "region_recorded": prices["region_recorded"],
            "retrieved_on": prices["retrieved_on"],
            "source_urls": prices["source_urls"],
            "region_caveat": prices["_region_caveat"],
        },
        "composer": composer,
        "queries": queries,
        "daily_total_usd_for_the_terms_that_have_a_quantity": composer["usd_per_day"],
        "terms_with_no_quantity": [
            "bytes scanned per run",
            "Cloud SQL database storage",
            "worker scale out",
            "network egress",
        ],
        "rebuilt_by": "python3 scripts/cost_model.py",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    result = model()
    manifest.write(COST_MEASUREMENT, result)

    composer = result["composer"]
    print("a price list multiplied out, not a measurement. nothing is applied")
    print(
        f"declared {composer['environment_size']}: {composer['declared_vcpu']} vCPU, "
        f"{composer['declared_memory_gibibytes']} GiB memory, "
        f"{composer['declared_storage_gibibytes']} GiB storage"
    )
    for term, value in sorted(composer["usd_per_day_by_term"].items()):
        print(f"  {term:24s} ${value:.4f} per day")
    print(
        f"composer ${composer['usd_per_day']:.2f} per day, "
        f"${composer['usd_per_30_days']:.2f} per 30 days"
    )
    print(
        f"the environment fee alone is {composer['environment_fee_share_of_the_day_pct']} "
        "percent of that, and all of it bills while idle"
    )
    queries = result["queries"]
    print(
        f"queries ${queries['on_demand_usd_per_tebibyte']} per TiB with "
        f"{queries['free_tebibytes_per_month']} TiB free, and NO cost per run, "
        "because bytes scanned has never been measured"
    )
    print(f"terms with no quantity: {', '.join(result['terms_with_no_quantity'])}")
    print(f"wrote {COST_MEASUREMENT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
