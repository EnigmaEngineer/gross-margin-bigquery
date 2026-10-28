"""Grades the transformation layer from the files it commits.

Two halves and the split matters.

The first half reads the dbt project as text. It needs no warehouse, no dbt and
no corpus on disk, so it grades the same on a clone that has never built
anything. That is deliberate. A check that only answers on a machine holding the
data is not a check, and the models are exactly where that mistake is easy to
make, because the thing they describe lives in a database.

The second half re-derives every figure in `measurements/transform-shape.json`.
That file is written by `scripts/run_transform.py` after a real DuckDB build, so
these checks grade the recorded result rather than re-running it.

What is deliberately not graded here is anything about BigQuery execution. No
query in this project has run on BigQuery, and a check pretending to grade that
would be the worst thing in the repository.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gross_margin import manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSFORM = REPO_ROOT / "transform"
MODELS = TRANSFORM / "models"
MACROS = TRANSFORM / "macros"
ANALYSES = TRANSFORM / "analyses"
SHAPE_FILE = REPO_ROOT / "measurements" / "transform-shape.json"


def _model_files() -> list[Path]:
    return sorted(MODELS.rglob("*.sql"))


def _declared_models() -> set[str]:
    """Model names declared in the schema files, and only those.

    The first version of this read every `- name:` under models/ with one regex
    and reported the `raw` source as a model that does not exist. A source block
    and a model block use the same key, so the section has to be tracked rather
    than the line matched. No yaml parser, because this layer is the only thing
    in the repo that would need one and the suite declares no dependencies.
    """
    names: set[str] = set()
    for path in sorted(MODELS.rglob("*.yml")):
        section = None
        for line in path.read_text(encoding="utf-8").splitlines():
            top = re.match(r"^(\w+):", line)
            if top:
                section = top.group(1)
                continue
            if section != "models":
                continue
            entry = re.match(r"^  - name: (\w+)\s*$", line)
            if entry:
                names.add(entry.group(1))
    return names


def _shape() -> dict:
    return manifest.read(SHAPE_FILE)


def check_every_model_is_named_in_a_schema_file():
    """A model nobody declared gets no column tests and nobody notices."""
    declared = _declared_models()
    on_disk = {path.stem for path in _model_files()}
    undeclared = sorted(on_disk - declared)
    if undeclared:
        raise AssertionError(f"these models are in no schema file: {undeclared}")
    phantom = sorted(declared - on_disk)
    if phantom:
        raise AssertionError(f"these models are declared and do not exist: {phantom}")


def check_the_scan_pair_shares_one_body():
    """Both halves must select through the shared macro and hold no select of their own.

    If either half carried its own select then the pair could drift, and a bytes
    scanned difference between two different queries is not a layout measurement.
    """
    for name in ("scan_margin_lines_unclustered", "scan_margin_lines_clustered"):
        text = (MODELS / "scan" / f"{name}.sql").read_text(encoding="utf-8")
        if "scan_margin_body()" not in text:
            raise AssertionError(f"{name} does not call the shared body macro")
        bare = [
            line for line in text.splitlines()
            if line.strip().lower().startswith("select")
        ]
        if bare:
            raise AssertionError(f"{name} carries its own select: {bare}")


def check_only_the_clustered_half_asks_for_a_layout():
    clustered = (MODELS / "scan" / "scan_margin_lines_clustered.sql").read_text(
        encoding="utf-8"
    )
    unclustered = (MODELS / "scan" / "scan_margin_lines_unclustered.sql").read_text(
        encoding="utf-8"
    )
    for wanted in ("partition_by=layout", "cluster_by=layout", "scan_layout(clustered=true)"):
        if wanted not in clustered:
            raise AssertionError(f"the clustered half does not carry {wanted!r}")
    for banned in ("partition_by", "cluster_by", "scan_layout"):
        if banned in unclustered:
            raise AssertionError(
                f"the control half mentions {banned!r}, so the pair differs in more "
                "than the layout it was built to isolate"
            )


def check_the_cluster_keys_are_the_ones_the_queries_group_by():
    """The keys are a claim about the three queries, so grade the claim.

    A clustering key list that nothing in analyses/ groups by is a guess written
    into the model, and the measurement it is waiting on would then be measuring
    somebody's guess.
    """
    layout = (MACROS / "scan_layout.sql").read_text(encoding="utf-8")
    keys = re.search(r"'cluster_by':\s*\[([^\]]+)\]", layout)
    if not keys:
        raise AssertionError("macros/scan_layout.sql declares no cluster_by list")
    declared = [k.strip().strip("'") for k in keys.group(1).split(",")]
    queries = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(ANALYSES.glob("*.sql"))
    )
    unused = [key for key in declared if key not in queries]
    if unused:
        raise AssertionError(
            f"these cluster keys appear in no finance query: {unused}"
        )
    partition = re.search(r"'field':\s*'(\w+)'", layout)
    if not partition:
        raise AssertionError("macros/scan_layout.sql declares no partition field")
    if partition.group(1) not in queries:
        raise AssertionError(
            f"the partition column {partition.group(1)!r} is filtered by no query"
        )


def check_the_adapter_specific_syntax_stays_in_the_macros():
    """One spelling of money and one of a date difference, both behind a macro.

    `numeric` is a BigQuery type name and DuckDB has no alias meaning the same
    thing, and date_diff takes its arguments in a different order on each. Either
    one written straight into a model compiles on one warehouse and not the
    other, which is the whole failure this layer exists to avoid.
    """
    offenders = {}
    for path in _model_files():
        text = path.read_text(encoding="utf-8")
        hits = []
        if re.search(r"as\s+numeric\s*\)", text, re.IGNORECASE):
            hits.append("a bare numeric cast")
        if re.search(r"(?<!dbt\.)date_diff\s*\(", text):
            hits.append("a bare date_diff")
        if re.search(r"::", text):
            hits.append("a postgres style cast")
        if re.search(r"as\s+(float|double)\s*\)", text, re.IGNORECASE):
            hits.append("a float cast on money")
        if hits:
            offenders[str(path.relative_to(TRANSFORM))] = hits
    if offenders:
        raise AssertionError(f"adapter specific syntax outside the macros: {offenders}")


def check_the_fact_table_keeps_a_line_whose_dimension_is_missing():
    """Every join out of the line grain is a left join, and that is load bearing.

    An inner join to the partner dimension would drop the invoices that carry no
    partner, and the measurement records what that would cost.
    """
    text = (MODELS / "marts" / "fct_invoice_line_margin.sql").read_text(encoding="utf-8")
    for dimension in ("stg_partners", "dim_product_version"):
        pattern = re.compile(r"inner join[^\n]*" + dimension)
        if pattern.search(text):
            raise AssertionError(f"{dimension} is inner joined, so rows can vanish")
    if "left join" not in text:
        raise AssertionError("the fact table has no left join at all")


def check_every_finance_query_reads_the_pair_through_one_name():
    for path in sorted(ANALYSES.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        if "scan_relation()" not in text:
            raise AssertionError(
                f"{path.name} names a table directly, so the probe cannot point it "
                "at the other half of the pair"
            )
        for half in ("scan_margin_lines_unclustered", "scan_margin_lines_clustered"):
            if half in text.replace("scan_relation()", ""):
                raise AssertionError(f"{path.name} hardcodes {half}")


def check_the_margin_agrees_across_every_grain():
    margin = _shape()["margin"]
    totals = {key: value for key, value in margin.items() if key.endswith("_cents")}
    if len(totals) < 4:
        raise AssertionError(f"only {len(totals)} grains were recorded, expected four")
    if len(set(totals.values())) != 1:
        raise AssertionError(f"the grains disagree: {totals}")


def check_the_scan_pair_covers_the_whole_fact_table():
    shape = _shape()
    rows = shape["rows"]
    fact = rows["main_marts.fct_invoice_line_margin"]
    for half in (
        "main_marts.scan_margin_lines_unclustered",
        "main_marts.scan_margin_lines_clustered",
    ):
        if rows[half] != fact:
            raise AssertionError(
                f"{half} holds {rows[half]} rows and the fact table holds {fact}"
            )


def check_the_two_targets_resolved_different_layouts():
    """The one BigQuery claim this repository can stand behind today.

    `dbt parse` does not open a connection, so the layout the bigquery branch
    produces is readable with no credentials. If both targets came back the same
    then the adapter dispatch in macros/scan_layout.sql is not working and nobody
    would find out until the day there is a project to run against.
    """
    layouts = _shape()["layout_resolved_by_target"]
    bigquery = layouts["bigquery"]["scan_margin_lines_clustered"]
    duckdb = layouts["duckdb"]["scan_margin_lines_clustered"]
    if not bigquery.get("cluster_by"):
        raise AssertionError("the bigquery target resolved no cluster_by")
    if (bigquery.get("partition_by") or {}).get("granularity") != "month":
        raise AssertionError(f"the bigquery partition is {bigquery.get('partition_by')}")
    if duckdb.get("cluster_by") or duckdb.get("partition_by"):
        raise AssertionError(
            f"the duckdb target resolved a layout it cannot build: {duckdb}"
        )
    control = layouts["bigquery"]["scan_margin_lines_unclustered"]
    if control.get("cluster_by") or control.get("partition_by"):
        raise AssertionError(f"the control half resolved a layout: {control}")


def check_no_bytes_scanned_figure_is_published_anywhere():
    """The rule this day turns on. There is no number, so nothing may state one.

    Checked in the measurement file and in the README, because a figure invented
    to fill a gap would go in one of the two.
    """
    shape = _shape()
    if shape["bytes_scanned"].get("measured") is not False:
        raise AssertionError("the measurement file claims bytes scanned was measured")
    for key in shape["bytes_scanned"]:
        if key not in ("measured", "why"):
            raise AssertionError(
                f"the bytes scanned block carries {key!r}, which looks like a figure"
            )
    blob = json.dumps(shape)
    for suspect in ("totalBytesProcessed", "bytes_processed", "gb_scanned"):
        if suspect in blob:
            raise AssertionError(f"the measurement file carries {suspect}")

    readme = " ".join((REPO_ROOT / "README.md").read_text(encoding="utf-8").split())
    claims = re.findall(
        r"[\d.,]+\s*(?:bytes|KB|MB|GB|TB)\s+(?:scanned|processed|billed)",
        readme,
        re.IGNORECASE,
    )
    if claims:
        raise AssertionError(f"the README states a scan size: {claims}")


def check_the_probe_refuses_to_run_without_the_access_it_needs():
    """The blocked script has to fail by name rather than print something."""
    import os
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from scripts import scan_probe

    saved = {name: os.environ.pop(name, None) for name in scan_probe.REQUIRED_ENV}
    try:
        reasons = scan_probe.blockers()
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value
    if not reasons:
        raise AssertionError(
            "the probe reports no blocker with the environment cleared, so it would "
            "try to measure against nothing"
        )
    source = (REPO_ROOT / "scripts" / "scan_probe.py").read_text(encoding="utf-8")
    for named in ("workload identity pool", "infra/README.md"):
        if named not in source:
            raise AssertionError(
                f"the probe does not name {named!r}, so a reader cannot find out what "
                "access it is waiting on"
            )


def _readme_flat() -> str:
    """The README with whitespace collapsed.

    Same reason as the other README module. The file is hard wrapped, so a
    sentence carrying a figure is usually split across a newline and a search for
    it on one line finds nothing.
    """
    return " ".join((REPO_ROOT / "README.md").read_text(encoding="utf-8").split())


def check_the_readme_figures_come_from_the_shape_measurement():
    shape = _shape()
    rows = shape["rows"]
    margin = shape["margin"]
    drop = shape["rows_an_inner_join_would_drop"]
    credits = shape["credits"]
    nodes = shape["nodes"]

    def money(cents: int) -> str:
        return f"{cents / 100:,.2f}"

    expected = {
        "node counts": f"{nodes['models']} models and {nodes['data_tests']} data tests",
        "invoiced margin": f"${money(margin['line_grain_cents'])}",
        "lines with no partner": f"{drop['lines_with_no_partner']} invoice lines carry no partner",
        "margin an inner join drops": f"${money(drop['their_margin_cents'])}",
        "reassigned lines": f"{shape['as_of_join']['lines_on_a_reassigned_product']:,} lines sit on a reassigned product",
        "unplaceable credits": f"{credits['unplaceable']} credits name a line",
        "credits over their line": f"{credits['exceeding_the_line_they_adjust']} are worth more than the line",
    }

    # The allocation figures, from the same file. These were typed by hand on
    # the day they were written and one of them was a percentage nobody could
    # recompute, which is the whole reason this function exists.
    allocation = shape["allocation"]
    move = allocation["largest_single_move"]
    share = abs(move["cents"]) / move["that_rows_named_margin_cents"] * 100
    expected.update({
        "vendor periods that moved": (
            f"{allocation['vendor_periods_that_moved']} of the "
            f"{rows['main_marts.margin_by_vendor']}"
        ),
        "largest single move": f"${money(abs(move['cents']))} off one vendor's",
        "that move as a share": f"{share:.1f} percent of what that row claimed",
        "gap between the two totals": (
            f"${money(allocation['by_scope']['invoice']['cents'])} over the whole"
        ),
        "what nobody can place": f"${money(allocation['by_scope']['unallocated']['cents'])}",
    })
    text = _readme_flat()
    missing = {name: want for name, want in expected.items() if want not in text}
    if missing:
        raise AssertionError(f"the README does not carry these derived figures: {missing}")

    # The row table has an address, so it gets compared cell by cell rather than
    # searched for.
    table = dict(
        (name, int(count.replace(",", "")))
        for name, count in re.findall(
            r"^\| (\w+) \| ([\d,]+) \|$",
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    if not table:
        raise AssertionError("the README has no model row count table")
    for name, stated in table.items():
        key = f"main_marts.{name}"
        if key not in rows:
            raise AssertionError(f"the README table names {name}, which the build did not make")
        if stated != rows[key]:
            raise AssertionError(
                f"the README says {name} holds {stated} rows and the build made {rows[key]}"
            )
    # Derived from the measurement rather than counted here. The first version
    # asserted a literal five and the sixth mart would have been met with a
    # failing check that named the wrong problem. The scan pair is left out
    # because it has its own section and its own count.
    expected = {
        key.split(".")[-1] for key in rows
        if not key.split(".")[-1].startswith("scan_")
        and not key.split(".")[-1].startswith("dim_")
    }
    if expected != set(table):
        raise AssertionError(
            f"the table names {sorted(table)} and the marts are {sorted(expected)}"
        )


def check_every_finance_query_was_executed_and_both_halves_agree():
    """Compiling a query is not running it, and that gap hid a parse error.

    A jinja call inside a SQL line comment renders like any other. The relation
    it produced spanned three lines, two of them escaped the comment, and the
    query stopped parsing. Compile exited zero. So the row counts are recorded
    and compared, and the two halves have to agree because they hold the same
    rows.
    """
    results = _shape()["finance_queries"]
    halves = sorted(results)
    if halves != ["scan_margin_lines_clustered", "scan_margin_lines_unclustered"]:
        raise AssertionError(f"the measurement records {halves}, expected both halves")

    on_disk = {path.stem for path in sorted(ANALYSES.glob("*.sql"))}
    for half, queries in results.items():
        if set(queries) != on_disk:
            raise AssertionError(
                f"{half} recorded {sorted(queries)} and the project holds {sorted(on_disk)}"
            )
        empty = [name for name, rows in queries.items() if rows == 0]
        if empty:
            raise AssertionError(
                f"these queries returned no rows against {half}: {empty}. A query "
                "over an empty result grades nothing"
            )
    left, right = (results[h] for h in halves)
    if left != right:
        raise AssertionError(
            f"the two halves disagree on row counts: {left} against {right}"
        )


def check_no_jinja_call_hides_in_a_sql_comment():
    """The exact defect above, refused by shape rather than by memory.

    Checked across the models and the analyses, because the next one will not be
    in the file that taught it.
    """
    offenders = {}
    for path in sorted(ANALYSES.glob("*.sql")) + _model_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith("--"):
                continue
            if "{{" in stripped or "{%" in stripped:
                offenders[f"{path.name}:{number}"] = stripped[:70]
    if offenders:
        raise AssertionError(
            f"a jinja call sits inside a sql comment, which renders: {offenders}"
        )
