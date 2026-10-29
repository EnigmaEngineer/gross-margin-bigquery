"""The ledger side of the reconciliation, graded without a warehouse.

Everything here runs off the in-memory corpus, so it grades on a clone that has
never held the generated csv files. The reconciliation arithmetic itself is
graded by the dbt tests and by scripts/recon_fixture.py, because it is SQL and
pretending to reimplement it here would be grading a second implementation.
"""

from __future__ import annotations

import datetime as dt

from gross_margin import calendar as cal
from gross_margin import ledger
from gross_margin.generate import build
from gross_margin.money import cents, extended
from gross_margin.registry import ARTIFACTS
from tests.corpus_fixture import corpus


def _own_corpus():
    """A corpus of this module's own, for the checks that have to mutate one."""
    built = build()
    ledger.post(built)
    return built


def check_an_unbroken_ledger_agrees_with_billing_on_every_document():
    """The property the whole reconciliation rests on.

    If a clean posting disagreed anywhere, every figure the reconciliation
    publishes would be measuring this module rather than the injected breaks.
    """
    built = _own_corpus()
    revenue: dict[str, int] = {}
    cost: dict[str, int] = {}
    for line in built.lines:
        invoice = line["invoice_id"]
        revenue[invoice] = revenue.get(invoice, 0) + extended(
            line["unit_price_usd"], line["qty"]
        )
        cost[invoice] = cost.get(invoice, 0) + extended(
            line["unit_cost_usd"], line["qty"]
        )
    want = {}
    for invoice, amount in revenue.items():
        want[("invoice", invoice, ledger.ACCOUNT_REVENUE)] = amount
    for invoice, amount in cost.items():
        want[("invoice", invoice, ledger.ACCOUNT_COGS)] = amount
    for credit in built.credits:
        key = (
            "credit",
            credit["credit_id"],
            ledger._credit_account(credit["applies_to"]),
        )
        want[key] = cents(credit["amount_usd"])

    got = {
        (entry["source_type"], entry["source_id"], entry["account"]): cents(
            entry["amount_usd"]
        )
        for entry in built.gl_entries
    }
    if got != want:
        missing = sorted(set(want) - set(got))[:3]
        extra = sorted(set(got) - set(want))[:3]
        moved = sorted(k for k in set(got) & set(want) if got[k] != want[k])[:3]
        raise AssertionError(
            f"a clean posting disagrees with billing. missing {missing}, "
            f"extra {extra}, different amounts {moved}"
        )


def check_the_grain_is_one_entry_per_document_per_account():
    """fct_gl_posting joins on these three columns, so a repeat fans the join out
    and double counts money on both sides of the variance."""
    built = corpus(True)
    seen = set()
    for entry in built.gl_entries:
        key = (entry["source_type"], entry["source_id"], entry["account"])
        if key in seen:
            raise AssertionError(f"{key} appears twice in the ledger")
        seen.add(key)


def check_an_invoice_posts_on_the_date_accounts_payable_would_use():
    built = _own_corpus()
    dates = {row["invoice_id"]: row["invoice_date"] for row in built.invoices}
    for entry in built.gl_entries:
        if entry["source_type"] != "invoice":
            continue
        billed = dt.date.fromisoformat(dates[entry["source_id"]])
        want = cal.posting_date(billed)
        if entry["posted_date"] != want.isoformat():
            raise AssertionError(
                f"{entry['source_id']} was billed {billed} and the ledger posted it "
                f"{entry['posted_date']}, the calendar says {want}"
            )


def check_the_close_freeze_actually_moves_invoices_into_the_next_period():
    """The ordinary timing difference, and it has to exist or the reconciliation's
    main term is untested by the generated corpus.

    A count rather than a share. The point is that the case is present at all.
    """
    built = _own_corpus()
    periods = {row["invoice_id"]: row["period"] for row in built.invoices}
    moved = sum(
        1
        for entry in built.gl_entries
        if entry["source_type"] == "invoice"
        and entry["period"] != periods[entry["source_id"]]
    )
    if moved == 0:
        raise AssertionError(
            "no invoice posts to the ledger in a later period than it was billed "
            "in, so nothing in this corpus exercises the period shift term"
        )


def check_every_ledger_amount_is_two_decimal_places():
    for entry in corpus(True).gl_entries:
        # money.cents refuses anything else, so this is the parse being the test.
        cents(entry["amount_usd"])


def check_every_entry_uses_an_account_the_module_declares():
    for entry in corpus(True).gl_entries:
        if entry["account"] not in ledger.ACCOUNTS:
            raise AssertionError(
                f"{entry['je_id']} posts to {entry['account']}, which is not one of "
                f"{list(ledger.ACCOUNTS)}"
            )


def check_the_period_label_matches_the_posted_date_on_every_entry():
    """The generator never writes a disagreeing pair, so stg_gl_entries' own
    comparison is reasoning about a case the corpus does not hold. Saying so here
    is cheaper than discovering the flag has never been true."""
    for entry in corpus(True).gl_entries:
        posted = dt.date.fromisoformat(entry["posted_date"])
        if entry["period"] != cal.period_of(posted):
            raise AssertionError(
                f"{entry['je_id']} is labelled {entry['period']} and posted "
                f"{entry['posted_date']}"
            )


def check_the_header_the_generator_writes_matches_the_registry():
    entry = corpus(True).gl_entries[0]
    if tuple(entry) != ARTIFACTS["gl_entries.csv"]:
        raise AssertionError(
            f"a ledger entry carries {tuple(entry)}, the registry says "
            f"{ARTIFACTS['gl_entries.csv']}"
        )


def check_the_injected_counts_are_what_was_requested():
    """Each injector reports what it achieved. A request that silently did not fit
    is the thing this compares, and `amount_keyed_wrong` is the one that can
    legitimately come back short, because transposing two equal digits changes
    nothing and is not counted."""
    built = corpus(True)
    achieved = built.gl_defects["achieved"]
    requested = built.gl_defects["requested"]
    for name in ("unposted_document", "orphan_entry", "posted_to_a_later_period"):
        if achieved[name] != requested[name]:
            raise AssertionError(
                f"{name} asked for {requested[name]} and landed {achieved[name]}"
            )
    if not 0 < achieved["amount_keyed_wrong"] <= requested["amount_keyed_wrong"]:
        raise AssertionError(
            f"amount_keyed_wrong landed {achieved['amount_keyed_wrong']} against a "
            f"request of {requested['amount_keyed_wrong']}"
        )


def check_breaking_the_ledger_removes_exactly_the_unposted_documents():
    clean = _own_corpus()
    before = len(clean.gl_entries)
    broken = build()
    ledger.post(broken)
    achieved = ledger.inject(broken)
    want = before - achieved["unposted_document"] + achieved["orphan_entry"]
    if len(broken.gl_entries) != want:
        raise AssertionError(
            f"the ledger went from {before} to {len(broken.gl_entries)} entries, "
            f"and the injected counts say it should be {want}"
        )


def check_a_clean_posting_reproduces_exactly():
    first = _own_corpus().gl_entries
    second = _own_corpus().gl_entries
    if first != second:
        raise AssertionError("two clean postings of one corpus disagree")


def check_injecting_refuses_a_name_it_does_not_know():
    built = _own_corpus()
    try:
        ledger.inject(built, {"no_such_break": 1})
    except ValueError:
        return
    raise AssertionError("a typo in an injector name has to raise, not read as zero")


def check_injecting_refuses_a_corpus_with_no_ledger():
    built = build()
    try:
        ledger.inject(built)
    except ValueError:
        return
    raise AssertionError(
        "breaking a ledger that was never posted has to raise rather than report "
        "that it broke nothing"
    )


def check_a_credit_that_applies_to_neither_cost_nor_revenue_raises():
    try:
        ledger._credit_account("goodwill")
    except ValueError:
        return
    raise AssertionError(
        "an unknown applies_to has to raise. Silently filing it under one of the "
        "two accounts would put real money in the wrong place"
    )
