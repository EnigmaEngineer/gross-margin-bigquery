"""Who may read which rows, and which columns carry a number they may not see.

This module is the only place the access model is written down. The BigQuery
side and the DuckDB side both read it, so a role added here appears in the
generated Terraform and in the generated view layer without either being
edited. Nothing below opens a connection or imports a driver, which is what
keeps the check suite runnable on a machine with no warehouse.

Two things in here are worth reading before the code.

The restricted quantity is cost and not the column named cost. On the line
table cost sits in `extended_cost_usd`, and it also sits in the pair of revenue
and margin, because margin is revenue minus cost and the subtraction is exact.
A policy tag on the cost column alone hands the same number back through
arithmetic a first year analyst would do by accident. So a relation declares
the column sets that recover the quantity, and the withheld set is the closure
over all of them rather than a list somebody remembered to keep current.

A region is a property of the partner and not of the invoice. Lines billed with
no partner on them carry a null region, so no regional predicate can ever match
them. That is not a defect in the predicate. It means those lines belong to no
region and a region scoped role cannot be given them, which leaves revenue that
the four regional reports do not add up to. `UNREACHABLE_BY_REGION` names the
consequence so it can be measured rather than discovered later.
"""

from __future__ import annotations

from dataclasses import dataclass

# The restricted quantity. One word, because a second one would need its own
# recovery sets and nothing today has them.
COST = "cost"

# Every region in the partner dimension. Asserted against the corpus by
# tests/test_access.py rather than trusted, because a region added to the
# generator and not to this tuple would silently lose a role.
REGIONS = ("midwest", "northeast", "southeast", "west")


@dataclass(frozen=True)
class Role:
    """A principal, the regions it may read and whether it may read cost.

    `regions` of None means every region and also the rows that belong to no
    region. That distinction is the whole reason it is None rather than the
    full tuple of regions. A role listing all four regions explicitly would
    still miss the null region lines, and writing that out looks correct.
    """

    name: str
    regions: tuple[str, ...] | None
    may_read_cost: bool

    @property
    def is_regional(self) -> bool:
        return self.regions is not None


def _regional_roles() -> tuple[Role, ...]:
    return tuple(
        Role(f"region_{region}_analyst", (region,), may_read_cost=False)
        for region in REGIONS
    )


ROLES: tuple[Role, ...] = (
    # Finance sees the whole book including the lines no region owns. This is
    # the control arm for every refusal the regional roles are supposed to
    # produce, so it has to exist for the proof and not only for the product.
    Role("finance_analyst", None, may_read_cost=True),
    # Partner operations works across regions and has no business with cost.
    # It is here because it separates the row rule from the column rule. A
    # suite with only finance and the regional roles cannot tell which of the
    # two refused a query.
    Role("partner_ops", None, may_read_cost=False),
    *_regional_roles(),
)


@dataclass(frozen=True)
class Relation:
    """A mart and how the two rules land on it.

    `region_column` of None means the relation is finance wide. That is a
    decision per relation and the reason is recorded in `why_finance_wide`,
    because a reader who finds a mart missing from the regional view layer is
    entitled to know whether it was considered.
    """

    name: str
    region_column: str | None
    cost_columns: tuple[str, ...]
    recovering_sets: tuple[tuple[str, ...], ...]
    why_finance_wide: str = ""
    # False for a relation nobody reports off. It still has to be in this list,
    # because it still holds the restricted quantity and it still sits in the
    # dataset the reporting roles hold. What it does not get is a view for any
    # role. On BigQuery it gets one row access policy naming only the roles that
    # may read cost, which makes it invisible to everybody else through the union
    # rule rather than through anybody remembering to withhold it.
    reporting_surface: bool = True


RELATIONS: tuple[Relation, ...] = (
    Relation(
        name="fct_invoice_line_margin",
        region_column="region",
        cost_columns=("extended_cost_usd",),
        recovering_sets=(("extended_revenue_usd", "invoiced_margin_usd"),),
    ),
    Relation(
        name="margin_by_partner",
        region_column="region",
        cost_columns=(),
        # No column here is called cost and the quantity is still present.
        # revenue_usd minus invoiced_margin_usd is the cost of the group to
        # the cent. The margin_after columns do not recover it, because a
        # credit has moved in between, so they are deliberately not listed.
        recovering_sets=(("revenue_usd", "invoiced_margin_usd"),),
    ),
    Relation(
        name="margin_by_vendor",
        region_column=None,
        cost_columns=("cost_usd",),
        recovering_sets=(("revenue_usd", "invoiced_margin_usd"),),
        why_finance_wide=(
            "vendor grain crosses every region, so a regional cut of it is a "
            "different table rather than a filtered one"
        ),
    ),
    Relation(
        name="margin_by_product_line",
        region_column=None,
        cost_columns=("cost_usd",),
        recovering_sets=(("revenue_usd", "invoiced_margin_usd"),),
        why_finance_wide=(
            "product line grain crosses every region for the same reason as "
            "vendor"
        ),
    ),
    Relation(
        name="recon_billing_to_gl",
        region_column=None,
        cost_columns=(),
        recovering_sets=(),
        why_finance_wide=(
            "the ledger posts at account grain and carries no partner, so "
            "there is nothing on a ledger entry to filter a region on. The "
            "variance identity also holds over the whole book and not over a "
            "slice of it, so a filtered reconciliation would add up to "
            "nothing a reader could act on"
        ),
    ),
    Relation(
        name="credit_rollup_by_line",
        region_column=None,
        cost_columns=(),
        recovering_sets=(),
        why_finance_wide=(
            "it carries no region column, so there is nothing on a row to "
            "filter on. It is in this list because the survey flags any mart "
            "holding a revenue and a margin column together and this one does. "
            "The decision is that its columns are credit amounts applied "
            "against cost rather than vendor cost, and no arithmetic over them "
            "returns vendor cost, so the restricted quantity is absent. The "
            "rebate figures are still commercially sensitive, which is the "
            "reason a regional role is refused the table rather than given it "
            "unfiltered"
        ),
    ),
    # The layout pair. Both halves hold every line of the margin table including
    # cost and region, in the same dataset the reporting roles hold, because they
    # exist to be queried twice and compared. A control on the line table that
    # does not cover these two is a control on one of three copies.
    #
    # They are not a reporting surface, so no role gets a view. The row policy
    # naming finance alone is what refuses them on BigQuery.
    Relation(
        name="scan_margin_lines_unclustered",
        region_column="region",
        cost_columns=("extended_cost_usd",),
        recovering_sets=(("extended_revenue_usd", "invoiced_margin_usd"),),
        reporting_surface=False,
    ),
    Relation(
        name="scan_margin_lines_clustered",
        region_column="region",
        cost_columns=("extended_cost_usd",),
        recovering_sets=(("extended_revenue_usd", "invoiced_margin_usd"),),
        reporting_surface=False,
    ),
)

RELATIONS_BY_NAME = {relation.name: relation for relation in RELATIONS}

# The line table's region is null exactly where the invoice carries no partner.
# Measured rather than assumed, by tests/test_access.py against the corpus.
UNREACHABLE_BY_REGION = "region is null"


def withheld_columns(relation: Relation, role: Role) -> tuple[str, ...]:
    """The columns a role may not select, closed over the recovery sets.

    A role that may read cost is withheld nothing. For a role that may not,
    every column that is cost goes, and then each recovering set has to be
    broken. Exactly one of revenue and margin has to go, and the choice is not
    a matter of taste.

    Revenue is an input and margin is what this warehouse computes. A role
    holding revenue from anywhere else, the staging layer or the billing system
    or the invoices it already receives, can subtract margin and get cost. So
    withholding revenue here protects nothing it can get elsewhere. Margin is
    the one number in the pair that exists nowhere outside these marts, which
    makes it the only one withholding actually withholds.

    What that costs is real and it is in the README. A regional role in this
    design reads revenue and not margin, so it is not a margin consumer. If
    regional margin visibility is wanted, the answer is a mart at a grain where
    cost cannot be backed out rather than a column mask on this one.

    The ordering matters and it is the reason this returns a sorted tuple. A
    caller builds a column list from it and a list whose order depends on a
    set iteration produces a different view definition on every run.
    """
    if role.may_read_cost:
        return ()
    withheld = set(relation.cost_columns)
    for recovering in relation.recovering_sets:
        if not set(recovering) & withheld:
            withheld.add(recovering[-1])
    return tuple(sorted(withheld))


def readable_columns(
    relation: Relation, role: Role, all_columns: tuple[str, ...]
) -> tuple[str, ...]:
    """The columns of `relation` this role may select, in warehouse order.

    `all_columns` is read out of the warehouse by the caller. Taking it as an
    argument rather than hard coding a column list is what stops this module
    from going stale when a model gains a column, and it is also why a column
    this module has never heard of defaults to readable. A default of withheld
    would be safer and it would also mean a new revenue column disappears from
    every regional report with nothing failing.
    """
    withheld = set(withheld_columns(relation, role))
    return tuple(column for column in all_columns if column not in withheld)


def row_predicate(relation: Relation, role: Role) -> str | None:
    """The SQL a row filter for this pair has to apply, or None for no filter.

    None means every row, which is what a finance wide relation gives a non
    regional role. A regional role against a finance wide relation gets no
    access at all rather than an unfiltered view, and that case returns the
    string `false` so a caller cannot mistake it for the no filter case.
    """
    if not role.is_regional:
        return None
    if relation.region_column is None:
        return "false"
    quoted = ", ".join(f"'{region}'" for region in role.regions or ())
    return f"{quote_identifier(relation.region_column)} in ({quoted})"


def tables_with_row_policies() -> tuple[Relation, ...]:
    """The relations that carry a row access policy at all.

    A relation with no region column carries none, and access to it is decided
    by whether the role holds the dataset at all. Mixing the two mechanisms on
    one table is how a reader ends up unable to say which one refused them.
    """
    return tuple(r for r in RELATIONS if r.region_column is not None)


def bigquery_filter_predicate(relation: Relation, role: Role) -> str | None:
    """The BigQuery policy predicate for a pair, or None for no policy.

    This is NOT `row_predicate` and the difference is the most useful thing in
    this module. BigQuery evaluates row access policies as a union of the
    policies the principal is a grantee of, so once a table carries one policy
    a principal with no policy on that table reads zero rows rather than all of
    them. A view layer does the opposite. A view with no where clause returns
    everything.

    So the two spellings of one rule list diverge exactly here, and the role
    that reads the whole book is the one it bites. `finance_analyst` needs an
    explicit true predicate on every table that carries any policy, and leaving
    it out gives finance an empty margin table with nothing failing anywhere.
    What runs locally cannot prove this, because the engine that behaves this
    way has never been connected to.
    """
    if relation not in tables_with_row_policies():
        return None
    if not relation.reporting_surface:
        # One policy, granted to the roles that may read cost. Every other
        # principal falls through the union with no policy of its own and reads
        # nothing, which is the refusal. A predicate of false granted to
        # everybody would read as more deliberate and it would also be a policy
        # somebody could widen by editing one word.
        return "true" if role.may_read_cost else None
    if not role.is_regional:
        return "true"
    quoted = ", ".join(f"'{region}'" for region in role.regions or ())
    return f"{relation.region_column} in ({quoted})"


def grants_for(role: Role) -> tuple[Relation, ...]:
    """The relations this role may read at all.

    A regional role is refused a finance wide relation outright. Handing it an
    empty view instead would look like a table with no data in it, and the
    difference between no access and no rows is the first thing somebody debugs
    for an afternoon.
    """
    surfaces = tuple(r for r in RELATIONS if r.reporting_surface)
    if not role.is_regional:
        return surfaces
    return tuple(r for r in surfaces if r.region_column is not None)


def quote_identifier(name: str) -> str:
    """Double quote an identifier and refuse anything that cannot be one.

    Every identifier in the generated SQL goes through here. The refusal is the
    point rather than the quoting. A relation or column name arriving from a
    rule list is not user input today and the generator is the thing that would
    make it one.
    """
    if not name or not all(ch.isalnum() or ch == "_" for ch in name):
        raise ValueError(f"{name!r} is not a bare identifier")
    if name[0].isdigit():
        raise ValueError(f"{name!r} starts with a digit")
    return f'"{name}"'


def role_schema(role: Role) -> str:
    """The DuckDB schema holding this role's views.

    One schema per role rather than one view per role in a shared schema. A
    reader of the catalogue can then see what a role can reach by listing a
    schema, which is also how the suite enumerates it.
    """
    return f"acl_{role.name}"


def create_view_sql(
    relation: Relation,
    role: Role,
    all_columns: tuple[str, ...],
    source_schema: str,
) -> str:
    """The view that enforces both rules for one pair.

    A view rather than a grant, because DuckDB has no role system to grant
    against. What that costs is in the README and it is not small. The view
    refuses the column by not projecting it, so the refusal arrives as a binder
    error naming the column, and that is a real refusal from the engine rather
    than a string this repository checked for itself.

    Returned as text rather than executed so the suite can grade the SQL on a
    machine with no warehouse.
    """
    columns = readable_columns(relation, role, all_columns)
    if not columns:
        raise ValueError(f"{role.name} would get an empty projection of {relation.name}")
    projection = ", ".join(quote_identifier(column) for column in columns)
    target = f"{quote_identifier(role_schema(role))}.{quote_identifier(relation.name)}"
    source = f"{quote_identifier(source_schema)}.{quote_identifier(relation.name)}"
    predicate = row_predicate(relation, role)
    where = "" if predicate is None else f" where {predicate}"
    return f"create or replace view {target} as select {projection} from {source}{where}"


def rules_as_json() -> dict:
    """The rule list in the shape the Terraform reads.

    Terraform cannot import this module, so the alternative to emitting it is
    writing the roles out again in HCL. Two copies of an access rule is the
    failure this whole module exists to avoid, so the HCL reads a generated
    file and tests/test_access.py fails when the committed file and this
    function disagree.

    Only the BigQuery enforceable half is emitted. The withheld column closure
    is not in here, because a policy tag restricts a column by name and the
    closure is the reason the tag list carries margin as well as cost.
    """
    return {
        "restricted_quantity": COST,
        # Name and cost verdict only. The regions a role covers are in its row
        # access policy predicate and nothing read a second copy of them, so the
        # second copy is gone. A field in a generated file that no consumer reads
        # is there to look like output.
        "roles": [
            {"name": role.name, "may_read_cost": role.may_read_cost}
            for role in ROLES
        ],
        "row_access_policies": [
            {
                "policy_id": f"{relation.name}__{role.name}",
                "table_id": relation.name,
                "role": role.name,
                "filter_predicate": bigquery_filter_predicate(relation, role),
            }
            for relation in tables_with_row_policies()
            for role in ROLES
            if bigquery_filter_predicate(relation, role) is not None
        ],
        "tagged_columns": [
            {"table_id": relation.name, "column": column}
            for relation in RELATIONS
            for column in sorted(
                set(relation.cost_columns)
                | {
                    recovering[-1]
                    for recovering in relation.recovering_sets
                    if not set(recovering) & set(relation.cost_columns)
                }
            )
        ],
    }
