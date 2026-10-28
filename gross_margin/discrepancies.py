"""Injects the discrepancies a reconciliation has to find.

This runs as a pass over a finished clean corpus rather than inside the draw. The
reason is that writing a defect while generating shifts every later random value,
so any figure that moved afterwards has two possible causes. Done this way a build
with nothing requested reproduces the clean corpus byte for byte, and a check
asserts it.

Every injector reports what it achieved rather than what it was asked for. A
request for more orphans than there are credits is answered with the number that
fit and the gap is visible in the manifest. Silently clamping a knob is how a
corpus ends up not holding the case a test was written for.

Ordering inside `apply` is load bearing. Mutating injectors run before appending
ones so that a position chosen by one is still the row the next one sees.
"""

from __future__ import annotations

import datetime as dt

from gross_margin.calendar import period_of
from gross_margin.generate import PRODUCT_LINES, WINDOW_END, WINDOW_START, _rng
from gross_margin.money import cents, extended, fmt_usd

# What a default build injects. Small absolute counts rather than rates, because
# the point of each one is to exist at all. A reconciliation that cannot find nine
# orphans in twenty thousand lines will not find ninety either.
DEFAULT_REQUEST = {
    "orphan_credit": 9,
    "duplicate_credit": 12,
    "cents_scaled_amount": 6,
    "overapplied_credit": 14,
    "missing_partner": 31,
    "reassigned_product_line": 4,
}


def _pick(rng, population: list, count: int) -> list:
    """Sample without replacement and return what fit rather than raising."""
    if count >= len(population):
        return list(population)
    return rng.sample(population, count)


def _orphan_credit(rng, corpus, count: int) -> int:
    """Point a credit at a line that is not in the corpus.

    The vendor sends a credit quoting a line reference from a system we do not
    have. This is the commonest real one and it is also the one a naive inner join
    makes invisible, because the row simply disappears from the margin and nothing
    says it did.

    Every third one loses its invoice reference as well. That is the harsher
    version and it exists so that the unmatched counter in the lag measurement has
    something to count. A counter whose value is zero by construction is a claim
    that something is being checked.
    """
    chosen = _pick(rng, list(range(len(corpus.credits))), count)
    for position, index in enumerate(chosen):
        corpus.credits[index]["line_id"] = f"L9{index:07d}"
        if position % 3 == 2:
            corpus.credits[index]["invoice_id"] = f"INV9{index:06d}"
    return len(chosen)


def _overapplied_credit(rng, corpus, count: int) -> int:
    """A credit worth more than the line it adjusts, which drives cost negative."""
    unit_by_line = {}
    for line in corpus.lines:
        unit_by_line[line["line_id"]] = (
            int(line["qty"]),
            line["unit_cost_usd"],
            line["unit_price_usd"],
        )
    candidates = [
        i for i, credit in enumerate(corpus.credits) if credit["line_id"] in unit_by_line
    ]
    chosen = _pick(rng, candidates, count)
    for index in chosen:
        credit = corpus.credits[index]
        qty, unit_cost, unit_price = unit_by_line[credit["line_id"]]
        unit = unit_cost if credit["applies_to"] == "cost" else unit_price
        line_total = extended(unit, qty)
        credit["amount_usd"] = fmt_usd(int(line_total * rng.uniform(1.05, 1.9)))
    return len(chosen)


def _cents_scaled_amount(rng, corpus, count: int) -> int:
    """An amount keyed in cents into a column that holds dollars.

    Two systems disagreeing about a unit is not a rounding problem. It is a factor
    of a hundred and it survives every total that does not have a bound on it.
    """
    candidates = [
        i
        for i, credit in enumerate(corpus.credits)
        if credit["credit_type"] != "return_credit"
    ]
    chosen = _pick(rng, candidates, count)
    for index in chosen:
        credit = corpus.credits[index]
        credit["amount_usd"] = fmt_usd(cents(credit["amount_usd"]) * 100)
    return len(chosen)


def _missing_partner(rng, corpus, count: int) -> int:
    """An invoice with no partner on it, which is the gap between billing and CRM."""
    chosen = _pick(rng, list(range(len(corpus.invoices))), count)
    for index in chosen:
        corpus.invoices[index]["partner_id"] = ""
    return len(chosen)


def _duplicate_credit(rng, corpus, count: int) -> int:
    """The same vendor document posted twice under two internal ids.

    Appended rather than edited, so this one runs after every mutating injector.
    The duplicate keeps the vendor document number, which is the only field that
    makes it findable.
    """
    chosen = _pick(rng, list(range(len(corpus.credits))), count)
    added = []
    for offset, index in enumerate(chosen, start=1):
        clone = dict(corpus.credits[index])
        clone["credit_id"] = f"CM9{offset:06d}"
        added.append(clone)
    corpus.credits.extend(added)
    return len(added)


def _reassigned_product_line(rng, corpus, count: int) -> int:
    """A product moved to another product line partway through the window.

    This is the one that is not a bad row. Both rows are correct and the dimension
    is now effective dated, so margin by product line depends on which row you
    join to and nothing in the data tells you that choice was made. A reader who
    joins on product_id alone gets a number that is neither snapshot.
    """
    chosen = _pick(rng, list(range(len(corpus.products))), count)
    added = []
    for index in chosen:
        product = corpus.products[index]
        others = [line for line in PRODUCT_LINES if line != product["product_line"]]
        cutover = WINDOW_START + dt.timedelta(
            days=rng.randint(120, (WINDOW_END - WINDOW_START).days - 120)
        )
        added.append(
            {
                "product_id": product["product_id"],
                "product_line": rng.choice(others),
                "vendor_id": product["vendor_id"],
                "effective_from": cutover.isoformat(),
            }
        )
    corpus.products.extend(added)
    return len(added)


MUTATORS = (
    ("orphan_credit", _orphan_credit),
    ("overapplied_credit", _overapplied_credit),
    ("cents_scaled_amount", _cents_scaled_amount),
    ("missing_partner", _missing_partner),
)
APPENDERS = (
    ("duplicate_credit", _duplicate_credit),
    ("reassigned_product_line", _reassigned_product_line),
)


def apply(corpus, request: dict[str, int] | None = None) -> dict[str, int]:
    """Inject the requested discrepancies and record what actually landed.

    Returns a mapping of name to achieved count and also writes it onto the corpus
    so that `write` publishes it. A name in the request that nothing here knows
    about raises, because a typo in a knob should not read as zero defects.
    """
    request = DEFAULT_REQUEST if request is None else request
    known = {name for name, _ in MUTATORS} | {name for name, _ in APPENDERS}
    unknown = sorted(set(request) - known)
    if unknown:
        raise ValueError(f"no injector named {unknown}, known names are {sorted(known)}")

    achieved: dict[str, int] = {}
    for name, injector in MUTATORS + APPENDERS:
        wanted = int(request.get(name, 0))
        if wanted <= 0:
            achieved[name] = 0
            continue
        rng = _rng(corpus.seed, f"defect:{name}")
        achieved[name] = injector(rng, corpus, wanted)

    # Posting periods are rederived for appended credits so a duplicate does not
    # carry a period from a row that was later edited.
    for credit in corpus.credits:
        credit["period"] = period_of(dt.date.fromisoformat(credit["posted_date"]))

    corpus.defects = {
        "requested": {name: int(request.get(name, 0)) for name, _ in MUTATORS + APPENDERS},
        "achieved": achieved,
    }
    return achieved
