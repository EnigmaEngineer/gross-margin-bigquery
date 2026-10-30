"""The access model, graded against the corpus, the Terraform and the dbt yaml.

Nothing here opens a warehouse. The row and column refusals are measured by
scripts/access_probe.py against DuckDB and the counts land in
measurements/access-enforcement.json, which these checks re-derive rather than
read as prose. That split is deliberate. The probe needs a built warehouse and
the suite has to pass on a machine that has never built one.

The check that matters most in this module is the last one. Every other check
grades a rule that was written down. `check_every_mart_holding_a_restricted_
column_is_in_the_rule_list` grades the rules against what the warehouse
actually contains, and it is the only one that can fail because of a model
somebody added. The layout pair was exactly that case. Two complete copies of
the line table, cost and region included, sitting in the dataset the reporting
roles hold, protected by nothing for two days.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gross_margin import access

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "measurements" / "access-enforcement.json"
RULES_FILE = REPO_ROOT / "infra" / "access-rules.json"
MODELS_YML = REPO_ROOT / "transform" / "models" / "marts" / "_models.yml"
ACCESS_TF = REPO_ROOT / "infra" / "access.tf"
PARTNERS = REPO_ROOT / "data" / "partners.csv"
DOC = REPO_ROOT / "docs" / "access-model.md"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _tf() -> str:
    """access.tf with comment lines removed, the way test_infra reads its files."""
    out = []
    for line in ACCESS_TF.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0] if "#" in line else line
        if stripped.strip():
            out.append(stripped)
    return "\n".join(out)


def check_the_declared_regions_are_the_regions_the_corpus_holds():
    """A region in the data with no role is revenue nobody can read.

    The corpus is gitignored, so this check prints a skip line rather than
    failing when it is absent. A skip is a silence and this is the one place it
    is the right answer, because the alternative is a fixture copy of the
    partner dimension that can disagree with the real one.
    """
    if not PARTNERS.exists():
        print(f"SKIP check_the_declared_regions: {PARTNERS} is absent, run scripts/build_corpus.py")
        return
    import csv

    with PARTNERS.open(encoding="utf-8", newline="") as handle:
        found = sorted({row["region"] for row in csv.DictReader(handle)})
    if found != sorted(access.REGIONS):
        raise AssertionError(
            f"the corpus holds regions {found} and gross_margin.access declares "
            f"{sorted(access.REGIONS)}, so a region has no role or a role has no data"
        )


def check_every_regional_role_gets_exactly_one_region():
    """Two regions on one role is a reasonable thing to want and it is not this.

    The roles are generated from REGIONS, so this is a check on the generator
    rather than on a list somebody typed. It is here because the row predicate
    builds an in list, which would quietly accept a role carrying three.
    """
    regional = [role for role in access.ROLES if role.is_regional]
    if len(regional) != len(access.REGIONS):
        raise AssertionError(
            f"{len(regional)} regional roles against {len(access.REGIONS)} regions"
        )
    for role in regional:
        if len(role.regions or ()) != 1:
            raise AssertionError(f"{role.name} carries {role.regions}")


def check_no_role_that_may_not_read_cost_can_reach_it_by_subtraction():
    """The closure, graded on the rule list rather than on the warehouse.

    For every relation and every role that may not read cost, no recovering set
    may survive intact in the readable columns. This is the check that fails if
    somebody removes margin from the withheld set to get a report working.
    """
    for relation in access.RELATIONS:
        all_columns = tuple(
            dict.fromkeys(
                list(relation.cost_columns)
                + [column for recovering in relation.recovering_sets for column in recovering]
            )
        )
        for role in access.ROLES:
            if role.may_read_cost:
                continue
            readable = set(access.readable_columns(relation, role, all_columns))
            if set(relation.cost_columns) & readable:
                raise AssertionError(
                    f"{role.name} can read {sorted(set(relation.cost_columns) & readable)} "
                    f"on {relation.name}"
                )
            for recovering in relation.recovering_sets:
                if set(recovering) <= readable:
                    raise AssertionError(
                        f"{role.name} can recover cost on {relation.name} from "
                        f"{list(recovering)}, which are all readable"
                    )


def check_a_role_that_may_read_cost_is_withheld_nothing():
    """The control arm has to be a real control.

    Every refusal the probe reports is retried against `finance_analyst`. If
    that role were withheld anything the control would refuse too and the probe
    would report a defect in itself rather than a working policy.
    """
    for role in access.ROLES:
        if not role.may_read_cost:
            continue
        for relation in access.RELATIONS:
            withheld = access.withheld_columns(relation, role)
            if withheld:
                raise AssertionError(
                    f"{role.name} is withheld {withheld} on {relation.name}, so it "
                    "cannot be the control"
                )


def check_a_regional_role_is_refused_a_finance_wide_relation_rather_than_shown_an_empty_one():
    """No access and no rows are different answers and only one is honest."""
    for role in access.ROLES:
        if not role.is_regional:
            continue
        granted = {relation.name for relation in access.grants_for(role)}
        for relation in access.RELATIONS:
            if relation.region_column is None and relation.name in granted:
                raise AssertionError(
                    f"{role.name} is granted the finance wide {relation.name}"
                )
        for relation in access.grants_for(role):
            predicate = access.row_predicate(relation, role)
            if predicate is None:
                raise AssertionError(
                    f"{role.name} gets an unfiltered view of {relation.name}"
                )


def check_the_bigquery_predicate_and_the_view_predicate_disagree_where_they_should():
    """The divergence is a finding and a check keeps it from being smoothed over.

    BigQuery reads the policies for a table as a union, so a principal with no
    policy on a table that has any reads zero rows. A view with no where clause
    returns everything. So the role that reads the whole book needs an explicit
    true predicate on BigQuery and no predicate at all in the view layer, and
    this check asserts both halves of that.
    """
    finance = next(role for role in access.ROLES if role.name == "finance_analyst")
    surfaces = [
        relation
        for relation in access.tables_with_row_policies()
        if relation.reporting_surface
    ]
    if not surfaces:
        raise AssertionError("no relation carries a row access policy, so this grades nothing")
    for relation in surfaces:
        if access.row_predicate(relation, finance) is not None:
            raise AssertionError(
                f"the view layer filters {relation.name} for finance, which it must not"
            )
        if access.bigquery_filter_predicate(relation, finance) != "true":
            raise AssertionError(
                f"finance has no true row access policy on {relation.name}, so on "
                "BigQuery it would read zero rows"
            )


def check_the_committed_terraform_rules_are_what_the_rule_list_produces():
    """The generated file is in git, so something has to grade it."""
    if not RULES_FILE.exists():
        raise AssertionError(f"{RULES_FILE} is missing, run scripts/emit_access_rules.py")
    on_disk = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    if on_disk != access.rules_as_json():
        raise AssertionError(
            f"{RULES_FILE.name} is not what gross_margin.access produces. Run "
            "scripts/emit_access_rules.py"
        )


def check_the_terraform_loops_over_the_generated_rules_rather_than_naming_a_role():
    """A role name written in the HCL is the second copy this all exists to avoid."""
    code = _tf()
    if "jsondecode(file(" not in code:
        raise AssertionError("access.tf does not read access-rules.json")
    for role in access.ROLES:
        if role.name in code:
            raise AssertionError(
                f"access.tf names the role {role.name} directly rather than looping "
                "over the generated rules"
            )
    for region in access.REGIONS:
        if region in code:
            raise AssertionError(f"access.tf names the region {region} directly")


def check_the_taxonomy_turns_on_fine_grained_access_control():
    """A taxonomy without it labels the column and denies nothing."""
    code = _tf()
    if "FINE_GRAINED_ACCESS_CONTROL" not in code:
        raise AssertionError(
            "the taxonomy does not activate FINE_GRAINED_ACCESS_CONTROL, so the "
            "policy tag is a label rather than a control"
        )
    if "google_data_catalog_policy_tag_iam_member" not in code:
        raise AssertionError(
            "nothing grants fineGrainedReader, so the tagged columns are denied to "
            "every principal including the ones that need them"
        )
    if "categoryFineGrainedReader" not in code:
        raise AssertionError("the grant is not the fine grained reader role")


def check_every_role_reads_the_marts_dataset_and_no_role_reads_raw_or_staging():
    """The grant the column tags rest on, and the one that was missing.

    A policy tag on a marts column is worth nothing to a principal that can
    read the staging layer, because stg_invoice_lines carries the cost column
    with no tag on it and no arithmetic needed. The tags were written first and
    the dataset grants were not written at all, which is the wrong order and
    the reason this check exists.
    """
    code = _tf()
    if "google_bigquery_dataset_iam_member" not in code:
        raise AssertionError(
            "no role is granted a dataset, so the policy tags protect columns in "
            "tables nobody has been given and the control is untested"
        )
    grant = code[code.index("google_bigquery_dataset_iam_member") :]
    grant = grant[: grant.index("\n}") + 2]
    if 'layer["marts"]' not in grant:
        raise AssertionError("the dataset grant does not name the marts dataset")
    for layer in ("raw", "staging"):
        if f'layer["{layer}"]' in grant:
            raise AssertionError(
                f"a role is granted the {layer} dataset, where the cost column carries "
                "no policy tag at all"
            )
    if "roles/bigquery.dataViewer" not in grant:
        raise AssertionError("the dataset grant is not dataViewer")
    for forbidden in (
        "roles/bigquery.dataEditor",
        "roles/bigquery.dataOwner",
        "roles/bigquery.admin",
    ):
        if forbidden in grant:
            raise AssertionError(
                f"a reporting role is granted {forbidden}, which lets it replace the "
                "mart the policy is attached to"
            )


def check_the_cost_column_outside_the_protected_layer_is_counted_rather_than_forgotten():
    """The limitation, held to a number so it cannot quietly stop being true.

    The control covers the marts schema. The probe counts the relations outside
    it that still carry a cost column, which on this build is the staging view
    and the raw landing table. If that count ever reaches zero the README's
    limitation is wrong in the direction that understates the exposure, and if
    it grows nobody is told unless something reads it.
    """
    outside = _manifest()["cost_outside_the_protected_layer"]
    if outside["relations"] <= 0:
        raise AssertionError(
            "no relation outside the marts schema carries a cost column, so either "
            "the layers changed or the survey looked in the wrong place"
        )
    if not outside["schemas"]:
        raise AssertionError("the survey named no schema holding cost outside marts")
    if "main_marts" in outside["schemas"]:
        raise AssertionError("the survey counted the protected layer as outside it")


def check_every_row_access_policy_names_a_grantee_and_a_predicate():
    """A policy with no grantee is a policy that refuses everybody."""
    rules = access.rules_as_json()
    if not rules["row_access_policies"]:
        raise AssertionError("the rule list produces no row access policies")
    for policy in rules["row_access_policies"]:
        if not policy["filter_predicate"]:
            raise AssertionError(f"{policy['policy_id']} has an empty predicate")
        if not policy["role"]:
            raise AssertionError(f"{policy['policy_id']} names no role")
    code = _tf()
    if "grantees" not in code:
        raise AssertionError("the row access policy resource sets no grantees")


def check_the_dbt_yaml_tags_exactly_the_columns_the_rule_list_restricts():
    """The yaml is the second place the column list lives, so it gets derived.

    Read as text rather than parsed, because the file carries jinja and a yaml
    parser would choke on the policy tag value. What matters is the pairing of
    a model name and a column name with a tag under it, which the indentation
    makes unambiguous.
    """
    text = MODELS_YML.read_text(encoding="utf-8")
    tagged: set[tuple[str, str]] = set()
    model = None
    column = None
    for line in text.splitlines():
        model_match = re.match(r"^  - name: (\S+)", line)
        if model_match:
            model = model_match.group(1)
            column = None
            continue
        column_match = re.match(r"^      - name: (\S+)", line)
        if column_match:
            column = column_match.group(1)
            continue
        if line.strip().startswith("policy_tags:"):
            if model is None or column is None:
                raise AssertionError(f"a policy_tags line has no model and column above it: {line}")
            tagged.add((model, column))

    expected = {
        (entry["table_id"], entry["column"])
        for entry in access.rules_as_json()["tagged_columns"]
    }
    if not expected:
        raise AssertionError("the rule list restricts no columns, so this grades nothing")
    if tagged != expected:
        raise AssertionError(
            f"the yaml tags {sorted(tagged - expected)} it should not and is missing "
            f"{sorted(expected - tagged)}"
        )


def check_the_marts_enable_persist_docs_so_a_tag_is_actually_sent():
    """Without it dbt records the tag and never applies it."""
    project = (REPO_ROOT / "transform" / "dbt_project.yml").read_text(encoding="utf-8")
    if project.count("+persist_docs:") < 2:
        raise AssertionError(
            "persist_docs is not set on both the marts and the scan layer, so a "
            "policy tag on one of them parses and is never sent"
        )
    if "columns: true" not in project:
        raise AssertionError("persist_docs does not persist columns")


def check_the_access_document_states_the_numbers_the_measurement_holds():
    """Four figures in docs/access-model.md were typed and four went stale.

    They were written from an earlier run of the probe and a relation added to
    the rule list moved all four. That is the reason this repository derives
    every figure a document states about itself, and the document had no check
    behind it for the hour it existed. This is that check.

    Read as a table row rather than as free prose, the way test_readme reads
    the lag quantiles, so the figure and its label cannot drift apart.
    """
    manifest = _manifest()
    rows = manifest["rows"]
    doc = DOC.read_text(encoding="utf-8")

    expected = {
        "views built": manifest["views_built"],
        "answer key queries, all answered": manifest["answer_key_queries"],
        "column attacks, all refused": manifest["column_attacks"],
        "relation attacks, all refused": manifest["relation_attacks"],
        "attacks the control role also refused": manifest["attacks_the_control_also_refused"],
        "row scope checks matching exactly": manifest["row_scope_matches"],
    }
    found = dict(re.findall(r"^\| ([a-z][^|]*?) \| (\d+) \|$", doc, re.MULTILINE))
    for label, value in expected.items():
        if label not in found:
            raise AssertionError(f"{DOC.name} has no row labelled {label!r}")
        if int(found[label]) != value:
            raise AssertionError(
                f"{DOC.name} says {label} is {found[label]} and the measurement says {value}"
            )

    # The row and revenue table, which carries formatted money rather than counts.
    for label, value in (
        ("whole book", rows["finance_rows"]),
        ("reachable by the four regional roles", rows["regional_rows_summed"]),
        ("reachable by no region", rows["unreachable_rows"]),
    ):
        pattern = rf"^\| {re.escape(label)} \| ([\d,]+) \| ([\d,]+\.\d\d) \|$"
        match = re.search(pattern, doc, re.MULTILINE)
        if not match:
            raise AssertionError(f"{DOC.name} has no row and revenue line for {label!r}")
        if int(match.group(1).replace(",", "")) != value:
            raise AssertionError(
                f"{DOC.name} says {label} holds {match.group(1)} lines and the "
                f"measurement says {value}"
            )

    cents = {
        "whole book": rows["finance_revenue_cents"],
        "reachable by the four regional roles": rows["regional_revenue_cents_summed"],
        "reachable by no region": rows["unreachable_revenue_cents"],
    }
    for label, value in cents.items():
        pattern = rf"^\| {re.escape(label)} \| [\d,]+ \| ([\d,]+\.\d\d) \|$"
        match = re.search(pattern, doc, re.MULTILINE)
        stated = int(match.group(1).replace(",", "").replace(".", ""))
        if stated != value:
            raise AssertionError(
                f"{DOC.name} says {label} revenue is {match.group(1)} and the "
                f"measurement says {value} cents"
            )

    outside = manifest["cost_outside_the_protected_layer"]
    if f"{outside['columns']} columns" not in " ".join(doc.split()):
        raise AssertionError(
            f"{DOC.name} does not state that {outside['columns']} columns outside the "
            "protected layer carry cost"
        )
    for where in outside["where"]:
        relation = where.rsplit(".", 1)[0].split(".", 1)[1]
        if relation not in doc:
            raise AssertionError(f"{DOC.name} never names {relation}, which holds cost")


def check_the_access_document_does_not_claim_the_cloud_side_ran():
    """The same overclaim test the README carries, on the file that replaced it.

    Most of this day's prose moved into docs/ to keep the README from growing
    again. The check that grades the README for claiming the infrastructure is
    live reads only the README, so the overclaim simply moved somewhere nothing
    was looking.
    """
    text = " ".join(DOC.read_text(encoding="utf-8").split()).lower()
    forbidden = (
        "terraform apply",
        "deployed to",
        "policy tag was applied",
        "queries run against bigquery",
        "running in production",
    )
    hits = [phrase for phrase in forbidden if phrase in text]
    if hits:
        raise AssertionError(f"{DOC.name} claims the cloud side is live: {hits}")
    for required in ("has not been applied", "no bigquery behaviour has been observed"):
        if required not in text:
            raise AssertionError(
                f"{DOC.name} no longer states {required!r}, so a reader cannot tell the "
                "cloud side is unapplied"
            )


def check_the_probe_proved_every_refusal_against_a_control():
    """A refusal nothing controlled for is a refusal with no cause attached."""
    manifest = _manifest()
    attacks = manifest["column_attacks"] + manifest["relation_attacks"]
    if attacks == 0:
        raise AssertionError("the probe ran no attacks, so a clean manifest means nothing")
    if manifest["column_refusals"] != manifest["column_attacks"]:
        raise AssertionError(
            f"{manifest['column_attacks'] - manifest['column_refusals']} column attacks "
            "were answered rather than refused"
        )
    if manifest["relation_refusals"] != manifest["relation_attacks"]:
        raise AssertionError(
            f"{manifest['relation_attacks'] - manifest['relation_refusals']} relation "
            "attacks were answered rather than refused"
        )
    if manifest["attacks_the_control_also_refused"] != 0:
        raise AssertionError(
            f"{manifest['attacks_the_control_also_refused']} refusals also refused for "
            "the control role, so they are probe defects rather than policy"
        )
    if manifest["answer_key_refusals"] != 0:
        raise AssertionError("a query the answer key says must answer was refused")


def check_the_probe_counted_one_attack_per_withheld_column_rather_than_a_fixture_list():
    """The attack count is derived from the rule list, so it can be re-derived.

    This is the check that fails when somebody adds a restricted column and the
    probe keeps testing the old set. It iterates the rules and never a list of
    cases sitting beside them.
    """
    expected = sum(
        len(access.withheld_columns(relation, role))
        for role in access.ROLES
        for relation in access.grants_for(role)
    )
    manifest = _manifest()
    if manifest["column_attacks"] != expected:
        raise AssertionError(
            f"the manifest records {manifest['column_attacks']} column attacks and the "
            f"rule list asks for {expected}"
        )
    expected_relations = sum(
        len(access.RELATIONS) - len(access.grants_for(role)) for role in access.ROLES
    )
    if manifest["relation_attacks"] != expected_relations:
        raise AssertionError(
            f"the manifest records {manifest['relation_attacks']} relation attacks and "
            f"the rule list asks for {expected_relations}"
        )


def check_the_regional_views_do_not_add_up_to_the_finance_total_and_the_gap_is_named():
    """The null region consequence, asserted as an identity rather than described.

    Lines billed with no partner carry no region, so no regional predicate
    matches them. The four regional views therefore hold fewer rows than
    finance, and the difference has to be exactly the unreachable set. If it is
    not, a row is missing for some other reason and that is the thing worth
    failing on.
    """
    rows = _manifest()["rows"]
    if rows["unreachable_rows"] <= 0:
        raise AssertionError(
            "the corpus holds no line with a null region, so this consequence is "
            "untested and the generator has changed"
        )
    if rows["rows_unaccounted_for"] != 0:
        raise AssertionError(
            f"{rows['rows_unaccounted_for']} rows are neither in a regional view nor "
            "in the unreachable set"
        )
    if rows["revenue_cents_unaccounted_for"] != 0:
        raise AssertionError(
            f"{rows['revenue_cents_unaccounted_for']} cents of revenue are neither in "
            "a regional view nor in the unreachable set"
        )
    if rows["regional_rows_summed"] >= rows["finance_rows"]:
        raise AssertionError(
            "the regional views add up to the finance total, so either the null "
            "region lines are reachable or the filter is not filtering"
        )


def check_tagging_only_the_cost_column_recovers_cost_on_every_row():
    """The measurement that justifies restricting margin.

    The probe builds the configuration that tags cost and keeps margin, then
    recovers cost from it. This asserts the recovery was exact on every row,
    because a partial recovery would be an argument for a weaker rule.
    """
    leak = _manifest()["leaky_variant"]
    if leak["cost_column_present_in_variant"]:
        raise AssertionError("the leaky variant still carries the cost column, so it proves nothing")
    if leak["rows_compared"] <= 0:
        raise AssertionError("the leak measurement compared no rows")
    if leak["rows_where_revenue_minus_margin_equals_cost"] != leak["rows_compared"]:
        raise AssertionError(
            f"cost was recovered on {leak['rows_where_revenue_minus_margin_equals_cost']} "
            f"of {leak['rows_compared']} rows, so the closure rests on a partial result"
        )


def check_the_view_layer_is_recorded_as_advisory_rather_than_as_enforcement():
    """DuckDB has no roles, so the local proof has a ceiling and it is stated.

    The probe reads the base relation directly and records that it answered.
    This check fails if that ever stops being true, because at that point the
    README's limitation is wrong in the direction that overstates the control.
    """
    uncovered = _manifest()["not_covered_by_the_view_layer"]
    if not uncovered["base_relation_still_readable"]:
        raise AssertionError(
            "the base relation refused a read, so the view layer is doing more than "
            "the README says and the limitation needs rewriting"
        )
    if uncovered["base_columns_visible_in_information_schema"] <= 0:
        raise AssertionError("the probe read no columns from the catalogue")


def check_every_mart_holding_a_restricted_column_is_in_the_rule_list():
    """The gate that would have caught the layout pair.

    Every other check in this module grades a rule against another rule. This
    one grades the rule list against what the warehouse actually contains,
    which is the only place a new copy of a restricted column can come from.
    The layout pair was two complete copies of the line table including cost
    and region, in the dataset the reporting roles hold, and nothing said so
    for two days.

    The survey is in the probe because the columns of a relation are a property
    of the built warehouse. A first version read the model sql instead and
    flagged four models that only join to the line table, which is not the same
    as carrying cost forward. That version is not worth keeping. A check that
    fires on four innocent models is a check somebody switches off.
    """
    survey = _manifest()["marts_survey"]
    if survey["relations_in_marts_schema"] < 10:
        raise AssertionError(
            f"the survey saw {survey['relations_in_marts_schema']} relations in the "
            "marts schema, which is too few to be the marts schema"
        )
    if survey["relations_carrying_the_quantity"] <= 0:
        raise AssertionError(
            "the survey found no relation carrying the restricted quantity, so it "
            "discovered nothing and a clean result means nothing"
        )
    if survey["unprotected"]:
        raise AssertionError(
            f"{survey['unprotected']} hold a restricted column or a revenue and margin "
            "pair and are not in gross_margin.access.RELATIONS, so they are copies "
            "nothing decided about"
        )


def check_the_survey_looked_at_every_relation_the_rule_list_claims_to_cover():
    """A rule for a relation the warehouse does not hold is a rule about nothing.

    The survey enumerates the marts schema. This checks the other direction, so
    a relation renamed in the models and left behind in the rule list fails
    here rather than sitting in the file producing views over nothing.
    """
    survey = _manifest()["marts_survey"]
    surveyed = {carrier["relation"] for carrier in survey["carriers"]}
    declared_with_quantity = {
        relation.name
        for relation in access.RELATIONS
        if relation.cost_columns or relation.recovering_sets
    }
    stale = sorted(declared_with_quantity - surveyed)
    if stale:
        raise AssertionError(
            f"the rule list restricts {stale} and the survey did not find the quantity "
            "on them, so either they are gone or the restriction is wrong"
        )
