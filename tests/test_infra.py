"""The Terraform, graded on the things a reader would get wrong rather than on syntax.

`terraform validate` is the syntax and schema check. It was run against the
hashicorp google provider resolved from the constraint in main.tf and it passed.
It is not run from here, because the suite has to pass on a machine with no
terraform binary and no network, and because validate would not catch any of the
configurations below. Every one of them validates cleanly and is wrong.

Nothing in infra/ has been applied. These checks read it as text.

Comments are stripped before every match. A check that fires on its own
explanation is the kind that gets switched off rather than fixed.
"""

from __future__ import annotations

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent / "infra"

LAYERS = ("raw", "staging", "marts")


def _code(name: str) -> str:
    """The file with comment lines and trailing comments removed."""
    out = []
    for line in (INFRA / name).read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0] if "#" in line else line
        if stripped.strip():
            out.append(stripped)
    return "\n".join(out)


def check_every_dataset_states_that_a_destroy_must_not_drop_its_tables():
    """The most expensive mistake available in this directory.

    `delete_contents_on_destroy` defaults to false, so leaving it out is not a
    security hole. It is worse than that in review terms. A reader cannot tell
    whether it was considered, and the edit that turns it true is one word on a
    line nobody is watching. Stated explicitly, that edit shows up in a diff.
    """
    code = _code("bigquery.tf")
    if "delete_contents_on_destroy = false" not in code:
        raise AssertionError(
            "bigquery.tf never states delete_contents_on_destroy, so a destroy "
            "dropping the margin tables depends on a provider default"
        )
    if re.search(r"delete_contents_on_destroy\s*=\s*true", code):
        raise AssertionError("a dataset allows a destroy to drop its contents")


def check_the_dataset_layers_are_the_three_this_project_declares():
    """Counts the set before grading anything derived from it.

    Every later check in this module reads the dataset block. A loop over a set it
    discovered has to assert how big that set is, or a typo that drops a layer
    turns into a pass.
    """
    code = _code("bigquery.tf")
    found = re.findall(r"^\s{4}(\w+)\s*=\s*\{", code, re.MULTILINE)
    if sorted(found) != sorted(LAYERS):
        raise AssertionError(f"bigquery.tf declares layers {sorted(found)}, expected {sorted(LAYERS)}")


def check_the_marts_layer_has_no_table_expiration_and_the_raw_layer_does():
    """An expiry on a mart is a scheduled outage with no alert on it.

    The asymmetry is the point. Raw and staging are rebuildable so expiring them
    bounds storage. A mart is what a report reads. Both halves are asserted,
    because a check on the marts side alone would pass against a file that expires
    nothing and bills forever.
    """
    code = _code("bigquery.tf")
    blocks = dict(re.findall(r"^\s{4}(\w+)\s*=\s*\{(.*?)^\s{4}\}", code, re.MULTILINE | re.DOTALL))
    missing = [layer for layer in LAYERS if layer not in blocks]
    if missing:
        raise AssertionError(f"could not read the block for {missing}")
    if not re.search(r"table_expiration_ms\s*=\s*null", blocks["marts"]):
        raise AssertionError("the marts layer carries a table expiration, so its tables vanish")
    for layer in ("raw", "staging"):
        if not re.search(r"table_expiration_ms\s*=\s*\d+", blocks[layer]):
            raise AssertionError(
                f"the {layer} layer has no table expiration, so a rebuildable "
                "layer is billed for storage nobody decided to keep"
            )


def check_the_bucket_and_every_dataset_share_one_location():
    """A load job cannot read a bucket in a different location from its dataset.

    The error arrives when the first load runs rather than at plan time, which is
    the worst place for it. Both sides read the same variable here and the check
    is that neither has been given a literal.
    """
    for name in ("main.tf", "bigquery.tf"):
        code = _code(name)
        for match in re.findall(r"location\s*=\s*(\S+)", code):
            if match != "var.location":
                raise AssertionError(
                    f"{name} sets a location of {match} rather than var.location, so the "
                    "bucket and the datasets can drift apart"
                )


def check_continuous_integration_cannot_delete_a_dataset_or_regrant_it():
    """dataEditor and jobUser. Not dataOwner and not admin.

    dataOwner carries the right to change who else can read the dataset, which on
    a margin mart is the whole access control story. admin carries the right to
    delete the dataset. A build needs neither.
    """
    code = _code("iam.tf")
    for forbidden in (
        "roles/bigquery.admin",
        "roles/bigquery.dataOwner",
        "roles/storage.objectAdmin",
        "roles/storage.admin",
        "roles/editor",
        "roles/owner",
    ):
        if forbidden in code:
            raise AssertionError(f"iam.tf grants {forbidden} to the build identity")
    for required in ("roles/bigquery.dataEditor", "roles/bigquery.jobUser"):
        if required not in code:
            raise AssertionError(f"iam.tf never grants {required}, so the build cannot run")


def check_the_identity_pool_restricts_which_repository_may_assume_it():
    """The documented footgun and the only security hole in this directory.

    Without `attribute_condition` the pool trusts a token from any repository on
    GitHub that names the provider.
    """
    code = _code("oidc.tf")
    if "attribute_condition" not in code:
        raise AssertionError(
            "oidc.tf declares a pool provider with no attribute_condition, so any "
            "repository on GitHub could assume the build identity"
        )
    if "assertion.repository ==" not in code:
        raise AssertionError(
            "the attribute_condition does not pin assertion.repository, so it is not "
            "restricting by repository"
        )


def check_no_project_identifier_carries_a_default():
    """A plausible placeholder is the thing that survives into a demo.

    The three identifying variables have no default on purpose and the variable
    file is gitignored, so the only way one of them acquires a value is somebody
    filling it in.
    """
    code = _code("variables.tf")
    for name in ("project_id", "bucket_name", "billing_account"):
        parts = code.split(f'variable "{name}"', 1)
        if len(parts) != 2:
            raise AssertionError(f"variables.tf declares no variable named {name}")
        body = parts[1].split("}", 1)[0]
        for line in body.splitlines():
            if "=" in line and line.split("=")[0].strip() == "default":
                raise AssertionError(
                    f"variable {name} carries a default, so a plan would run against a "
                    "plausible and wrong project"
                )
    committed = sorted(p.name for p in INFRA.glob("*.tfvars"))
    if committed:
        raise AssertionError(f"infra/ holds {committed}, which must never be committed")


def check_a_budget_watches_for_an_overrun_and_not_only_for_reaching_the_budget():
    code = _code("budget.tf")
    if "google_billing_budget" not in code:
        raise AssertionError("infra/ declares no budget and nothing here ships without one")
    thresholds = re.search(r"for_each\s*=\s*\[([^\]]+)\]", code)
    if not thresholds:
        raise AssertionError("the budget declares no threshold list")
    values = [float(v.strip()) for v in thresholds.group(1).split(",") if v.strip()]
    if not any(v > 1.0 for v in values):
        raise AssertionError(
            f"the budget thresholds are {values} and none is above the budget, so a "
            "sudden overrun looks the same as reaching it"
        )


def check_the_landing_bucket_refuses_a_destroy_and_expires_its_own_objects():
    code = _code("main.tf")
    if "force_destroy = false" not in code:
        raise AssertionError("the landing bucket allows a destroy to empty it")
    if "lifecycle_rule" not in code:
        raise AssertionError(
            "the landing bucket has no lifecycle rule, so every extract ever landed "
            "is billed forever"
        )
    if "uniform_bucket_level_access = true" not in code:
        raise AssertionError("the landing bucket still allows per object access control lists")


def check_the_dataset_prefix_is_rewritten_because_a_dash_is_not_legal():
    """A dataset id cannot hold a dash and the default prefix does.

    This is the one in here that would fail at apply time with a clear message, and
    it is still worth a check because the default carries the dash deliberately so
    the rewrite is on the path everybody takes.
    """
    code = _code("bigquery.tf")
    if 'replace(var.dataset_prefix, "-", "_")' not in code:
        raise AssertionError(
            "the dataset id uses var.dataset_prefix without rewriting the dash, which "
            "BigQuery refuses"
        )
    variables = _code("variables.tf")
    if '"gross-margin"' not in variables:
        raise AssertionError(
            "the default dataset prefix no longer carries a dash, so the rewrite is "
            "never exercised by the default path"
        )
