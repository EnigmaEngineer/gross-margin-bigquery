"""Measures the credit lag and answers whether one lookback window is enough.

Reconciliation compares billing against the ledger for a closed period. A credit
that posts after the comparison ran makes the comparison wrong, so the job has to
reopen a window of past days. How wide that window is a measurement and not a
setting, and this module is where it gets made.

The lag that matters is the one a consumer can see. That is `posted_date` minus
the invoice date. It is not the delay the generator drew, which nothing writes
down, and it is not `raised_date` minus the invoice date, which is the vendor's
view and arrives before the ledger does. The gap between those last two is the
business calendar, and keeping the two terms apart is the only way to say which
half of a long lag anybody could shorten.

Every figure here is a count or a whole number of days. Nothing in this module
measures elapsed time, because a published figure has to reproduce on another
machine and a millisecond does not.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class LagRecord:
    credit_type: str
    raise_lag_days: int
    calendar_lag_days: int
    observable_lag_days: int


def _nearest_rank(n: int, q: float) -> int:
    """Rank for the nearest rank quantile, one indexed and clamped into range.

    Rounded to nine places before the ceiling. Without that, `0.95 * 20` can land
    a hair above 19 in binary floating point and the ceiling returns 20, which
    moves a published window by a day for no reason anybody could find.
    """
    rank = math.ceil(round(q * n, 9))
    return min(max(rank, 1), n)


def quantile(values: list[int], q: float) -> int:
    """Nearest rank on values sorted ascending.

    One definition, used everywhere in this repo. A quantile has more than one
    correct definition and an interpolating one would put a fractional day on a
    window that can only be a whole number of days. The caller sorts.
    """
    if not values:
        raise ValueError("no values to take a quantile of")
    if not 0.0 < q <= 1.0:
        raise ValueError(f"q must be in (0, 1], got {q}")
    return values[_nearest_rank(len(values), q) - 1]


def lag_records(invoices: list[dict], credits: list[dict]) -> tuple[list[LagRecord], int]:
    """Build one record per credit whose invoice is present.

    The second return value counts credits whose invoice is absent. It is returned
    rather than logged because a loop over a set it discovered has to say how big
    that set was. A caller that ignores it is reporting on a subset without saying
    which.
    """
    invoice_dates = {
        row["invoice_id"]: dt.date.fromisoformat(row["invoice_date"]) for row in invoices
    }
    records: list[LagRecord] = []
    unmatched = 0
    for credit in credits:
        invoice_date = invoice_dates.get(credit["invoice_id"])
        if invoice_date is None:
            unmatched += 1
            continue
        raised = dt.date.fromisoformat(credit["raised_date"])
        posted = dt.date.fromisoformat(credit["posted_date"])
        records.append(
            LagRecord(
                credit_type=credit["credit_type"],
                raise_lag_days=(raised - invoice_date).days,
                calendar_lag_days=(posted - raised).days,
                observable_lag_days=(posted - invoice_date).days,
            )
        )
    return records, unmatched


def summarise(records: list[LagRecord]) -> dict:
    """Quantiles of the observable lag, and the calendar term beside it."""
    if not records:
        raise ValueError("cannot summarise an empty record set")
    observable = sorted(r.observable_lag_days for r in records)
    raise_only = sorted(r.raise_lag_days for r in records)
    calendar_only = sorted(r.calendar_lag_days for r in records)
    held = [r for r in records if r.calendar_lag_days >= 3]
    return {
        "credits": len(records),
        "observable_lag_days": {
            "p50": quantile(observable, 0.50),
            "p90": quantile(observable, 0.90),
            "p95": quantile(observable, 0.95),
            "p99": quantile(observable, 0.99),
            "max": observable[-1],
        },
        "raise_lag_days": {
            "p50": quantile(raise_only, 0.50),
            "p95": quantile(raise_only, 0.95),
        },
        "calendar_lag_days": {
            "p50": quantile(calendar_only, 0.50),
            "p95": quantile(calendar_only, 0.95),
            "max": calendar_only[-1],
        },
        # A credit held three days or more past its vendor date did not just land on
        # a weekend. It waited for the close batch.
        "credits_held_past_a_weekend": len(held),
    }


def window_for_coverage(records: list[LagRecord], coverage: float) -> int:
    """The smallest whole number of days covering at least `coverage` of credits.

    This is the lookback window. Derived by counting rather than by rounding a
    quantile, so the returned number is checkable against the records directly.
    """
    if not records:
        raise ValueError("cannot size a window from an empty record set")
    if not 0.0 < coverage <= 1.0:
        raise ValueError(f"coverage must be in (0, 1], got {coverage}")
    observable = sorted(r.observable_lag_days for r in records)
    return observable[_nearest_rank(len(observable), coverage) - 1]


def coverage_at(records: list[LagRecord], window_days: int) -> dict:
    """What share of these credits land inside a window of that width."""
    inside = sum(1 for r in records if r.observable_lag_days <= window_days)
    return {
        "credits": len(records),
        "inside_window": inside,
        "coverage_pct": round(100.0 * inside / len(records), 2),
    }


def keyed_against_pooled(records: list[LagRecord], coverage: float = 0.95) -> dict:
    """Does the window need to be per credit type, or will one serve.

    Measured in both directions, because the first version of this function only
    measured one and got the sign wrong. It subtracted each type's window from the
    pooled window and summed the result as a saving. For the slowest type that
    difference is negative, so the total was a sum of mixed signs reported as a
    single positive saving.

    The two directions are different failures and they are both real here.

    Too long. A type whose own window is shorter than the pooled one waits for a
    window it never needed, and the wait is reported in credit days.

    Too short. A pooled window set from a mixed population is pulled toward
    whichever type is most numerous, so the slowest type gets less coverage than
    the target advertises. That one is the dangerous direction, because the number
    on the tin still says the target.
    """
    pooled = window_for_coverage(records, coverage)
    by_type: dict[str, list[LagRecord]] = {}
    for record in records:
        by_type.setdefault(record.credit_type, []).append(record)

    keyed = {name: window_for_coverage(rows, coverage) for name, rows in by_type.items()}
    shortest = min(keyed.values())
    target_pct = round(coverage * 100, 2)

    at_pooled = {name: coverage_at(rows, pooled) for name, rows in by_type.items()}
    under_covered = sorted(
        name for name, got in at_pooled.items() if got["coverage_pct"] < target_pct
    )
    avoidable_wait_credit_days = sum(
        (pooled - keyed[name]) * len(rows)
        for name, rows in by_type.items()
        if keyed[name] < pooled
    )

    return {
        "coverage_target_pct": target_pct,
        "pooled_window_days": pooled,
        "keyed_window_days": dict(sorted(keyed.items())),
        "shortest_keyed_window_days": shortest,
        "coverage_at_the_pooled_window": dict(sorted(at_pooled.items())),
        "types_under_covered_by_the_pooled_window": under_covered,
        "avoidable_wait_credit_days": avoidable_wait_credit_days,
        "coverage_if_the_shortest_window_were_applied_to_all": dict(
            sorted(
                (name, coverage_at(rows, shortest)) for name, rows in by_type.items()
            )
        ),
    }
