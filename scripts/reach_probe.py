"""What a principal actually reaches under the written dataset grants.

    python3 scripts/reach_probe.py

This answers a question the view layer cannot be asked. `gross_margin.access`
decides what each role may read and `scripts/access_probe.py` proves the view
layer enforces it, on DuckDB, where the mechanism is a schema of views per role.
BigQuery does not work that way. There the grant is a dataset role and the
filter is a row access policy, and those two compose by a rule the view layer
has no equivalent of.

The rule is the union. A principal reads a table through the union of the row
access policies it is a grantee of, so once a table carries any policy a
principal with none of them reads zero rows. The part that matters here is the
other half of the same sentence. A table carrying NO policy at all is not
filtered by anything, so every principal holding the dataset reads it whole.

`infra/access.tf` grants `roles/bigquery.dataViewer` on the marts dataset to
every role in the rule list. The row access policies cover four relations. So
the relations that carry no policy are readable in full by every role, including
the four regional ones the view layer refuses them to.

That is what this script enumerates. It reads the Terraform and the rule list
and the built catalogue, and reports per role what is reachable, what a policy
narrows, and which columns survive the column mask. It makes no cloud call and
it proves nothing about BigQuery's behaviour. It reports what the written
configuration says, which is the half that was never read.
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

from gross_margin import access, manifest

REACH_MEASUREMENT = REPO_ROOT / "measurements" / "reach-by-role.json"
ACCESS_TF = REPO_ROOT / "infra" / "access.tf"
RULES = REPO_ROOT / "infra" / "access-rules.json"
MARTS_SCHEMA = "main_marts"

# Columns whose name says they carry revenue. Revenue is the input half of the
# recovery pair and the whole question this script was written for is whether a
# regional role can obtain it by a route the rule list did not consider.
REVENUE_COLUMNS = ("revenue_usd", "extended_revenue_usd")


def dataset_grant_from_terraform(text: str) -> dict:
    """Which dataset every role is granted, read out of the Terraform.

    Regex against HCL rather than a parser, and the reason it is acceptable here
    is that the question is narrow and the failure mode is loud. It looks for one
    named resource and refuses when the block is not the shape it expects, so a
    rewrite of the grant surfaces as this script failing rather than as a stale
    answer. A silent wrong answer is the only outcome worth engineering against.
    """
    block = re.search(
        r'resource\s+"google_bigquery_dataset_iam_member"\s+"role_reads_marts"\s*\{(.*?)\n\}',
        text,
        re.DOTALL,
    )
    if not block:
        raise SystemExit(
            "infra/access.tf has no google_bigquery_dataset_iam_member named "
            "role_reads_marts. The grant this script reads has been renamed or "
            "removed, so its answer would be about a resource that is gone"
        )
    body = block.group(1)
    layer = re.search(r'google_bigquery_dataset\.layer\["(\w+)"\]', body)
    granted_role = re.search(r'role\s*=\s*"([^"]+)"', body)
    for_each = re.search(r"for_each\s*=\s*(.+)", body)
    if not (layer and granted_role and for_each):
        raise SystemExit(
            "the role_reads_marts block no longer names a dataset layer, an IAM "
            f"role and a for_each. Body was:\n{body}"
        )
    return {
        "dataset_layer": layer.group(1),
        "iam_role": granted_role.group(1),
        "for_each_expression": for_each.group(1).strip(),
        "granted_to_every_role_in_the_rule_list": "access_rules.roles" in for_each.group(1),
    }


def catalogue(con) -> dict[str, list[str]]:
    rows = con.execute(
        "select table_name, column_name from information_schema.columns "
        "where table_schema = ? order by table_name, ordinal_position",
        [MARTS_SCHEMA],
    ).fetchall()
    out: dict[str, list[str]] = {}
    for table, column in rows:
        out.setdefault(table, []).append(column)
    return out


def reach_for(role: access.Role, rules: dict, relations: dict[str, list[str]]) -> dict:
    """What this role reaches on BigQuery, relation by relation.

    Three outcomes per relation and the middle one is the finding. A relation
    the role holds a policy on is narrowed to that policy's predicate. A
    relation carrying policies the role holds none of is refused. A relation
    carrying no policy at all is read whole, and nothing in the rule list says
    so because the rule list only lists the policies it creates.
    """
    policies_by_table: dict[str, list[dict]] = {}
    for policy in rules["row_access_policies"]:
        policies_by_table.setdefault(policy["table_id"], []).append(policy)

    masked = {
        (entry["table_id"], entry["column"])
        for entry in rules["tagged_columns"]
    }
    may_read_cost = next(
        entry["may_read_cost"] for entry in rules["roles"] if entry["name"] == role.name
    )

    view_layer = {relation.name for relation in access.grants_for(role)}
    per_relation = {}
    for table, columns in sorted(relations.items()):
        policies = policies_by_table.get(table, [])
        mine = [p for p in policies if p["role"] == role.name]
        if not policies:
            outcome, predicate = "unfiltered", None
        elif mine:
            outcome, predicate = "narrowed", mine[0]["filter_predicate"]
            if predicate == "true":
                outcome = "unfiltered"
        else:
            outcome, predicate = "refused", None

        readable = [
            column
            for column in columns
            if may_read_cost or (table, column) not in masked
        ]
        per_relation[table] = {
            "outcome": outcome,
            "row_filter": predicate,
            "columns_in_catalogue": len(columns),
            "columns_the_mask_leaves": len(readable),
            "revenue_columns_readable": [
                column for column in readable if column in REVENUE_COLUMNS
            ],
            "the_view_layer_grants_this_relation": table in view_layer,
        }

    reachable = {t: v for t, v in per_relation.items() if v["outcome"] != "refused"}
    wider_than_the_view_layer = sorted(
        table
        for table, v in reachable.items()
        if not v["the_view_layer_grants_this_relation"]
    )
    with_revenue = sorted(
        table for table, v in reachable.items() if v["revenue_columns_readable"]
    )
    return {
        "role": role.name,
        "is_regional": role.is_regional,
        "may_read_cost": may_read_cost,
        "relations_in_the_marts_dataset": len(per_relation),
        "relations_reachable": len(reachable),
        "relations_read_whole": sorted(
            t for t, v in reachable.items() if v["outcome"] == "unfiltered"
        ),
        "relations_narrowed_by_a_policy": sorted(
            t for t, v in reachable.items() if v["outcome"] == "narrowed"
        ),
        "relations_refused": sorted(
            t for t, v in per_relation.items() if v["outcome"] == "refused"
        ),
        "reachable_on_bigquery_but_not_through_the_view_layer": wider_than_the_view_layer,
        "relations_where_revenue_is_readable": with_revenue,
        "revenue_is_reachable": bool(with_revenue),
        "per_relation": per_relation,
    }


def probe(db_path: Path) -> dict:
    import duckdb

    rules = json.loads(RULES.read_text(encoding="utf-8"))
    grant = dataset_grant_from_terraform(ACCESS_TF.read_text(encoding="utf-8"))

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        relations = catalogue(con)
    finally:
        con.close()
    if not relations:
        raise SystemExit(
            f"{MARTS_SCHEMA} holds no relations in {db_path}, so there is nothing "
            "to read the grants against. Run scripts/run_transform.py first"
        )

    by_role = [reach_for(role, rules, relations) for role in access.ROLES]
    regional = [entry for entry in by_role if entry["is_regional"]]

    # The same read against the rule list as it was before the completion pass,
    # when a policy was only created for a relation carrying a region column.
    # It is here because the README publishes a before and after pair, and a
    # before figure that lives only in a run's output is a typed number the next
    # reader cannot check. Derived from the same module rather than transcribed.
    was = {
        "row_access_policies": [
            policy
            for policy in rules["row_access_policies"]
            if access.RELATIONS_BY_NAME[policy["table_id"]].region_column is not None
        ],
        "roles": rules["roles"],
        "tagged_columns": rules["tagged_columns"],
    }
    before = [reach_for(role, was, relations) for role in access.ROLES]
    before_regional = [entry for entry in before if entry["is_regional"]]

    def counts(entries: list[dict]) -> dict:
        """One regional role's shape, after asserting every one of them agrees.

        The four regional roles differ only in which region their predicate
        names, so a single triple describes all four. Asserted rather than
        assumed, because a role that diverged would make one row of the
        README's table a description of whichever role happened to be first.
        """
        shapes = {
            (
                len(entry["relations_read_whole"]),
                len(entry["relations_narrowed_by_a_policy"]),
                len(entry["relations_refused"]),
            )
            for entry in entries
        }
        if len(shapes) != 1:
            raise SystemExit(
                f"the regional roles do not agree on what they reach: {sorted(shapes)}. "
                "The published pair describes one role and would be wrong for the rest"
            )
        whole, narrowed, refused = shapes.pop()
        return {"read_whole": whole, "narrowed": narrowed, "refused": refused}

    # The number the thread was opened for. Whether a regional principal can
    # obtain revenue by a route other than the margin table it is scoped on.
    revenue_routes = sorted(
        {
            table
            for entry in regional
            for table in entry["relations_where_revenue_is_readable"]
        }
    )
    divergence = sorted(
        {
            table
            for entry in regional
            for table in entry["reachable_on_bigquery_but_not_through_the_view_layer"]
        }
    )
    return {
        "dataset_grant": grant,
        "marts_relations": sorted(relations),
        "roles": by_role,
        "a_regional_role": {
            "before_every_relation_carried_a_policy": counts(before_regional),
            "after": counts(regional),
            "what_before_means": (
                "the same read with the rule list cut back to the relations "
                "carrying a region column, which is what it held until the "
                "completion pass. Derived here so the published pair is not a "
                "figure typed out of a run's output"
            ),
        },
        "row_access_policy_count": {
            "before": len(was["row_access_policies"]),
            "after": len(rules["row_access_policies"]),
        },
        "revenue_routes_open_to_a_regional_role_before": sorted(
            {
                table
                for entry in before_regional
                for table in entry["relations_where_revenue_is_readable"]
            }
        ),
        "regional_roles": [entry["role"] for entry in regional],
        "revenue_routes_open_to_a_regional_role": revenue_routes,
        "relations_the_two_enforcements_disagree_on": divergence,
        "a_regional_role_can_obtain_revenue": bool(revenue_routes),
        "row_access_policy_tables": sorted(
            {p["table_id"] for p in rules["row_access_policies"]}
        ),
        "what_this_does_not_prove": (
            "no BigQuery statement ran. This reads the committed Terraform and "
            "the rule list against the DuckDB catalogue, so it reports what the "
            "configuration says rather than what the engine does. The union "
            "semantic it reasons from is documentation and not an observation, "
            "which is the open question in the access notes"
        ),
        "rebuilt_by": "python3 scripts/reach_probe.py",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(REPO_ROOT / "build" / "gross_margin.duckdb"))
    args = parser.parse_args(argv)

    result = probe(Path(args.db))
    manifest.write(REACH_MEASUREMENT, result)

    grant = result["dataset_grant"]
    print(
        f"{grant['iam_role']} on the {grant['dataset_layer']} dataset, "
        f"every role in the rule list: {grant['granted_to_every_role_in_the_rule_list']}"
    )
    print(f"{len(result['marts_relations'])} relations in {MARTS_SCHEMA}")
    print(f"{'role':26s} {'whole':>6} {'narrow':>7} {'refused':>8}  revenue")
    for entry in result["roles"]:
        print(
            f"{entry['role']:26s} {len(entry['relations_read_whole']):>6}"
            f" {len(entry['relations_narrowed_by_a_policy']):>7}"
            f" {len(entry['relations_refused']):>8}"
            f"  {len(entry['relations_where_revenue_is_readable'])}"
        )
    routes = result["revenue_routes_open_to_a_regional_role"]
    print(f"revenue reachable by a regional role: {result['a_regional_role_can_obtain_revenue']}")
    if routes:
        print("  through " + ", ".join(routes))
    pair = result["a_regional_role"]
    policies = result["row_access_policy_count"]
    print(
        f"a regional role, before: {pair['before_every_relation_carried_a_policy']}, "
        f"after: {pair['after']}"
    )
    print(
        f"row access policies {policies['before']} before and {policies['after']} after"
    )
    print(
        "revenue routes before: "
        + ", ".join(result["revenue_routes_open_to_a_regional_role_before"])
    )
    diverge = result["relations_the_two_enforcements_disagree_on"]
    print(f"{len(diverge)} relations the dataset grant opens that the view layer refuses")
    for table in diverge:
        print(f"  {table}")
    print(f"wrote {REACH_MEASUREMENT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
