"""The reopen plan's arithmetic, and whether it still names real columns.

The check worth reading first is
`check_every_unsettled_term_is_a_column_the_reconciliation_publishes`. The plan
decides which periods to recompute by looking for three named columns on the
reconciliation rows, and a column read by name off a dict answers None when it
is not there. None is falsey, so renaming `awaiting_the_ledger_cents` in the
model would make every period look settled and the job would reopen nothing
and report success. That is the failure this module exists for.
"""

from __future__ import annotations

from pathlib import Path

from gross_margin import manifest, reopen

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_FILE = REPO_ROOT / "measurements" / "reopen-plan.json"
RECON_MODEL = (REPO_ROOT / "transform" / "models" / "marts"
               / "recon_billing_to_gl.sql")


def _plan_measurement() -> dict:
    return manifest.read(PLAN_FILE)


def _row(period: str, *, inside: bool, **terms) -> dict:
    row = {
        "period": period,
        "account": "4000",
        "inside_the_reopen_window": inside,
        "unposted_cents": 0,
        "awaiting_the_ledger_cents": 0,
        "amount_difference_cents": 0,
        "ledger_only_cents": 0,
        "from_another_period_cents": 0,
    }
    row.update(terms)
    return row


def check_the_two_term_lists_do_not_overlap():
    both = set(reopen.UNSETTLED_TERMS) & set(reopen.SETTLED_ELSEWHERE_TERMS)
    if both:
        raise AssertionError(
            f"{sorted(both)} is in both term lists, so the module both does and "
            "does not treat it as a reason to reopen"
        )


def check_every_unsettled_term_is_a_column_the_reconciliation_publishes():
    sql = RECON_MODEL.read_text(encoding="utf-8")
    for term in reopen.UNSETTLED_TERMS + reopen.SETTLED_ELSEWHERE_TERMS:
        if f"as {term}" not in sql:
            raise AssertionError(
                f"{term} is read by the plan and {RECON_MODEL.name} does not "
                "publish it, so the plan reads a null and treats the period as "
                "settled"
            )


def check_a_period_outside_the_window_is_never_reopened():
    rows = [_row("2024-01", inside=False, unposted_cents=50_000)]
    plan = reopen.plan(rows)
    if plan.periods:
        raise AssertionError(
            "a closed period with an unposted document was reopened, which is "
            "what the alert exists for instead"
        )
    if plan.periods_outside_the_window != 1:
        raise AssertionError("the closed period was not counted as closed")


def check_a_period_inside_the_window_with_nothing_unsettled_is_left_alone():
    rows = [_row("2026-05", inside=True, ledger_only_cents=900)]
    plan = reopen.plan(rows)
    if plan.periods:
        raise AssertionError(
            "a period whose only term is counted in another period was reopened, "
            "so the plan reopens for an event that happens elsewhere"
        )
    if plan.settled_inside_the_window != 1:
        raise AssertionError("the settled period was not reported as settled")


def check_the_counts_inside_the_window_add_up():
    rows = [
        _row("2026-01", inside=True, unposted_cents=10),
        _row("2026-02", inside=True),
        _row("2026-03", inside=True, amount_difference_cents=-7),
        _row("2023-01", inside=False),
    ]
    plan = reopen.plan(rows)
    total = len(plan.reopen) + plan.settled_inside_the_window
    if total != plan.periods_in_the_window:
        raise AssertionError(
            f"{len(plan.reopen)} reopened plus {plan.settled_inside_the_window} "
            f"settled is {total}, against {plan.periods_in_the_window} in the "
            "window, so a period inside the window is in neither bucket"
        )


def check_an_unsettled_amount_that_nets_to_zero_still_reopens():
    """Two accounts cancelling is still two documents somebody has to deal with.

    The reconciliation carries the terms apart for this reason at period
    level. The plan has to hold the same line one level up, or a period with a
    positive break on one account and a negative one on another reads clean.
    """
    rows = [
        _row("2026-04", inside=True, amount_difference_cents=5_000),
        dict(_row("2026-04", inside=True, amount_difference_cents=-5_000),
             account="5000"),
    ]
    plan = reopen.plan(rows)
    if len(plan.reopen) != 1:
        raise AssertionError("a netting period was dropped from the reopen set")
    if plan.reopen[0].unsettled_cents != 10_000:
        raise AssertionError(
            f"the netting period reports {plan.reopen[0].unsettled_cents} "
            "unsettled cents, so the sum is signed and two cancelling breaks "
            "read as none"
        )


def check_an_empty_plan_scopes_to_nothing_rather_than_to_everything():
    predicate = reopen.scope_predicate([])
    if predicate.replace(" ", "") != "1=0":
        raise AssertionError(
            f"an empty plan produced {predicate!r}. A scope that is true when "
            "the plan is empty recomputes the whole history on the day the "
            "plan correctly found nothing to do"
        )


def check_the_scope_predicate_names_every_planned_period():
    periods = ("2026-02", "2026-03")
    predicate = reopen.scope_predicate(periods)
    for period in periods:
        if f"'{period}'" not in predicate:
            raise AssertionError(f"{period} is in the plan and not in the scope")


def check_a_period_in_the_reopen_set_carries_a_reason():
    try:
        reopen.PeriodToReopen(
            period="2026-02", accounts=1, reasons=(), unsettled_cents=0
        )
    except ValueError:
        return
    raise AssertionError(
        "a period was constructed with no reason, so the selection rule and the "
        "printed reasons can disagree without anything failing"
    )


def check_the_committed_plan_matches_the_declared_window():
    plan = _plan_measurement()
    from tests.test_recon import declared_reopen_days

    declared = declared_reopen_days()
    if plan["declared_window_days"] != declared:
        raise AssertionError(
            f"the plan was measured against a {plan['declared_window_days']} day "
            f"window and dbt_project.yml declares {declared}"
        )


def check_the_committed_plan_reopened_less_than_the_whole_history():
    plan = _plan_measurement()
    if plan["periods_reopened"] >= plan["periods_total"]:
        raise AssertionError(
            f"{plan['periods_reopened']} of {plan['periods_total']} periods were "
            "reopened, so the window and the term rule together excluded nothing "
            "and a scoped rerun is a full rebuild"
        )
    recomputed = plan["recomputation"]["rows_recomputed"]
    if recomputed >= plan["rows_a_full_rebuild_would_touch"]:
        raise AssertionError(
            f"the scoped recomputation touched {recomputed} rows against "
            f"{plan['rows_a_full_rebuild_would_touch']} for a full rebuild"
        )


def check_the_scoped_recomputation_agreed_with_the_stored_table():
    plan = _plan_measurement()
    recomputation = plan["recomputation"]
    if recomputation["rows_that_disagree"] != 0:
        raise AssertionError(
            f"{recomputation['rows_that_disagree']} period and account rows "
            "disagree between the scoped rerun and the full build"
        )
    if recomputation["rows_recomputed"] != recomputation[
        "rows_stored_for_the_same_periods"
    ]:
        raise AssertionError(
            "the scoped rerun and the stored table returned different row counts "
            "for the same periods, which a per row comparison cannot see"
        )
    if recomputation["rows_recomputed"] == 0:
        raise AssertionError(
            "nothing was recomputed, so the comparison compared nothing and "
            "reported agreement"
        )
