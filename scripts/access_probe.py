"""Builds the per role view layer and measures what it refuses.

    python3 scripts/access_probe.py
    python3 scripts/access_probe.py --db build/gross_margin.duckdb

Three things about the shape of this probe, because the order of its sections
is load bearing and a reader would otherwise reorder them.

The answer key runs before the attacks. A rule that refuses a query it was
supposed to allow is worse than a rule that allows one it should have refused,
because the first gets the whole control switched off by whoever is on call.
So every role reads every relation it is granted first, and a failure there
stops the probe before a single refusal is counted.

Every refusal is retried against a control where the protection is off. A
select of a column that does not exist raises the same binder error whether the
column was withheld on purpose or misspelled in the probe. The control is
`finance_analyst`, which is withheld nothing, so a refusal that also refuses
there is a defect in the probe and is reported as one.

The leaky variant is built on purpose and the recovery is measured. Withholding
the column named cost and keeping margin is the configuration somebody reaches
for first. It is built here as a third schema and the probe recovers cost from
it to the cent, which is the only honest way to say the closure is doing work.

Nothing here writes a wall time. The manifest carries counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import access

SOURCE_SCHEMA = "main_marts"
LEAKY_SCHEMA = "acl_leaky_variant"


class ProbeFailure(RuntimeError):
    """Raised when the probe's own setup is wrong rather than the policy."""


def columns_of(con, schema: str, table: str) -> tuple[str, ...]:
    rows = con.execute(
        "select column_name from information_schema.columns "
        "where table_schema = ? and table_name = ? order by ordinal_position",
        [schema, table],
    ).fetchall()
    return tuple(row[0] for row in rows)


def build_views(con) -> dict[str, list[str]]:
    """One schema per role, one view per relation the role is granted."""
    built: dict[str, list[str]] = {}
    for role in access.ROLES:
        schema = access.role_schema(role)
        con.execute(f"create schema if not exists {access.quote_identifier(schema)}")
        names = []
        for relation in access.grants_for(role):
            all_columns = columns_of(con, SOURCE_SCHEMA, relation.name)
            if not all_columns:
                raise ProbeFailure(f"{SOURCE_SCHEMA}.{relation.name} is not in the warehouse")
            con.execute(
                access.create_view_sql(relation, role, all_columns, SOURCE_SCHEMA)
            )
            names.append(relation.name)
        built[role.name] = names
    return built


def build_leaky_variant(con) -> None:
    """The configuration that tags the cost column and leaves margin behind."""
    relation = access.RELATIONS_BY_NAME["fct_invoice_line_margin"]
    all_columns = columns_of(con, SOURCE_SCHEMA, relation.name)
    kept = [c for c in all_columns if c not in set(relation.cost_columns)]
    projection = ", ".join(access.quote_identifier(c) for c in kept)
    con.execute(f"create schema if not exists {access.quote_identifier(LEAKY_SCHEMA)}")
    con.execute(
        f"create or replace view {access.quote_identifier(LEAKY_SCHEMA)}."
        f"{access.quote_identifier(relation.name)} as "
        f"select {projection} from {access.quote_identifier(SOURCE_SCHEMA)}."
        f"{access.quote_identifier(relation.name)}"
    )


def run_answer_key(con, built: dict[str, list[str]]) -> list[dict]:
    """Every granted pair has to answer. A refusal in here halts the probe."""
    answered = []
    for role in access.ROLES:
        schema = access.role_schema(role)
        for relation_name in built[role.name]:
            relation = access.RELATIONS_BY_NAME[relation_name]
            readable = access.readable_columns(
                relation, role, columns_of(con, schema, relation_name)
            )
            projection = ", ".join(access.quote_identifier(c) for c in readable)
            sql = (
                f"select {projection} from {access.quote_identifier(schema)}."
                f"{access.quote_identifier(relation_name)}"
            )
            try:
                rows = len(con.execute(sql).fetchall())
            except Exception as exc:
                raise ProbeFailure(
                    f"the answer key was refused for {role.name} on {relation_name}: {exc}"
                ) from exc
            answered.append(
                {"role": role.name, "relation": relation_name, "rows": rows,
                 "columns": len(readable)}
            )
    return answered


def attempt(con, sql: str) -> tuple[bool, str]:
    """True and an empty message when the query answered, False and the error."""
    try:
        con.execute(sql).fetchall()
        return True, ""
    except Exception as exc:
        return False, str(exc).strip().splitlines()[0]


def run_column_attacks(con) -> list[dict]:
    """Select each withheld column as the role, then as the control."""
    results = []
    control = next(r for r in access.ROLES if r.name == "finance_analyst")
    for role in access.ROLES:
        if role.may_read_cost:
            continue
        for relation in access.grants_for(role):
            for column in access.withheld_columns(relation, role):
                role_sql = (
                    f"select {access.quote_identifier(column)} from "
                    f"{access.quote_identifier(access.role_schema(role))}."
                    f"{access.quote_identifier(relation.name)}"
                )
                answered, message = attempt(con, role_sql)
                control_sql = (
                    f"select {access.quote_identifier(column)} from "
                    f"{access.quote_identifier(access.role_schema(control))}."
                    f"{access.quote_identifier(relation.name)}"
                )
                control_answered, control_message = attempt(con, control_sql)
                results.append(
                    {
                        "role": role.name,
                        "relation": relation.name,
                        "column": column,
                        "refused": not answered,
                        "message": message,
                        "control_answered": control_answered,
                        "control_message": control_message,
                    }
                )
    return results


def run_relation_attacks(con) -> list[dict]:
    """A regional role naming a finance wide relation must not resolve it.

    Controlled the same way the column attacks are. A catalog error saying a
    table does not exist is what a misspelled relation name produces too, so
    the same select runs against the control's schema where the relation is
    granted. Without that arm this section would pass against a probe that
    built no views at all.
    """
    results = []
    control = next(r for r in access.ROLES if r.name == "finance_analyst")
    for role in access.ROLES:
        granted = set(access.grants_for(role))
        for relation in access.RELATIONS:
            if relation in granted:
                continue
            sql = (
                f"select * from {access.quote_identifier(access.role_schema(role))}."
                f"{access.quote_identifier(relation.name)}"
            )
            answered, message = attempt(con, sql)
            # The control for a relation nobody reports off cannot be another
            # role's schema, because no role has a view on it. It is the source
            # relation, which is readable, so a refusal in the role's schema is
            # the missing view rather than a missing table.
            if relation.reporting_surface:
                control_schema = access.quote_identifier(access.role_schema(control))
            else:
                control_schema = access.quote_identifier(SOURCE_SCHEMA)
            control_sql = (
                f"select * from {control_schema}."
                f"{access.quote_identifier(relation.name)}"
            )
            control_answered, control_message = attempt(con, control_sql)
            results.append(
                {
                    "role": role.name,
                    "relation": relation.name,
                    "refused": not answered,
                    "message": message,
                    "control_answered": control_answered,
                    "control_message": control_message,
                }
            )
    return results


def run_row_attacks(con) -> list[dict]:
    """What regions each role can actually see through its own view.

    The row counts in `measure_rows` add up whether or not the predicate is
    filtering the right region, because four wrong filters can still partition
    the table. This reads the distinct regions out of each role's view and is
    the only part of the probe that would catch a predicate pointed at the
    wrong region.
    """
    relation = access.RELATIONS_BY_NAME["fct_invoice_line_margin"]
    quoted = access.quote_identifier(relation.name)
    results = []
    for role in access.ROLES:
        if relation not in access.grants_for(role):
            continue
        schema = access.quote_identifier(access.role_schema(role))
        seen = con.execute(
            f"select distinct coalesce({access.quote_identifier('region')}, '<null>') "
            f"from {schema}.{quoted} order by 1"
        ).fetchall()
        visible = [row[0] for row in seen]
        expected = (
            sorted(role.regions)
            if role.is_regional
            else sorted(list(access.REGIONS) + ["<null>"])
        )
        results.append(
            {
                "role": role.name,
                "regions_visible": visible,
                "regions_expected": expected,
                "matches": visible == expected,
            }
        )
    return results


def measure_rows(con) -> dict:
    """Row counts per regional role against the finance total, and the gap."""
    relation = access.RELATIONS_BY_NAME["fct_invoice_line_margin"]
    quoted = access.quote_identifier(relation.name)
    total, total_revenue_cents = con.execute(
        f"select count(*), cast(round(sum(extended_revenue_usd) * 100) as hugeint) "
        f"from {access.quote_identifier(SOURCE_SCHEMA)}.{quoted}"
    ).fetchone()

    per_role = {}
    seen = 0
    revenue_seen = 0
    for role in access.ROLES:
        if not role.is_regional:
            continue
        schema = access.quote_identifier(access.role_schema(role))
        rows, revenue_cents = con.execute(
            f"select count(*), cast(round(sum(extended_revenue_usd) * 100) as hugeint) "
            f"from {schema}.{quoted}"
        ).fetchone()
        per_role[role.name] = {"rows": rows, "revenue_cents": int(revenue_cents)}
        seen += rows
        revenue_seen += int(revenue_cents)

    unreachable_rows, unreachable_cents = con.execute(
        f"select count(*), cast(round(sum(extended_revenue_usd) * 100) as hugeint) "
        f"from {access.quote_identifier(SOURCE_SCHEMA)}.{quoted} "
        f"where {access.UNREACHABLE_BY_REGION}"
    ).fetchone()

    return {
        "finance_rows": total,
        "finance_revenue_cents": int(total_revenue_cents),
        "per_regional_role": per_role,
        "regional_rows_summed": seen,
        "regional_revenue_cents_summed": revenue_seen,
        "unreachable_rows": unreachable_rows,
        "unreachable_revenue_cents": int(unreachable_cents),
        "rows_unaccounted_for": total - seen - unreachable_rows,
        "revenue_cents_unaccounted_for": int(total_revenue_cents)
        - revenue_seen
        - int(unreachable_cents),
    }


def measure_the_leak(con) -> dict:
    """Recover cost from the variant that only tagged the cost column."""
    relation = "fct_invoice_line_margin"
    leaky = access.quote_identifier(LEAKY_SCHEMA)
    source = access.quote_identifier(SOURCE_SCHEMA)
    quoted = access.quote_identifier(relation)
    rows, exact = con.execute(
        f"select count(*), sum(case when recovered = truth then 1 else 0 end) from ("
        f"  select leak.extended_revenue_usd - leak.invoiced_margin_usd as recovered,"
        f"         truth.extended_cost_usd as truth"
        f"  from {leaky}.{quoted} as leak"
        f"  join {source}.{quoted} as truth on leak.line_id = truth.line_id"
        f")"
    ).fetchone()
    cost_column_present = "extended_cost_usd" in columns_of(con, LEAKY_SCHEMA, relation)
    return {
        "cost_column_present_in_variant": cost_column_present,
        "rows_compared": rows,
        "rows_where_revenue_minus_margin_equals_cost": int(exact),
    }


def measure_what_the_view_layer_does_not_cover(con) -> dict:
    """The base relation is still named in the catalogue and still readable.

    DuckDB has no role system, so a view layer is the enforcement and the
    enforcement is advisory. This measures that rather than describing it,
    because a limitation with a number on it survives a rewrite of the README
    and a sentence does not.
    """
    relation = access.quote_identifier("fct_invoice_line_margin")
    source = access.quote_identifier(SOURCE_SCHEMA)
    base_answered, base_message = attempt(
        con, f"select extended_cost_usd from {source}.{relation}"
    )
    visible = con.execute(
        "select count(*) from information_schema.columns "
        "where table_schema = ? and table_name = 'fct_invoice_line_margin'",
        [SOURCE_SCHEMA],
    ).fetchone()[0]
    return {
        "base_relation_still_readable": base_answered,
        "base_relation_message": base_message,
        "base_columns_visible_in_information_schema": visible,
    }


def survey_unprotected_copies(con) -> dict:
    """Every relation in the marts schema, and whether the rule list knows it.

    The question is which relations hold the restricted quantity, and that is
    answerable exactly from the catalogue rather than by reading model sql. A
    first version of this read the sql and flagged four models that only join
    to the line table, because reading a relation that holds cost is not the
    same as carrying cost forward. The columns a relation actually has settle
    it with no heuristic in the way.

    A relation carries the quantity if it has a column the rule list restricts
    anywhere, or if it holds a revenue and a margin column together. The second
    clause is the one that catches a mart whose author renamed cost away.
    """
    restricted_names = set()
    for relation in access.RELATIONS:
        restricted_names.update(relation.cost_columns)
        for recovering in relation.recovering_sets:
            restricted_names.update(recovering)

    relations = [
        row[0]
        for row in con.execute(
            "select table_name from information_schema.tables "
            "where table_schema = ? order by table_name",
            [SOURCE_SCHEMA],
        ).fetchall()
    ]

    carriers = []
    for name in relations:
        columns = set(columns_of(con, SOURCE_SCHEMA, name))
        by_name = sorted(columns & restricted_names)
        has_pair = any("revenue" in c for c in columns) and any(
            "margin" in c for c in columns
        )
        if by_name or has_pair:
            carriers.append(
                {
                    "relation": name,
                    "restricted_columns_present": by_name,
                    "holds_a_revenue_and_margin_pair": has_pair,
                    "in_rule_list": name in access.RELATIONS_BY_NAME,
                }
            )

    return {
        "relations_in_marts_schema": len(relations),
        "relations_carrying_the_quantity": len(carriers),
        "unprotected": sorted(c["relation"] for c in carriers if not c["in_rule_list"]),
        "carriers": carriers,
    }


def measure_cost_outside_the_protected_layer(con) -> dict:
    """Relations outside the marts schema that still carry a cost column.

    The policy tags and the row filters cover one schema. The same number sits
    in the staging view and in the raw landing table with nothing on it, so the
    whole column control rests on those two layers not being granted. That is a
    dataset grant rather than a tag, and it is the thing that was missing when
    the tags were written.

    Role schemas are excluded because they are this probe's own output.
    """
    rows = con.execute(
        "select table_schema, table_name, column_name "
        "from information_schema.columns "
        "where lower(column_name) like '%cost%' "
        "  and table_schema <> ? "
        "  and table_schema not like 'acl_%' "
        "order by 1, 2, 3",
        [SOURCE_SCHEMA],
    ).fetchall()
    return {
        "relations": len({(r[0], r[1]) for r in rows}),
        "columns": len(rows),
        "schemas": sorted({r[0] for r in rows}),
        "where": [f"{r[0]}.{r[1]}.{r[2]}" for r in rows],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(REPO_ROOT / "build" / "gross_margin.duckdb"))
    parser.add_argument(
        "--out", default=str(REPO_ROOT / "measurements" / "access-enforcement.json")
    )
    args = parser.parse_args(argv)

    import duckdb

    con = duckdb.connect(args.db)
    try:
        built = build_views(con)
        build_leaky_variant(con)

        answer_key = run_answer_key(con, built)
        column_attacks = run_column_attacks(con)
        relation_attacks = run_relation_attacks(con)
        row_attacks = run_row_attacks(con)
        rows = measure_rows(con)
        leak = measure_the_leak(con)
        uncovered = measure_what_the_view_layer_does_not_cover(con)
        survey = survey_unprotected_copies(con)
        outside = measure_cost_outside_the_protected_layer(con)
    finally:
        con.close()

    probe_defects = [
        a for a in column_attacks + relation_attacks if not a["control_answered"]
    ]
    manifest = {
        "roles": len(access.ROLES),
        "relations": len(access.RELATIONS),
        "views_built": sum(len(v) for v in built.values()),
        "answer_key_queries": len(answer_key),
        "answer_key_refusals": 0,
        "column_attacks": len(column_attacks),
        "column_refusals": sum(1 for a in column_attacks if a["refused"]),
        "relation_attacks": len(relation_attacks),
        "relation_refusals": sum(1 for a in relation_attacks if a["refused"]),
        "attacks_the_control_also_refused": len(probe_defects),
        "row_scope_checks": len(row_attacks),
        "row_scope_matches": sum(1 for a in row_attacks if a["matches"]),
        "rows": rows,
        "leaky_variant": leak,
        "not_covered_by_the_view_layer": uncovered,
        "marts_survey": survey,
        "cost_outside_the_protected_layer": outside,
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")

    print(f"views built                  {manifest['views_built']}")
    print(f"answer key answered          {manifest['answer_key_queries']} of "
          f"{manifest['answer_key_queries']}")
    print(f"column attacks refused       {manifest['column_refusals']} of "
          f"{manifest['column_attacks']}")
    print(f"relation attacks refused     {manifest['relation_refusals']} of "
          f"{manifest['relation_attacks']}")
    print(f"control also refused         {manifest['attacks_the_control_also_refused']} of "
          f"{manifest['column_attacks'] + manifest['relation_attacks']}")
    print(f"row scope matched            {manifest['row_scope_matches']} of "
          f"{manifest['row_scope_checks']}")
    print(f"regional rows summed         {rows['regional_rows_summed']} of "
          f"{rows['finance_rows']}")
    print(f"unreachable by any region    {rows['unreachable_rows']} rows, "
          f"{rows['unreachable_revenue_cents']} cents of revenue")
    print(f"leaky variant recovered cost {leak['rows_where_revenue_minus_margin_equals_cost']}"
          f" of {leak['rows_compared']} rows")
    print(f"marts carrying the quantity {survey['relations_carrying_the_quantity']} of "
          f"{survey['relations_in_marts_schema']}")
    print(f"unprotected copies           {survey['unprotected'] or 'none'}")
    print(f"cost outside marts          {outside['columns']} columns over "
          f"{outside['relations']} relations in {outside['schemas']}")
    print(f"wrote {args.out}")

    if probe_defects:
        print("the control refused an attack, so the probe is wrong and not the policy")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
