"""Which periods the reconciliation has to recompute, and why each one.

`recon_reopen_days` was a number in `dbt_project.yml` and
`recon_billing_to_gl` published `inside_the_reopen_window` on every row.
Nothing read either one. The window was a word in the prose and a column in a
table, and a reader who took the README literally would have expected a
process that reopens periods. There was no such process. This module is the
half of the answer the suite can grade.

What the window does NOT mean is that every period inside it gets rebuilt. A
period inside the window whose documents are all matched in the same period at
the same amount cannot move when the pipeline runs again, because there is
nothing left to arrive and nothing left to correct. Rebuilding it costs the
same as rebuilding one that can move and buys nothing.

So a period is reopened when both of these hold.

It is inside the window, meaning the reconciliation still considers it in
reach. Outside the window the answer is that the period is closed and a
difference there is an alert rather than a rerun, which is what
`alert_late_credit_arrival` already does.

It carries at least one term that is not settled. Three of the five terms
qualify and two do not. `unposted` is a document billing raised that the
ledger has not received, so a later ledger load changes the period.
`awaiting_the_ledger` is a document the ledger will post into a different
period, so the period changes when it does. `amount_difference` is a keyed
amount that somebody will correct at the source. `ledger_only` and
`from_another_period` are the other halves of the same documents, counted in
the period that received them, and they move only when their origin period
does, so including them would reopen a period for an event that happens
somewhere else.

The distinction is the whole value of the module. Reopening everything inside
the window is one line and needs no reasons, and it is also the version that
cannot say what it saved.
"""

from __future__ import annotations

from dataclasses import dataclass

# The terms whose presence means the period can still change. Spelled as the
# column names the reconciliation publishes rather than as a shorter label,
# because the reason a reader comes here is to check this list against that
# table.
UNSETTLED_TERMS = (
    "unposted_cents",
    "awaiting_the_ledger_cents",
    "amount_difference_cents",
)

# Present in the reconciliation and deliberately not above. Kept as a named
# constant so a reader can see that the omission was decided rather than
# forgotten, and so the check can assert the two lists do not overlap.
SETTLED_ELSEWHERE_TERMS = (
    "ledger_only_cents",
    "from_another_period_cents",
)


@dataclass(frozen=True)
class PeriodToReopen:
    """One period the job will recompute, and the terms that put it there."""

    period: str
    accounts: int
    reasons: tuple[str, ...]
    unsettled_cents: int

    def __post_init__(self) -> None:
        if not self.reasons:
            raise ValueError(
                f"{self.period} is in the reopen set with no reason, which means "
                "the selection rule and the reason list disagree"
            )


@dataclass(frozen=True)
class ReopenPlan:
    """The whole decision, including what it declined to reopen.

    The declined counts are not decoration. A plan that reports only what it
    selected cannot be told apart from a plan that selected everything, and
    the 10-26 rule about an empty comparison applies to a selection as much as
    to a loop: a rule that happens to match every row is reported the same way
    as a rule that matched the right rows.
    """

    reopen: tuple[PeriodToReopen, ...]
    periods_in_the_window: int
    periods_outside_the_window: int
    settled_inside_the_window: int

    @property
    def periods(self) -> tuple[str, ...]:
        return tuple(entry.period for entry in self.reopen)

    def as_measurement(self) -> dict:
        return {
            "declared_window_days": None,
            "periods_total": self.periods_in_the_window
            + self.periods_outside_the_window,
            "periods_in_the_window": self.periods_in_the_window,
            "periods_outside_the_window": self.periods_outside_the_window,
            "periods_reopened": len(self.reopen),
            "periods_inside_the_window_left_closed": self.settled_inside_the_window,
            "reopened": [
                {
                    "period": entry.period,
                    "accounts": entry.accounts,
                    "reasons": list(entry.reasons),
                    "unsettled_cents": entry.unsettled_cents,
                }
                for entry in self.reopen
            ],
        }


def _reasons(rows: list[dict]) -> tuple[tuple[str, ...], int]:
    """The unsettled terms present across one period's account rows.

    Summed in absolute cents per term. A term that nets to zero across two
    accounts is still two documents somebody has to deal with, and the netting
    is exactly the mistake the reconciliation keeps its terms apart to
    avoid at period level.
    """
    reasons: list[str] = []
    total = 0
    for term in UNSETTLED_TERMS:
        present = sum(abs(int(row.get(term, 0) or 0)) for row in rows)
        if present:
            reasons.append(term)
            total += present
    return tuple(reasons), total


def plan(rows: list[dict]) -> ReopenPlan:
    """Build the plan from `recon_billing_to_gl` rows.

    Takes the rows rather than a connection so the arithmetic is graded by the
    suite on a machine with no warehouse. The driver that reads the warehouse
    is `scripts/reopen_periods.py` and it holds no arithmetic. A number a script
    computes is a number no mutation pass reaches.
    """
    by_period: dict[str, list[dict]] = {}
    outside = 0
    for row in rows:
        period = row["period"]
        if not row.get("inside_the_reopen_window"):
            outside += 1
            continue
        by_period.setdefault(period, []).append(row)

    selected: list[PeriodToReopen] = []
    settled = 0
    for period in sorted(by_period):
        period_rows = by_period[period]
        reasons, unsettled = _reasons(period_rows)
        if not reasons:
            settled += 1
            continue
        selected.append(
            PeriodToReopen(
                period=period,
                accounts=len(period_rows),
                reasons=reasons,
                unsettled_cents=unsettled,
            )
        )

    return ReopenPlan(
        reopen=tuple(selected),
        periods_in_the_window=len(by_period),
        periods_outside_the_window=len({
            row["period"] for row in rows if not row.get("inside_the_reopen_window")
        }),
        settled_inside_the_window=settled,
    )


def scope_predicate(periods: tuple[str, ...] | list[str]) -> str:
    """The SQL predicate that scopes a recomputation to the planned periods.

    Returns a predicate that is false for every row when the plan is empty,
    not one that is true. A scoped rerun whose empty scope means everything is
    the 10-26 failure with the sign flipped, and it would recompute the whole
    history on the day the plan correctly found nothing to do.
    """
    if not periods:
        return "1 = 0"
    quoted = ", ".join("'" + period.replace("'", "''") + "'" for period in periods)
    return f"period in ({quoted})"
