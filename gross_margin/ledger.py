"""Posts the billing corpus to a general ledger, then breaks it in four ways.

A reconciliation needs two records of the same money that were written by
different systems. Billing is already here. This is the other side.

The honest description of what this module is, and it belongs at the top rather
than in a footnote. The ledger is POSTED FROM billing and then perturbed. It is
not an independent record of the same underlying trade. So the reconciliation
downstream recovers defects this file injected rather than discovering defects
that arose on their own, and the number it reports is a property of the request
in `DEFAULT_REQUEST`. What is real is the arithmetic, the period attribution and
the decomposition. What is synthetic is the population of breaks.

One difference is not injected at all, and it is the one worth having. An invoice
billed in the close freeze at the end of a period posts to the ledger in the
following period, because `calendar.posting_date` says so and the ledger takes
the date accounts payable actually used. That is ordinary timing variance, it
arrives from the business calendar rather than from a knob, and it is the bulk of
what a period level comparison shows.

Money is integer cents in here. The csv carries dollars formatted from the
integer, the same as every other table.
"""

from __future__ import annotations

import datetime as dt

from gross_margin import calendar as cal
from gross_margin.generate import _rng
from gross_margin.money import cents, extended, fmt_usd

# Four accounts, which is the smallest set that lets revenue and cost each carry
# their own credits. A real distributor's chart has hundreds and the extra ones
# would be rows with nothing to reconcile against.
ACCOUNT_REVENUE = "4000-revenue"
ACCOUNT_COGS = "5000-cost-of-goods"
ACCOUNT_CUSTOMER_CREDITS = "4100-customer-credits"
ACCOUNT_VENDOR_CREDITS = "5100-vendor-credits"

ACCOUNTS = (
    ACCOUNT_REVENUE,
    ACCOUNT_CUSTOMER_CREDITS,
    ACCOUNT_COGS,
    ACCOUNT_VENDOR_CREDITS,
)

# Small absolute counts, for the reason `discrepancies.DEFAULT_REQUEST` gives.
# The point of each break is to exist at all, so a reconciliation that cannot
# find seven unposted invoices in seven thousand will not find seventy either.
DEFAULT_REQUEST = {
    "unposted_document": 7,
    "amount_keyed_wrong": 5,
    "orphan_entry": 4,
    "posted_to_a_later_period": 11,
}


def _credit_account(applies_to: str) -> str:
    if applies_to == "cost":
        return ACCOUNT_VENDOR_CREDITS
    if applies_to == "revenue":
        return ACCOUNT_CUSTOMER_CREDITS
    raise ValueError(
        f"a credit applies to cost or to revenue, not to {applies_to!r}. A row in "
        "another shape means the generator wrote something nobody has an account for"
    )


def post(corpus) -> list[dict]:
    """Build the ledger from the corpus as it currently stands.

    Called after the billing discrepancy pass, because a credit whose amount was
    rescaled has to reach the ledger at the amount billing holds. A ledger built
    before that pass would disagree with billing everywhere and the
    reconciliation would be measuring the order the two passes ran in.
    """
    revenue_cents: dict[str, int] = {}
    cost_cents: dict[str, int] = {}
    for line in corpus.lines:
        invoice_id = line["invoice_id"]
        qty = line["qty"]
        revenue_cents[invoice_id] = revenue_cents.get(invoice_id, 0) + extended(
            line["unit_price_usd"], qty
        )
        cost_cents[invoice_id] = cost_cents.get(invoice_id, 0) + extended(
            line["unit_cost_usd"], qty
        )

    entries: list[dict] = []
    je_no = 0

    for invoice in corpus.invoices:
        invoice_id = invoice["invoice_id"]
        if invoice_id not in revenue_cents:
            # An invoice with no lines has nothing to post. It does not happen in
            # this corpus and skipping it silently would be how it starts to.
            continue
        billed = dt.date.fromisoformat(invoice["invoice_date"])
        posted = cal.posting_date(billed)
        for account, amount in (
            (ACCOUNT_REVENUE, revenue_cents[invoice_id]),
            (ACCOUNT_COGS, cost_cents[invoice_id]),
        ):
            je_no += 1
            entries.append(
                {
                    "je_id": f"JE{je_no:08d}",
                    "period": cal.period_of(posted),
                    "account": account,
                    "source_type": "invoice",
                    "source_id": invoice_id,
                    "amount_usd": fmt_usd(amount),
                    "posted_date": posted.isoformat(),
                }
            )

    for credit in corpus.credits:
        posted = dt.date.fromisoformat(credit["posted_date"])
        je_no += 1
        entries.append(
            {
                "je_id": f"JE{je_no:08d}",
                "period": cal.period_of(posted),
                "account": _credit_account(credit["applies_to"]),
                "source_type": "credit",
                "source_id": credit["credit_id"],
                "amount_usd": credit["amount_usd"],
                "posted_date": posted.isoformat(),
            }
        )

    corpus.gl_entries = entries
    return entries


def _pick(rng, population: list, count: int) -> list:
    """Sample without replacement, returning what fit. Same contract as the
    billing injectors, so a request larger than the population is visible in the
    manifest instead of being clamped out of sight."""
    if count >= len(population):
        return list(population)
    return rng.sample(population, count)


def _unposted_document(rng, corpus, count: int) -> int:
    """A billing document the ledger never received.

    The commonest real break and the one a period total hides, because a missing
    entry looks exactly like a smaller month until somebody compares the two
    sides document by document.
    """
    chosen = set(_pick(rng, list(range(len(corpus.gl_entries))), count))
    corpus.gl_entries = [
        entry for index, entry in enumerate(corpus.gl_entries) if index not in chosen
    ]
    return len(chosen)


def _amount_keyed_wrong(rng, corpus, count: int) -> int:
    """A ledger entry carrying a different amount from the document behind it.

    Transposed digits rather than a scale error. The billing side already carries
    a factor of a hundred defect and a second one would be the same test twice.
    """
    chosen = _pick(rng, list(range(len(corpus.gl_entries))), count)
    changed = 0
    for index in chosen:
        entry = corpus.gl_entries[index]
        before = abs(cents(entry["amount_usd"]))
        digits = list(str(before))
        if len(digits) < 2:
            continue
        at = rng.randrange(len(digits) - 1)
        digits[at], digits[at + 1] = digits[at + 1], digits[at]
        after = int("".join(digits))
        if after == before:
            # Transposing two equal digits changes nothing. Counting it would be
            # an injector reporting a defect it did not make, which is the thing
            # the achieved count exists to prevent.
            continue
        entry["amount_usd"] = fmt_usd(after)
        changed += 1
    return changed


def _orphan_entry(rng, corpus, count: int) -> int:
    """A ledger entry whose source document does not exist in billing.

    Appended, so it runs after every mutating injector and cannot be chosen by
    one. A journal posted by hand during a close looks like this.
    """
    je_no = len(corpus.gl_entries)
    added = []
    for offset in range(1, count + 1):
        template = corpus.gl_entries[rng.randrange(len(corpus.gl_entries))]
        je_no += 1
        added.append(
            {
                "je_id": f"JE9{je_no:07d}",
                "period": template["period"],
                "account": template["account"],
                "source_type": template["source_type"],
                "source_id": f"ORPHAN{offset:05d}",
                "amount_usd": template["amount_usd"],
                "posted_date": template["posted_date"],
            }
        )
    corpus.gl_entries.extend(added)
    return len(added)


def _posted_to_a_later_period(rng, corpus, count: int) -> int:
    """An entry posted a month later than the calendar would have put it.

    This is the one that is not a bad row in the way the others are. The money is
    right and the period is wrong, so it clears by itself next month and a
    reconciliation that treats it as a loss raises a break that resolves with no
    action. Telling it apart from a document that never arrived is the whole
    reason the decomposition downstream exists.
    """
    chosen = _pick(rng, list(range(len(corpus.gl_entries))), count)
    for index in chosen:
        entry = corpus.gl_entries[index]
        was = dt.date.fromisoformat(entry["posted_date"])
        shifted = cal.period_end(was) + dt.timedelta(days=1)
        settled = cal.next_business_day(shifted)
        entry["posted_date"] = settled.isoformat()
        entry["period"] = cal.period_of(settled)
    return len(chosen)


MUTATORS = (
    ("amount_keyed_wrong", _amount_keyed_wrong),
    ("posted_to_a_later_period", _posted_to_a_later_period),
    ("unposted_document", _unposted_document),
)
APPENDERS = (("orphan_entry", _orphan_entry),)


def inject(corpus, request: dict[str, int] | None = None) -> dict[str, int]:
    """Break the ledger in the requested ways and record what landed.

    `unposted_document` runs last of the mutators because it reindexes the list,
    and an injector that picked a position before the removal would be editing a
    different row than the one it chose.
    """
    request = DEFAULT_REQUEST if request is None else request
    known = {name for name, _ in MUTATORS} | {name for name, _ in APPENDERS}
    unknown = sorted(set(request) - known)
    if unknown:
        raise ValueError(
            f"no ledger injector named {unknown}, known names are {sorted(known)}"
        )
    if not corpus.gl_entries:
        raise ValueError(
            "there is no ledger to break. Call ledger.post(corpus) first, after the "
            "billing discrepancy pass"
        )

    achieved: dict[str, int] = {}
    for name, injector in MUTATORS + APPENDERS:
        wanted = int(request.get(name, 0))
        if wanted <= 0:
            achieved[name] = 0
            continue
        rng = _rng(corpus.seed, f"gl:{name}")
        achieved[name] = injector(rng, corpus, wanted)

    corpus.gl_defects = {
        "requested": {name: int(request.get(name, 0)) for name, _ in MUTATORS + APPENDERS},
        "achieved": achieved,
    }
    return achieved
