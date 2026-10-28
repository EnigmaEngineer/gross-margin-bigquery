"""Business calendar for the billing close.

The only reason this module exists is that a credit memo does not post on the day
somebody decides to raise it. It posts when accounts payable runs, and accounts
payable does not run at weekends and does not run during the close freeze. That
turns a smooth intent into a lumpy observation, and the lumps are the whole
difficulty of the lookback window this project has to set.

Holidays are the US federal set for the generated window. They are hardcoded
because the corpus window is fixed and a holiday library would be a dependency
earning nothing.
"""

from __future__ import annotations

import datetime as dt

# Federal holidays observed 2024 through 2026, on the dates the office is shut
# rather than on the statutory date. A Saturday holiday is observed on the Friday.
HOLIDAYS = frozenset(
    dt.date(*parts)
    for parts in [
        (2024, 1, 1), (2024, 1, 15), (2024, 2, 19), (2024, 5, 27), (2024, 6, 19),
        (2024, 7, 4), (2024, 9, 2), (2024, 11, 28), (2024, 11, 29), (2024, 12, 25),
        (2025, 1, 1), (2025, 1, 20), (2025, 2, 17), (2025, 5, 26), (2025, 6, 19),
        (2025, 7, 4), (2025, 9, 1), (2025, 11, 27), (2025, 11, 28), (2025, 12, 25),
        (2026, 1, 1), (2026, 1, 19), (2026, 2, 16), (2026, 5, 25), (2026, 6, 19),
        (2026, 7, 3), (2026, 9, 7), (2026, 11, 26), (2026, 11, 27), (2026, 12, 25),
    ]
)

# How many business days at the end of a period are frozen for the close. Two is
# the number finance teams I have worked with actually used. It is a design choice
# and not a measurement, so it is a module constant a reader can argue with.
FREEZE_BUSINESS_DAYS = 2

# Which business day of the following period the close batch releases on.
CLOSE_BATCH_BUSINESS_DAY = 2


def is_business_day(day: dt.date) -> bool:
    return day.weekday() < 5 and day not in HOLIDAYS


def next_business_day(day: dt.date) -> dt.date:
    """The first business day on or after `day`."""
    while not is_business_day(day):
        day += dt.timedelta(days=1)
    return day


def period_of(day: dt.date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def period_end(day: dt.date) -> dt.date:
    if day.month == 12:
        return dt.date(day.year, 12, 31)
    return dt.date(day.year, day.month + 1, 1) - dt.timedelta(days=1)


def business_days_in_period(day: dt.date) -> list[dt.date]:
    """Every business day in the calendar month containing `day`, in order."""
    first = day.replace(day=1)
    last = period_end(day)
    out = []
    cursor = first
    while cursor <= last:
        if is_business_day(cursor):
            out.append(cursor)
        cursor += dt.timedelta(days=1)
    return out


def is_frozen(day: dt.date) -> bool:
    """True when `day` falls in the close freeze at the end of its own period.

    A day that is not a business day is never frozen, because nothing posts on it
    for the simpler reason. Callers resolve to a business day first.
    """
    if not is_business_day(day):
        return False
    return day in set(business_days_in_period(day)[-FREEZE_BUSINESS_DAYS:])


def release_date(frozen_day: dt.date) -> dt.date:
    """When the close batch releases work that arrived during the freeze."""
    nxt = period_end(frozen_day) + dt.timedelta(days=1)
    days = business_days_in_period(nxt)
    return days[CLOSE_BATCH_BUSINESS_DAY - 1]


def posting_date(raw: dt.date) -> dt.date:
    """Resolve an intended date to the date accounts payable actually posts it.

    Two effects and they compose. A non business day rolls forward. A day inside
    the close freeze waits for the batch in the following period. The second one
    is what makes the observable lag distribution multimodal, and a lookback
    window fitted without it is short by most of a week for part of the month.
    """
    settled = next_business_day(raw)
    if is_frozen(settled):
        return release_date(settled)
    return settled
