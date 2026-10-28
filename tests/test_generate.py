"""The generator, on the properties a reader would want before trusting the rows."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import tempfile
from pathlib import Path

from gross_margin import calendar as cal
from gross_margin import discrepancies
from gross_margin.generate import WINDOW_END, WINDOW_START, build, write
from gross_margin.money import cents
from gross_margin.registry import ARTIFACTS
from tests.corpus_fixture import corpus

MONEY = re.compile(r"^-?\d+\.\d{2}$")

# Anything naming the delay the generator drew. The draw is thrown away on
# purpose, and a column carrying it would let a downstream model recover the
# answer from the data instead of measuring it.
LEAKED_NAMES = ("delay", "intent", "lognormal", "sigma", "drawn", "lag")


def _digests(built, tmp: str) -> dict[str, str]:
    out = Path(tmp)
    write(built, out)
    return {
        name: hashlib.sha256((out / name).read_bytes()).hexdigest() for name in ARTIFACTS
    }


def check_a_clean_build_reproduces_byte_for_byte():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        first = _digests(build(), a)
        second = _digests(build(), b)
        moved = sorted(name for name in first if first[name] != second[name])
        if moved:
            raise AssertionError(f"two clean builds at one seed disagree on {moved}")


def check_the_discrepancy_pass_leaves_the_untouched_tables_alone():
    """The injectors run after the draw, so a clean table must be bit identical.

    This is the check that makes the whole arrangement worth the extra pass. If a
    defect were written inside the draw, every later random value would shift and
    the four tables below would move for no reason connected to the defect.
    """
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        clean = _digests(build(), a)
        dirty_corpus = build()
        discrepancies.apply(dirty_corpus)
        dirty = _digests(dirty_corpus, b)

    untouched = ("vendors.csv", "partners.csv", "plans.csv", "invoice_lines.csv")
    for name in untouched:
        if clean[name] != dirty[name]:
            raise AssertionError(f"{name} moved and no injector writes to it")
    touched = ("products.csv", "invoices.csv", "credit_memos.csv")
    unmoved = sorted(name for name in touched if clean[name] == dirty[name])
    if unmoved:
        raise AssertionError(
            f"{unmoved} should have been edited by an injector and did not move, "
            "so the injection silently did nothing"
        )


def check_no_output_column_names_the_drawn_delay():
    for name, header in ARTIFACTS.items():
        for column in header:
            lowered = column.lower()
            for leak in LEAKED_NAMES:
                if leak in lowered:
                    raise AssertionError(
                        f"{name} publishes a column called {column}, which names the "
                        "delay the generator drew rather than something observable"
                    )


def check_every_amount_is_two_decimal_places():
    built = corpus(False)
    checked = 0
    for line in built.lines:
        for column in ("unit_price_usd", "unit_cost_usd"):
            if not MONEY.match(line[column]):
                raise AssertionError(f"{line['line_id']} {column} is {line[column]}")
            checked += 1
    for credit in built.credits:
        if not MONEY.match(credit["amount_usd"]):
            raise AssertionError(f"{credit['credit_id']} amount is {credit['amount_usd']}")
        checked += 1
    if checked < 10_000:
        raise AssertionError(f"only {checked} amounts were checked, the corpus looks empty")


def check_a_clean_line_carries_a_positive_margin_before_any_credit():
    """Otherwise a negative margin later cannot be attributed to a credit."""
    built = corpus(False)
    bad = []
    for line in built.lines:
        price = cents(line["unit_price_usd"])
        cost = cents(line["unit_cost_usd"])
        if price <= cost:
            bad.append(line["line_id"])
    if bad:
        raise AssertionError(f"{len(bad)} clean lines price at or below cost, first {bad[:3]}")


def check_every_invoice_falls_on_a_business_day_inside_the_window():
    built = corpus(False)
    for invoice in built.invoices:
        day = dt.date.fromisoformat(invoice["invoice_date"])
        if not WINDOW_START <= day <= WINDOW_END:
            raise AssertionError(f"{invoice['invoice_id']} is dated {day}, outside the window")
        if not cal.is_business_day(day):
            raise AssertionError(f"{invoice['invoice_id']} is dated {day}, not a business day")
        if invoice["period"] != cal.period_of(day):
            raise AssertionError(f"{invoice['invoice_id']} period disagrees with its date")


def check_no_credit_posts_after_the_window_closes():
    built = corpus(False)
    for credit in built.credits:
        posted = dt.date.fromisoformat(credit["posted_date"])
        if posted > WINDOW_END:
            raise AssertionError(f"{credit['credit_id']} posted on {posted}, past the window")
        if dt.date.fromisoformat(credit["raised_date"]) > posted:
            raise AssertionError(f"{credit['credit_id']} posted before it was raised")


def check_the_window_actually_censors_some_credits():
    """A censoring count of zero would make the tail quantiles complete.

    They are not complete, and a reader has to be told. If this ever comes back
    zero the README sentence about the tail being a lower bound is wrong and has to
    go rather than be softened.
    """
    built = corpus(False)
    if built.censored_credits <= 0:
        raise AssertionError(
            "no credit fell past the end of the window, so the observable "
            "distribution is not censored and the README says it is"
        )


def check_a_credit_posting_date_respects_the_calendar():
    built = corpus(False)
    for credit in built.credits[:500]:
        posted = dt.date.fromisoformat(credit["posted_date"])
        if not cal.is_business_day(posted):
            raise AssertionError(f"{credit['credit_id']} posted on a non business day")
        if cal.is_frozen(posted):
            raise AssertionError(f"{credit['credit_id']} posted inside the close freeze")
