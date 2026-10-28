"""The dollar parse, mostly on what it refuses.

This module exists because of a defect rather than a feature. Four places read a
dollar string with `int(text.replace(".", ""))`. That is correct for a value with
exactly two decimal places and silently wrong for every other shape. It is the
dangerous kind of wrong, because the generator only ever emits two places and so
nothing here would have shown it.
"""

from __future__ import annotations

from gross_margin.money import cents, extended, fmt_usd


def check_a_two_place_amount_round_trips():
    for value in (0, 1, 99, 100, 1234, 180000, 99999999):
        text = fmt_usd(value)
        if cents(text) != value:
            raise AssertionError(f"{value} formatted as {text} and parsed back as {cents(text)}")


def check_a_negative_amount_round_trips():
    """Nothing in the corpus is negative today and cost can go negative downstream.

    An overapplied credit against a line is how that happens, so the formatter and
    the parse have to agree about the sign before the margin layer exists.
    """
    for value in (-1, -50, -123456):
        if cents(fmt_usd(value)) != value:
            raise AssertionError(f"{value} did not survive a round trip through {fmt_usd(value)}")


def check_one_decimal_place_is_refused_rather_than_read_as_cents():
    """The regression. The old parse read this as 123 cents and did not complain.

    A tenth of a dollar silently becoming a dollar and twenty three cents is a
    factor of ten in one direction and it would land in a margin total.
    """
    try:
        cents("12.3")
    except ValueError as err:
        if "12.3" not in str(err):
            raise AssertionError(f"the refusal does not name the value: {err}")
        return
    raise AssertionError("'12.3' was accepted, which the old parse also did")


def check_the_other_shapes_a_column_could_hold_are_refused():
    for bad in ("12", "12.345", "", "abc", "1.2.3", " 12.00", "12.00 ", "+12.00", "1,200.00"):
        try:
            cents(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} was accepted as a dollar amount")


def check_the_smallest_value_the_parse_must_accept():
    """A refusal checked only against values it must reject is half a check."""
    if cents("0.00") != 0:
        raise AssertionError("zero was not read as zero")
    if cents("0.01") != 1:
        raise AssertionError("one cent was not read as one cent")


def check_extended_multiplies_by_a_count_and_not_by_money():
    if extended("12.34", 3) != 3702:
        raise AssertionError(f"three at 12.34 came back {extended('12.34', 3)}")
    if extended("12.34", "3") != 3702:
        raise AssertionError("a quantity arriving as text was read differently")
    try:
        extended("12.3", 3)
    except ValueError:
        return
    raise AssertionError("extended accepted a unit price the parse should refuse")
