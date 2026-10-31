"""Parses the DAG with Airflow and records what came back.

    PYTHONPATH=<airflow> python3.11 scripts/dag_parse.py

Separate from the suite on purpose. Airflow 2 does not install on Python 3.13
and this repository declares a 3.10 floor it exercises on every run, so a check
that imported Airflow would make the whole suite unrunnable on both of the
machines this program has. The suite reads the measurement this writes instead,
which is the same split the warehouse figures already use.

A parse is not a run. It catches an import error, a duplicate task id, a cycle
and a schedule the scheduler cannot read. It does not execute a single task,
and the measurement says so in a field rather than in a comment so that a check
can assert it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import manifest

MEASUREMENT = REPO_ROOT / "measurements" / "dag-parse.json"
DAG_FOLDER = REPO_ROOT / "orchestration" / "dags"


def main() -> int:
    from airflow.models.dagbag import DagBag
    from airflow.version import version as airflow_version

    bag = DagBag(dag_folder=str(DAG_FOLDER), include_examples=False)
    if bag.import_errors:
        for path, error in bag.import_errors.items():
            sys.stderr.write(f"{path}\n{error}\n")
    if len(bag.dags) != 1:
        raise SystemExit(
            f"{len(bag.dags)} dags were found in {DAG_FOLDER} and the daily run "
            "is one dag, so either a file was added or one failed to import"
        )
    (dag_id, dag), = bag.dags.items()

    # Raises on a cycle, a duplicate task id and a few other shapes the DagBag
    # itself does not reject. Called rather than trusted.
    dag.validate()

    payload = {
        "airflow_version": airflow_version,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "dag_id": dag_id,
        "schedule": str(dag.schedule_interval),
        "catchup": bool(dag.catchup),
        "max_active_runs": dag.max_active_runs,
        "tasks": len(dag.tasks),
        "task_ids": sorted(task.task_id for task in dag.tasks),
        "edges": sorted(
            [upstream, task.task_id]
            for task in dag.tasks
            for upstream in task.upstream_task_ids
        ),
        "topological_order": [task.task_id for task in dag.topological_sort()],
        "import_errors": {str(k): str(v) for k, v in bag.import_errors.items()},
        "validate_raised": False,
        # The field the check reads. Parsing proves the file loads and nothing
        # here has ever been handed to a scheduler.
        "scheduled_run": False,
        "rebuilt_by": "PYTHONPATH=<airflow> python3.11 scripts/dag_parse.py",
    }
    manifest.write(MEASUREMENT, payload)
    print(f"airflow {airflow_version} parsed {dag_id}: {payload['tasks']} tasks")
    print("order: " + " -> ".join(payload["topological_order"]))
    print(f"wrote {MEASUREMENT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
