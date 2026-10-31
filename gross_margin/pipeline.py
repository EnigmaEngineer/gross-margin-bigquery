"""The daily pipeline as a declaration, read by the DAG and by the check.

The orchestration day's risk is that the DAG becomes a second description of
the pipeline, so the graph and the thing it runs drift and nothing notices.
The access layer hit the same shape, where one rule list has to generate both
enforcements rather than be restated in each. A DAG that touches the marts
must not become a third copy of those rules.

So the steps and their order live here, in a module with no Airflow import and
no warehouse import, and two things read it. `orchestration/dags` builds the
task graph from it. `tests/test_orchestration.py` asserts the DAG file names
every step and nothing else, which is a check that can run on a machine with
no Airflow at all.

What this is NOT is a scheduler, and it is not an abstraction over one. There
is one implementation and it is Airflow. The declaration exists because two
readers need the same list, not to leave room for a second engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Step:
    """One task in the daily run.

    `command` is the argv the step runs, relative to the repository root. It
    is spelled out rather than built from a template so that reading this file
    tells you exactly what the DAG will execute.
    """

    name: str
    command: tuple[str, ...]
    after: tuple[str, ...] = field(default_factory=tuple)
    why: str = ""


# Order matters and the dependencies are the real ones rather than a straight
# line. The access probe does not depend on the reopen job, because the two
# read the same built marts and neither writes what the other reads, so an
# orchestrator that runs them together is correct and a linear chain would be
# slower for no reason.
STEPS: tuple[Step, ...] = (
    Step(
        name="build_corpus",
        command=("python3", "scripts/build_corpus.py"),
        why=(
            "The generated ERP corpus. Deterministic from a string seed and "
            "byte identical across 3.10 and 3.13, so a rerun of this step "
            "cannot be the reason a downstream figure moved."
        ),
    ),
    Step(
        name="transform",
        command=("python3", "scripts/run_transform.py"),
        after=("build_corpus",),
        why=(
            "Loads raw, runs dbt build against duckdb, parses against "
            "bigquery and records the shape. The only step that writes the "
            "warehouse."
        ),
    ),
    Step(
        name="reopen_periods",
        command=("python3", "scripts/reopen_periods.py"),
        after=("transform",),
        why=(
            "Reads the reconciliation, decides which periods inside the "
            "window can still move, recomputes those and compares the answer "
            "against the table. This is the step the window existed for."
        ),
    ),
    Step(
        name="recon_report",
        command=("python3", "scripts/recon_report.py"),
        after=("transform",),
        why="The period and account view of the variance, for the close pack.",
    ),
    Step(
        name="access_probe",
        command=("python3", "scripts/access_probe.py"),
        after=("transform",),
        why=(
            "Builds the per role views and runs the attacks. It reads the "
            "rule list in gross_margin/access.py and holds no rules of its "
            "own, which is why the DAG does not either."
        ),
    ),
)


def step_names() -> tuple[str, ...]:
    return tuple(step.name for step in STEPS)


def by_name() -> dict[str, Step]:
    return {step.name: step for step in STEPS}


def edges() -> tuple[tuple[str, str], ...]:
    """Every dependency as an upstream to downstream pair."""
    known = set(step_names())
    out: list[tuple[str, str]] = []
    for step in STEPS:
        for upstream in step.after:
            if upstream not in known:
                raise ValueError(
                    f"{step.name} depends on {upstream}, which is not a step"
                )
            out.append((upstream, step.name))
    return tuple(out)
