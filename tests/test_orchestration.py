"""The DAG against the declaration it is supposed to be wiring over.

None of these import Airflow. The suite has to run on a machine with no
dependencies at all, which is the property that lets it grade a clean clone,
and Airflow 2 does not install on Python 3.13 anyway. So the DAG file is read
as source and the questions asked of it are the ones that do not need a
scheduler: does it name every step, does it name anything that is not a step,
and does it hold logic that belongs in the library.

What this cannot answer is whether the file parses under Airflow. That needs
`airflow.models.dagbag` and an Airflow install, which is a sandbox step
recorded in the measurement file rather than a check here.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from gross_margin import manifest, pipeline

REPO_ROOT = Path(__file__).resolve().parent.parent
DAG_FILE = REPO_ROOT / "orchestration" / "dags" / "gross_margin_daily.py"
PARSE_FILE = REPO_ROOT / "measurements" / "dag-parse.json"
COMPOSER_TF = REPO_ROOT / "infra" / "composer.tf"
PINS_FILE = REPO_ROOT / "infra" / "pipeline-pins.json"
REQUIREMENTS = REPO_ROOT / "requirements.txt"


def _dag_source() -> str:
    return DAG_FILE.read_text(encoding="utf-8")


def check_the_dag_builds_its_tasks_from_the_declaration():
    """No literal task id in the DAG file.

    A BashOperator constructed with `task_id="transform"` would work and it
    would also be the orchestration layer holding its own copy of the step
    list. The loop over pipeline.STEPS is the whole design and this is what
    stops the next edit from quietly adding one task by hand beside it.
    """
    source = _dag_source()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "BashOperator":
            continue
        for keyword in node.keywords:
            if keyword.arg == "task_id" and isinstance(keyword.value, ast.Constant):
                raise AssertionError(
                    f"a task id is written as the literal {keyword.value.value!r} "
                    "in the DAG, so the graph no longer comes only from "
                    "gross_margin.pipeline"
                )


def check_the_dag_runs_the_commands_the_declaration_names():
    """Every step's script is named by the declaration and exists on disk."""
    for step in pipeline.STEPS:
        script = step.command[-1]
        if not (REPO_ROOT / script).exists():
            raise AssertionError(
                f"step {step.name} runs {script} and that file does not exist"
            )


def check_every_step_is_reachable_from_the_first_one():
    """A step with no path from the root never runs and nothing says so."""
    edges = pipeline.edges()
    downstream: dict[str, list[str]] = {name: [] for name in pipeline.step_names()}
    for upstream, target in edges:
        downstream[upstream].append(target)
    roots = [
        step.name for step in pipeline.STEPS if not step.after
    ]
    if len(roots) != 1:
        raise AssertionError(
            f"{roots} have no upstream, and a daily run with two entry points "
            "needs a reason written down"
        )
    seen = set()
    stack = list(roots)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(downstream[current])
    missing = sorted(set(pipeline.step_names()) - seen)
    if missing:
        raise AssertionError(f"{missing} cannot be reached from {roots[0]}")


def check_the_step_graph_has_no_cycle():
    remaining = {
        step.name: set(step.after) for step in pipeline.STEPS
    }
    while remaining:
        ready = [name for name, after in remaining.items() if not after]
        if not ready:
            raise AssertionError(
                f"the remaining steps {sorted(remaining)} depend on each other"
            )
        for name in ready:
            del remaining[name]
        for after in remaining.values():
            after.difference_update(ready)


def check_every_step_says_why_it_is_there():
    for step in pipeline.STEPS:
        if len(step.why.split()) < 8:
            raise AssertionError(
                f"step {step.name} carries no real explanation, and a task list "
                "with no reasons is the thing a reader of the DAG needs most"
            )


def check_the_dag_holds_no_sql_and_no_access_rule():
    """The orchestration layer is wiring and not a third copy of the rules.

    Every access rule lives in one module so that two enforcements can be
    generated from it rather than restated. A DAG that filtered or masked
    anything would be a third place to keep current.
    """
    source = _dag_source().lower()
    for token in ("select ", "policy_tag", "filter_predicate", "row_access"):
        if token in source:
            raise AssertionError(
                f"the DAG contains {token!r}, so it is doing work that belongs "
                "in the models or in gross_margin/access.py"
            )


def check_the_dag_parse_was_recorded_and_found_no_import_error():
    parsed = manifest.read(PARSE_FILE)
    if parsed["import_errors"]:
        raise AssertionError(
            f"the recorded DAG parse carried import errors: {parsed['import_errors']}"
        )
    if parsed["tasks"] != len(pipeline.STEPS):
        raise AssertionError(
            f"the parsed DAG had {parsed['tasks']} tasks and the declaration "
            f"carries {len(pipeline.STEPS)}"
        )
    if sorted(parsed["task_ids"]) != sorted(pipeline.step_names()):
        raise AssertionError(
            "the parsed DAG's task ids are not the declared step names"
        )
    recorded_edges = {tuple(edge) for edge in parsed["edges"]}
    if recorded_edges != set(pipeline.edges()):
        raise AssertionError(
            "the parsed DAG's dependencies are not the declared ones"
        )


def check_the_recorded_parse_is_not_a_claim_that_the_dag_ran():
    parsed = manifest.read(PARSE_FILE)
    if parsed.get("scheduled_run") is not False:
        raise AssertionError(
            "the parse measurement does not state that no scheduled run has "
            "happened, and a parse is not a run"
        )


def check_the_composer_environment_takes_its_image_version_from_a_variable():
    """A typed image version would be a guess that validate cannot catch.

    Whether that variable carries a default is asked by
    `check_no_project_identifier_carries_a_default` in tests/test_infra.py,
    which strips the comments first and already covers the other three
    identifiers. This half is the DAG story's own: the image version pins the
    Airflow release the scheduler runs, so it has to be a decision somebody
    makes per environment rather than a literal in a file.
    """
    text = COMPOSER_TF.read_text(encoding="utf-8")
    if "image_version = var.composer_image_version" not in text:
        raise AssertionError(
            "composer.tf does not take its image version from a variable, so the "
            "Airflow release the scheduler runs is pinned in a file nobody "
            "reviews per environment"
        )


def check_the_scheduler_cannot_rewrite_the_datasets_at_the_project_level():
    """Dataset level write, project level job runner, and nothing wider.

    `roles/bigquery.dataEditor` at the project would let a DAG with a bug on it
    rewrite anything in the project, and the column security in access.tf is
    worth nothing against that.
    """
    text = COMPOSER_TF.read_text(encoding="utf-8")
    project_block = text.split('resource "google_project_iam_member" "composer"', 1)
    if len(project_block) != 2:
        raise AssertionError("composer.tf grants the service account no project role")
    roles_block = text.split("composer_project_roles", 1)[1].split("]", 1)[0]
    for role in ("dataEditor", "dataOwner", "admin", "editor", "owner"):
        if role in roles_block:
            raise AssertionError(
                f"the composer service account holds {role} at the project level"
            )
    if 'role       = "roles/bigquery.dataEditor"' not in text:
        raise AssertionError(
            "the scheduler has no dataset level write, so it cannot materialise "
            "the models it is being asked to build"
        )


def check_the_scheduler_image_carries_the_pins_the_pipeline_declares():
    """The generated pin file against requirements.txt, with no second copy.

    The Composer environment installs the project's dependencies on its image.
    If that list drifts from `requirements.txt` the scheduler runs a different
    dbt from the one the suite was graded against, and the symptom is a model
    that builds here and not there.
    """
    import subprocess

    done = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "emit_pipeline_pins.py"), "--check"],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise AssertionError(
            "infra/pipeline-pins.json is not what requirements.txt would produce: "
            + (done.stderr or done.stdout).strip()
        )
    declared = json.loads(PINS_FILE.read_text(encoding="utf-8"))["pypi_packages"]
    text = COMPOSER_TF.read_text(encoding="utf-8")
    if "jsondecode(file(" not in text:
        raise AssertionError(
            "composer.tf does not read the generated pin file, so the image and "
            "requirements.txt are two lists somebody keeps current"
        )
    for name in declared:
        if f'"{name}"' in text:
            raise AssertionError(
                f"composer.tf names the package {name} directly, which is the "
                "second copy the generated file exists to avoid"
            )
