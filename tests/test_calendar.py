"""The business calendar, checked on both sides of every boundary it draws."""

from __future__ import annotations

import datetime as dt

from gross_margin import calendar as cal


def check_a_weekend_rolls_forward_to_monday():
    saturday = dt.date(2026, 3, 7)
    assert saturday.weekday() == 5
    if cal.posting_date(saturday) != dt.date(2026, 3, 9):
        raise AssertionError(f"saturday posted on {cal.posting_date(saturday)}")


def check_an_observed_holiday_rolls_forward():
    """Independence Day in 2026 falls on a Saturday so the office shuts on the Friday."""
    friday = dt.date(2026, 7, 3)
    if cal.is_business_day(friday):
        raise AssertionError("2026-07-03 is in HOLIDAYS and still reads as a business day")
    if cal.posting_date(friday) != dt.date(2026, 7, 6):
        raise AssertionError(f"the holiday posted on {cal.posting_date(friday)}")


def check_the_freeze_boundary_from_both_sides():
    """The largest day that is not frozen, beside the smallest one that is.

    Checking only the frozen side would pass against a rule that freezes the whole
    month. March 2026 has 22 business days and the freeze is the last two of them.
    """
    days = cal.business_days_in_period(dt.date(2026, 3, 1))
    if len(days) != 22:
        raise AssertionError(f"March 2026 has {len(days)} business days, expected 22")
    last_open = days[-cal.FREEZE_BUSINESS_DAYS - 1]
    first_frozen = days[-cal.FREEZE_BUSINESS_DAYS]
    if cal.is_frozen(last_open):
        raise AssertionError(f"{last_open} is the day before the freeze and reads as frozen")
    if not cal.is_frozen(first_frozen):
        raise AssertionError(f"{first_frozen} is the first frozen day and reads as open")
    if cal.posting_date(last_open) != last_open:
        raise AssertionError("a day outside the freeze was deferred")


def check_the_close_batch_releases_in_the_following_period():
    frozen = dt.date(2026, 3, 30)
    if not cal.is_frozen(frozen):
        raise AssertionError("the fixture day is no longer inside the freeze")
    posted = cal.posting_date(frozen)
    if posted != dt.date(2026, 4, 2):
        raise AssertionError(f"the close batch released on {posted}, expected 2026-04-02")
    if cal.period_of(posted) == cal.period_of(frozen):
        raise AssertionError("a frozen credit posted inside its own period")


def check_a_non_business_day_is_never_frozen():
    """A Sunday at the end of a month is held back by the simpler rule.

    Without this, `is_frozen` could answer yes for a day nothing posts on anyway
    and `posting_date` would defer it a month instead of to the Monday.
    """
    sunday = dt.date(2026, 3, 29)
    assert sunday.weekday() == 6
    if cal.is_frozen(sunday):
        raise AssertionError("a Sunday read as frozen")


def check_period_end_handles_december():
    if cal.period_end(dt.date(2025, 12, 9)) != dt.date(2025, 12, 31):
        raise AssertionError("December rolled into the wrong year")
    if cal.period_end(dt.date(2024, 2, 1)) != dt.date(2024, 2, 29):
        raise AssertionError("February 2024 is a leap February and came back short")
