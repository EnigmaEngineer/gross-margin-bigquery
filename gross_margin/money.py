"""One place that turns a dollar string into integer cents, and back.

There were three versions of this at one point. The generator split on the
decimal point and did the arithmetic inline. The injectors and two check modules
used `int(text.replace(".", ""))`, which is the same answer for a value with
exactly two decimal places and a silently wrong one for anything else. `12.3`
came back as 123 cents rather than being refused, and `12` came back as 12.

So the parse is strict. A string this function accepts has a sign, at least one
whole digit, a point, and exactly two places. Anything else raises, because a
value arriving in another shape means a column holds something nobody expected
and the right outcome is a traceback rather than a number off by a factor of ten.
"""

from __future__ import annotations

import re

AMOUNT = re.compile(r"^(-?)(\d+)\.(\d{2})$")


def cents(text: str) -> int:
    match = AMOUNT.match(text)
    if match is None:
        raise ValueError(
            f"{text!r} is not a dollar amount with two decimal places, so reading it "
            "as cents would be a guess"
        )
    sign, whole, fraction = match.groups()
    value = int(whole) * 100 + int(fraction)
    return -value if sign else value


def fmt_usd(amount_cents: int) -> str:
    sign = "-" if amount_cents < 0 else ""
    whole, fraction = divmod(abs(amount_cents), 100)
    return f"{sign}{whole}.{fraction:02d}"


def extended(unit_usd: str, qty: int | str) -> int:
    """Line total in cents. Quantity is a count and never a money value."""
    return cents(unit_usd) * int(qty)
