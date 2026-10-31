"""The daily run as an Airflow DAG.

    airflow dags list --subdir orchestration/dags

The graph is built from `gross_margin.pipeline.STEPS` and this file holds no
step list of its own. That is the rule the access layer follows. The rules
live in one module and generate both enforcements rather than being restated
in each. A DAG that described the pipeline a second time would drift from the
scripts it runs with nothing to catch it.

Every task is a BashOperator over the argv the declaration carries. No step is
reimplemented as a PythonOperator calling into the library, because a task that
imports the project runs the project inside the scheduler's interpreter, and
then the scheduler's dependency set and the pipeline's are one set. On Composer
that is the difference between a dbt upgrade being a worker image change and it
being an environment rebuild.

WHAT HAS NOT HAPPENED. This DAG has never run under a scheduler. It is parsed
by `airflow.models.dagbag` in the sandbox, which is what catches an import
error, a cycle and a bad schedule, and parsing is not running. There is no
Composer environment behind it because there is no project. `infra/composer.tf`
declares one and has never been applied.

The retry policy is the one place a default would be wrong. `build_corpus` is
deterministic and retrying it is free. `transform` writes the warehouse and a
retry re-runs dbt, which is idempotent here because every model is a table or
a view rebuilt in full, except the allocation incremental, which is keyed on
credit_id with delete and insert so a second pass replaces its own rows. The
reopen job and the probes only read. So retries are safe on every task and the
reason is per task rather than global.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.models.dag import DAG
from airflow.operators.bash import BashOperator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import pipeline

# Where the repository lives on the worker. Composer syncs the dags folder out
# of a bucket and the rest of the repository is not in it, so a real
# environment installs the project on the image and points this at it. The
# default is the checkout this file sits in, which is what makes the DAG
# parseable in a sandbox and runnable on a developer machine.
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

DEFAULT_ARGS = {
    "owner": "commercial-finance",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    # The close cares about the figures being right rather than early, and a
    # task that fails twice is a signal rather than something to wait on.
    "depends_on_past": False,
    "email_on_failure": False,
}

with DAG(
    dag_id="gross_margin_daily",
    description="Generate, transform, reopen the periods still in reach, probe access",
    # 06:00 UTC, which is after the overnight ERP extract and before the
    # European close team starts. A real deployment reads this off the source
    # system's own schedule and this value is a placeholder with a reason
    # rather than a measured one.
    schedule="0 6 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    # One run at a time. The transform step writes a single DuckDB file and two
    # writers would corrupt it, and on BigQuery two concurrent full rebuilds of
    # the same tables is a billed way to get the same answer.
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["finance", "dbt", "gross-margin"],
) as dag:
    tasks = {}
    for step in pipeline.STEPS:
        tasks[step.name] = BashOperator(
            task_id=step.name,
            bash_command=f"cd {PROJECT_DIR} && " + " ".join(step.command),
            doc_md=step.why,
        )

    for upstream, downstream in pipeline.edges():
        tasks[upstream] >> tasks[downstream]
