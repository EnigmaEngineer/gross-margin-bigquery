"""Each injector, checked by what it left behind rather than by what it returned.

Every check here asks one injector for a specific count and then counts the
evidence in the rows. A check that only read the return value would pass against
an injector that counted correctly and wrote nothing.
"""

from __future__ import annotations

from gross_margin import discrepancies
from gross_margin.generate import PRODUCT_LINES, build
from gross_margin.money import cents, extended
from tests.corpus_fixture import corpus


def _only(name: str, count: int):
    built = build()
    achieved = discrepancies.apply(built, {name: count})
    return built, achieved[name]


def check_the_default_request_lands_in_full():
    built = corpus(True)
    requested = built.defects["requested"]
    achieved = built.defects["achieved"]
    short = {
        name: (requested[name], achieved[name])
        for name in requested
        if achieved[name] != requested[name]
    }
    if short:
        raise AssertionError(f"the default request did not land in full: {short}")
    if sum(achieved.values()) == 0:
        raise AssertionError("nothing was injected, so every later check is vacuous")


def check_asking_for_more_than_exist_reports_what_fit():
    """The knob reports its achievement instead of clamping quietly.

    Asked for five hundred reassignments against a product master of a hundred and
    twenty, the right answer is a hundred and twenty and a visible gap. A silent
    clamp would make the manifest claim five hundred.
    """
    built, achieved = _only("reassigned_product_line", 500)
    products = len(build().products)
    if achieved != products:
        raise AssertionError(f"achieved {achieved}, expected the {products} that exist")
    if built.defects["requested"]["reassigned_product_line"] != 500:
        raise AssertionError("the request was rewritten to match what fit")
    if built.defects["achieved"]["reassigned_product_line"] >= 500:
        raise AssertionError("the achieved count claims more than the population holds")


def check_an_unknown_injector_name_is_refused_by_message():
    """Asserted on the message rather than the exception type.

    A typo in a knob has to be loud. `ValueError` alone would also be raised by
    something else in here one day, so the check reads the text and confirms it
    names the key that was wrong.
    """
    built = build()
    try:
        discrepancies.apply(built, {"orphan_credits": 3})
    except ValueError as err:
        if "orphan_credits" not in str(err):
            raise AssertionError(f"the refusal does not name the bad key: {err}")
        return
    raise AssertionError("a misspelled injector name was accepted")


def check_an_orphan_credit_points_at_a_line_that_is_not_there():
    built, achieved = _only("orphan_credit", 9)
    line_ids = {line["line_id"] for line in built.lines}
    invoice_ids = {invoice["invoice_id"] for invoice in built.invoices}
    orphans = [c for c in built.credits if c["line_id"] not in line_ids]
    if len(orphans) != achieved:
        raise AssertionError(f"{achieved} reported and {len(orphans)} rows have a dead line")
    harsher = [c for c in orphans if c["invoice_id"] not in invoice_ids]
    if not harsher:
        raise AssertionError(
            "no orphan lost its invoice reference, so the unmatched counter in the "
            "lag measurement cannot ever be non zero"
        )


def check_a_duplicate_credit_shares_the_vendor_document_number():
    built, achieved = _only("duplicate_credit", 12)
    seen: dict[str, list[str]] = {}
    for credit in built.credits:
        seen.setdefault(credit["vendor_doc_no"], []).append(credit["credit_id"])
    doubled = {doc: ids for doc, ids in seen.items() if len(ids) > 1}
    if len(doubled) != achieved:
        raise AssertionError(f"{achieved} reported and {len(doubled)} documents appear twice")
    ids = [i for pair in doubled.values() for i in pair]
    if len(set(ids)) != len(ids):
        raise AssertionError("a duplicate reuses an internal credit id, which it must not")


def check_a_cents_scaled_amount_is_exactly_a_hundred_times_the_clean_one():
    """Compared against the clean corpus rather than against a plausible size.

    An amount that merely looks too large would also match the overapplied
    injector. A factor of exactly one hundred against the same credit id is the
    only evidence that separates the two.
    """
    clean = {c["credit_id"]: c["amount_usd"] for c in build().credits}
    built, achieved = _only("cents_scaled_amount", 6)
    scaled = 0
    for credit in built.credits:
        before = clean.get(credit["credit_id"])
        if before is None or before == credit["amount_usd"]:
            continue
        if cents(credit["amount_usd"]) == cents(before) * 100:
            scaled += 1
        else:
            raise AssertionError(
                f"{credit['credit_id']} moved from {before} to {credit['amount_usd']}, "
                "which is not a unit error"
            )
    if scaled != achieved:
        raise AssertionError(f"{achieved} reported and {scaled} are a hundred times over")


def check_an_overapplied_credit_exceeds_the_line_it_adjusts():
    built, achieved = _only("overapplied_credit", 14)
    lines = {line["line_id"]: line for line in built.lines}
    over = 0
    for credit in built.credits:
        line = lines.get(credit["line_id"])
        if line is None:
            continue
        unit = line["unit_cost_usd"] if credit["applies_to"] == "cost" else line["unit_price_usd"]
        if cents(credit["amount_usd"]) > extended(unit, line["qty"]):
            over += 1
    if over != achieved:
        raise AssertionError(f"{achieved} reported and {over} exceed their line")


def check_a_missing_partner_leaves_an_empty_reference():
    built, achieved = _only("missing_partner", 31)
    blank = [i for i in built.invoices if i["partner_id"] == ""]
    if len(blank) != achieved:
        raise AssertionError(f"{achieved} reported and {len(blank)} invoices are blank")


def check_a_reassigned_product_gains_a_second_row_on_a_later_date():
    built, achieved = _only("reassigned_product_line", 4)
    rows: dict[str, list[dict]] = {}
    for product in built.products:
        rows.setdefault(product["product_id"], []).append(product)
    doubled = {pid: entries for pid, entries in rows.items() if len(entries) > 1}
    if len(doubled) != achieved:
        raise AssertionError(f"{achieved} reported and {len(doubled)} products have two rows")
    for pid, entries in doubled.items():
        dates = sorted(e["effective_from"] for e in entries)
        if dates[0] == dates[1]:
            raise AssertionError(f"{pid} has two rows effective on the same date")
        lines = {e["product_line"] for e in entries}
        if len(lines) != 2:
            raise AssertionError(f"{pid} was reassigned to the line it was already on")
        if not lines.issubset(set(PRODUCT_LINES)):
            raise AssertionError(f"{pid} was moved to a line that does not exist")


def check_a_clean_corpus_holds_none_of_them():
    """The control. Without it every count above could be measuring the generator."""
    built = corpus(False)
    rows: dict[str, int] = {}
    for product in built.products:
        rows[product["product_id"]] = rows.get(product["product_id"], 0) + 1
    if max(rows.values()) != 1:
        raise AssertionError("the clean corpus already holds a reassigned product")
    if any(invoice["partner_id"] == "" for invoice in built.invoices):
        raise AssertionError("the clean corpus already holds an invoice with no partner")
    docs = [credit["vendor_doc_no"] for credit in built.credits]
    if len(set(docs)) != len(docs):
        raise AssertionError("the clean corpus already holds a duplicated vendor document")
