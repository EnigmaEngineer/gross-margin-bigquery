"""The lag measurement, including a regression on a defect in this module.

The first version of `keyed_against_pooled` summed the difference between the
pooled window and each type's own window and published the total as a saving. For
a type slower than the pooled window that difference is negative, so the total was
a sum of mixed signs presented as one positive number. The two fixtures below are
built so that the broken arithmetic and the fixed arithmetic give different
answers, and the second of them also has a genuine saving in it so that a version
returning a flat zero would fail too.
"""

from __future__ import annotations

from gross_margin import lag
from gross_margin.lag import LagRecord
from tests.corpus_fixture import records


def _records(pairs: list[tuple[str, int]]) -> list[LagRecord]:
    """Build records from a type and an observable lag. The two other terms are
    not read by any function under test here, so they are set to zero rather than
    to a plausible value nobody checks."""
    return [LagRecord(name, 0, 0, days) for name, days in pairs]


def check_the_quantile_definition_survives_ties_and_integers():
    """Repeated values are the degenerate case for any rank based statistic."""
    values = [1, 1, 1, 1, 2, 4, 6]
    if lag.quantile(values, 0.50) != 1:
        raise AssertionError(f"p50 of {values} came back {lag.quantile(values, 0.50)}")
    if lag.quantile(values, 0.90) != 6:
        raise AssertionError(f"p90 of {values} came back {lag.quantile(values, 0.90)}")
    if lag.quantile(values, 1.0) != 6:
        raise AssertionError("p100 is not the largest value")


def check_the_rank_boundary_from_both_sides():
    """Twenty values, so the ninety fifth centile sits exactly on rank nineteen.

    A float that lands a hair above nineteen moves the answer to rank twenty, which
    is a published window off by a day. The value just above the boundary is here
    as well, because a check on one side of a boundary does not pin it.
    """
    values = list(range(1, 21))
    if lag.quantile(values, 0.95) != 19:
        raise AssertionError(f"q=0.95 on twenty values gave {lag.quantile(values, 0.95)}")
    if lag.quantile(values, 0.951) != 20:
        raise AssertionError(f"q=0.951 gave {lag.quantile(values, 0.951)}, expected 20")


def check_the_quantile_refuses_a_q_outside_its_range():
    for bad in (0.0, -0.1, 1.5):
        try:
            lag.quantile([1, 2, 3], bad)
        except ValueError as err:
            if str(bad) not in str(err):
                raise AssertionError(f"the refusal for q={bad} does not name it: {err}")
            continue
        raise AssertionError(f"q={bad} was accepted")


def check_an_empty_record_set_is_refused_rather_than_summarised():
    for call in (lambda: lag.summarise([]), lambda: lag.window_for_coverage([], 0.95)):
        try:
            call()
        except ValueError:
            continue
        raise AssertionError("an empty record set produced an answer")


def check_the_window_and_the_quantile_agree_on_one_population():
    """Two routes to the same number, so one of them being wrong is visible.

    `window_for_coverage` counts and `quantile` ranks. They are different code and
    they have to land on the same day.
    """
    found, _ = records()
    observable = sorted(r.observable_lag_days for r in found)
    for coverage in (0.5, 0.9, 0.95, 0.99):
        counted = lag.window_for_coverage(found, coverage)
        ranked = lag.quantile(observable, coverage)
        if counted != ranked:
            raise AssertionError(
                f"at {coverage} the counted window is {counted} and the quantile is {ranked}"
            )


def check_a_window_reaches_the_coverage_it_claims():
    found, _ = records()
    for coverage in (0.5, 0.9, 0.95):
        window = lag.window_for_coverage(found, coverage)
        got = lag.coverage_at(found, window)
        if got["coverage_pct"] < coverage * 100:
            raise AssertionError(
                f"a {window} day window claims {coverage * 100} percent and reaches "
                f"{got['coverage_pct']}"
            )


def check_a_type_slower_than_the_pooled_window_is_named_and_never_netted_off():
    """The regression. Ninety five fast credits drag the pooled window down to ten.

    The five slow ones then need a hundred and four days and get zero coverage. The
    broken version returned minus four hundred and seventy for the saving. The
    fixed version returns zero and names the type that is under covered.
    """
    found = _records([("fast", 10)] * 95 + [("slow", 100 + i) for i in range(5)])
    out = lag.keyed_against_pooled(found, 0.95)
    if out["pooled_window_days"] != 10:
        raise AssertionError(f"the fixture's pooled window is {out['pooled_window_days']}")
    if out["keyed_window_days"]["slow"] != 104:
        raise AssertionError(f"slow keyed window is {out['keyed_window_days']['slow']}")
    if out["types_under_covered_by_the_pooled_window"] != ["slow"]:
        raise AssertionError(
            f"under covered came back {out['types_under_covered_by_the_pooled_window']}"
        )
    if out["avoidable_wait_credit_days"] != 0:
        raise AssertionError(
            f"avoidable wait is {out['avoidable_wait_credit_days']} and nothing waits "
            "unnecessarily in this fixture"
        )
    if out["coverage_at_the_pooled_window"]["slow"]["coverage_pct"] != 0.0:
        raise AssertionError("the slow type reads as covered by a ten day window")


def check_a_genuine_saving_is_counted_in_full():
    """The other side of the same function, so a flat zero cannot pass both.

    Fifty credits at ten days and fifty spread from forty one to ninety. The pooled
    window is eighty five and the fast half needs ten, so seventy five days of wait
    times fifty credits is three thousand seven hundred and fifty credit days.
    """
    found = _records([("fast", 10)] * 50 + [("slow", 40 + i) for i in range(1, 51)])
    out = lag.keyed_against_pooled(found, 0.95)
    if out["pooled_window_days"] != 85:
        raise AssertionError(f"the fixture's pooled window is {out['pooled_window_days']}")
    if out["keyed_window_days"]["fast"] != 10:
        raise AssertionError(f"fast keyed window is {out['keyed_window_days']['fast']}")
    if out["avoidable_wait_credit_days"] != 3750:
        raise AssertionError(
            f"avoidable wait is {out['avoidable_wait_credit_days']}, expected 3750"
        )
    if out["types_under_covered_by_the_pooled_window"] != ["slow"]:
        raise AssertionError("the slow type is inside the pooled window and should not be")


def check_the_unmatched_counter_is_not_zero_by_construction():
    """A counter that can only ever read zero is a claim that nothing is wrong."""
    _, unmatched = records()
    if unmatched <= 0:
        raise AssertionError(
            "no credit in the corpus is missing its invoice, so the unmatched count "
            "in the published measurement is a constant"
        )


def check_the_corpus_needs_a_keyed_window():
    """The finding, asserted rather than written down once in a document.

    A pooled window at the target coverage leaves the rebate credits short. If a
    change to the generator ever makes that stop being true, the README sentence
    about it is wrong and this check is what says so.
    """
    found, _ = records()
    out = lag.keyed_against_pooled(found, 0.95)
    if "volume_rebate" not in out["types_under_covered_by_the_pooled_window"]:
        raise AssertionError(
            "the pooled window now covers volume rebates, so the argument for keying "
            "the window by credit type no longer rests on this corpus"
        )
    if out["keyed_window_days"]["volume_rebate"] <= out["pooled_window_days"]:
        raise AssertionError("the rebate window is no longer wider than the pooled one")


def check_the_calendar_term_is_visible_in_the_summary():
    found, _ = records()
    summary = lag.summarise(found)
    if summary["credits_held_past_a_weekend"] <= 0:
        raise AssertionError(
            "no credit was held three days or more past its vendor date, so the close "
            "freeze is not reaching the data"
        )
    if summary["observable_lag_days"]["p50"] < summary["raise_lag_days"]["p50"]:
        raise AssertionError("the observable lag is shorter than the raise lag at the median")
