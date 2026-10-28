"""Generates the billing, partner and plan corpus this project reads.

Three rules shaped this file.

The generator owns the process and not the answer. Nothing here writes down the
number a downstream model is supposed to recover. The credit lag is drawn per
credit and the draw is thrown away, so the only lag anybody can measure is the one
visible in the emitted dates. That is also the only lag reconciliation will ever
see, which is the point.

The clean corpus is generated first and the discrepancies are a separate pass over
the finished rows. Each entity kind draws from its own stream, so adding a defect
cannot shift a draw somewhere else and a clean build reproduces byte for byte.

Money is integer cents everywhere inside this module. The CSV carries dollars with
two decimals, formatted from the integer. Nothing here adds floats together.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from gross_margin import calendar as cal
from gross_margin.money import extended, fmt_usd
from gross_margin.registry import ARTIFACTS, MANIFEST

WINDOW_START = dt.date(2024, 7, 1)
WINDOW_END = dt.date(2026, 6, 30)

PRODUCT_LINES = (
    "networking",
    "storage",
    "endpoint",
    "security-software",
    "support-contracts",
    "cabling",
)
REGIONS = ("northeast", "southeast", "midwest", "west")
SEGMENTS = ("enterprise", "mid-market", "public-sector")
CHANNELS = ("direct", "partner-led", "marketplace")
REBATE_PROGRAMS = ("quarterly-volume", "annual-volume", "none")


@dataclass(frozen=True)
class CreditKind:
    """One kind of credit memo and the shape of its arrival.

    `median_days` and `sigma` are a design choice rather than a measurement. A
    lognormal is the usual shape for a settlement delay because it cannot go
    negative and it has a long right tail, which is what a disputed claim looks
    like. The numbers are what the finance teams I have worked with described, and
    the README says so at the point of use. What matters for this project is not
    whether they are right in general. It is that they differ enough between kinds
    that one lookback window cannot serve all three.

    `anchor` is the part that carries the real behaviour. Price protection and
    returns are raised against the invoice. A volume rebate cannot be raised until
    the quarter it belongs to has closed, so its distance from the invoice depends
    on where in the quarter the invoice happened to sit.
    """

    name: str
    applies_to: str
    anchor: str
    median_days: float
    sigma: float
    share_of_lines: float
    rate_lo: float
    rate_hi: float


CREDIT_KINDS = (
    CreditKind("price_protection", "cost", "invoice", 9.0, 0.45, 0.07, 0.02, 0.08),
    CreditKind("return_credit", "revenue", "invoice", 21.0, 0.55, 0.06, 0.15, 1.00),
    CreditKind("volume_rebate", "cost", "quarter_end", 30.0, 0.40, 0.05, 0.01, 0.04),
)

INVOICES_PER_DAY = (8, 18)
LINES_PER_INVOICE = (1, 5)


def _rng(seed: int, stream: str) -> random.Random:
    """A separate generator per stream so passes stay independent.

    Seeding on a string keeps the derivation readable in a traceback. Deriving by
    arithmetic on the seed would collide the moment two streams were one apart.
    """
    return random.Random(f"{seed}:{stream}")


def quarter_end(day: dt.date) -> dt.date:
    last_month_of_quarter = ((day.month - 1) // 3) * 3 + 3
    return cal.period_end(dt.date(day.year, last_month_of_quarter, 1))


def business_days(start: dt.date, end: dt.date) -> list[dt.date]:
    out = []
    cursor = start
    while cursor <= end:
        if cal.is_business_day(cursor):
            out.append(cursor)
        cursor += dt.timedelta(days=1)
    return out


@dataclass
class Corpus:
    seed: int
    vendors: list[dict] = field(default_factory=list)
    partners: list[dict] = field(default_factory=list)
    products: list[dict] = field(default_factory=list)
    plans: list[dict] = field(default_factory=list)
    invoices: list[dict] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)
    credits: list[dict] = field(default_factory=list)
    censored_credits: int = 0
    defects: dict[str, int] = field(default_factory=dict)

    def tables(self) -> dict[str, list[dict]]:
        return {
            "vendors.csv": self.vendors,
            "partners.csv": self.partners,
            "products.csv": self.products,
            "plans.csv": self.plans,
            "invoices.csv": self.invoices,
            "invoice_lines.csv": self.lines,
            "credit_memos.csv": self.credits,
        }


def _vendors(seed: int, n: int) -> list[dict]:
    rng = _rng(seed, "vendors")
    rows = []
    for i in range(1, n + 1):
        rows.append(
            {
                "vendor_id": f"V{i:03d}",
                "vendor_name": f"Vendor {i:03d}",
                "rebate_program": rng.choice(REBATE_PROGRAMS),
            }
        )
    return rows


def _partners(seed: int, n: int) -> list[dict]:
    rng = _rng(seed, "partners")
    rows = []
    for i in range(1, n + 1):
        rows.append(
            {
                "partner_id": f"P{i:04d}",
                "partner_name": f"Partner {i:04d}",
                "region": rng.choice(REGIONS),
                "segment": rng.choice(SEGMENTS),
            }
        )
    return rows


def _products(seed: int, n: int, vendors: list[dict]) -> list[dict]:
    rng = _rng(seed, "products")
    rows = []
    for i in range(1, n + 1):
        rows.append(
            {
                "product_id": f"SKU{i:05d}",
                "product_line": rng.choice(PRODUCT_LINES),
                "vendor_id": rng.choice(vendors)["vendor_id"],
                # One row per product in a clean corpus. The reassignment defect
                # adds a second row with a later date, which is what turns this
                # into a dimension somebody has to think about.
                "effective_from": WINDOW_START.isoformat(),
            }
        )
    return rows


def _plans(seed: int, partners: list[dict]) -> list[dict]:
    rng = _rng(seed, "plans")
    rows = []
    for partner in partners:
        rows.append(
            {
                "partner_id": partner["partner_id"],
                "plan_code": rng.choice(("GOLD", "SILVER", "BRONZE", "FLAT")),
                "rebate_rate_pct": f"{rng.uniform(0.5, 4.5):.2f}",
                "effective_from": WINDOW_START.isoformat(),
            }
        )
    return rows


def _invoices_and_lines(
    seed: int, partners: list[dict], products: list[dict]
) -> tuple[list[dict], list[dict]]:
    head_rng = _rng(seed, "invoices")
    line_rng = _rng(seed, "lines")
    invoices: list[dict] = []
    lines: list[dict] = []
    invoice_no = 0
    line_no = 0
    for day in business_days(WINDOW_START, WINDOW_END):
        # Volume is higher in the last week of a quarter, which is the one seasonal
        # effect in here and it is deliberate. It gives the rebate anchor something
        # to bite on.
        base = head_rng.randint(*INVOICES_PER_DAY)
        if (quarter_end(day) - day).days <= 7:
            base = int(base * 1.6)
        for _ in range(base):
            invoice_no += 1
            partner = head_rng.choice(partners)
            invoice = {
                "invoice_id": f"INV{invoice_no:07d}",
                "invoice_date": day.isoformat(),
                "period": cal.period_of(day),
                "partner_id": partner["partner_id"],
                "channel": head_rng.choice(CHANNELS),
            }
            invoices.append(invoice)
            for _ in range(line_rng.randint(*LINES_PER_INVOICE)):
                line_no += 1
                product = line_rng.choice(products)
                qty = line_rng.randint(1, 40)
                cost_cents = line_rng.randint(1_200, 180_000)
                # Margin sits between 6 and 34 percent before any credit. Thin, the
                # way distribution actually is, so a late credit can move a product
                # line from profitable to not.
                markup = line_rng.uniform(1.06, 1.34)
                price_cents = int(round(cost_cents * markup))
                lines.append(
                    {
                        "line_id": f"L{line_no:08d}",
                        "invoice_id": invoice["invoice_id"],
                        "product_id": product["product_id"],
                        "qty": str(qty),
                        "unit_price_usd": fmt_usd(price_cents),
                        "unit_cost_usd": fmt_usd(cost_cents),
                    }
                )
    return invoices, lines


def _credits(
    seed: int, invoices: list[dict], lines: list[dict]
) -> tuple[list[dict], int]:
    """One credit memo per selected line, with its arrival resolved by the calendar.

    The drawn delay is local to this function and never leaves it. Everything
    downstream has `raised_date` and `posted_date` and has to work from those.
    """
    rng = _rng(seed, "credits")
    by_invoice = {inv["invoice_id"]: inv for inv in invoices}
    out: list[dict] = []
    censored = 0
    doc_no = 0
    for line in lines:
        invoice_date = dt.date.fromisoformat(by_invoice[line["invoice_id"]]["invoice_date"])
        for kind in CREDIT_KINDS:
            if rng.random() >= kind.share_of_lines:
                continue
            anchor = invoice_date if kind.anchor == "invoice" else quarter_end(invoice_date)
            delay = rng.lognormvariate(math.log(kind.median_days), kind.sigma)
            raised = anchor + dt.timedelta(days=int(round(delay)))
            posted = cal.posting_date(raised)
            if posted > WINDOW_END:
                # The corpus ends and some credits have not arrived. They are not
                # written, which is what makes the observable distribution a
                # censored one. The count is published so nobody reads a tail
                # quantile as the whole truth.
                censored += 1
                continue
            unit = line["unit_cost_usd"] if kind.applies_to == "cost" else line["unit_price_usd"]
            extended_cents = extended(unit, line["qty"])
            amount_cents = int(round(extended_cents * rng.uniform(kind.rate_lo, kind.rate_hi)))
            doc_no += 1
            out.append(
                {
                    "credit_id": f"CM{doc_no:07d}",
                    "vendor_doc_no": f"VD-{doc_no:07d}",
                    "invoice_id": line["invoice_id"],
                    "line_id": line["line_id"],
                    "credit_type": kind.name,
                    "applies_to": kind.applies_to,
                    "amount_usd": fmt_usd(max(amount_cents, 1)),
                    "raised_date": raised.isoformat(),
                    "posted_date": posted.isoformat(),
                    "period": cal.period_of(posted),
                }
            )
    return out, censored


def build(
    seed: int = 20260428,
    vendors: int = 14,
    partners: int = 40,
    products: int = 120,
) -> Corpus:
    """The clean corpus. No discrepancies, no defects, nothing injected."""
    corpus = Corpus(seed=seed)
    corpus.vendors = _vendors(seed, vendors)
    corpus.partners = _partners(seed, partners)
    corpus.products = _products(seed, products, corpus.vendors)
    corpus.plans = _plans(seed, corpus.partners)
    corpus.invoices, corpus.lines = _invoices_and_lines(
        seed, corpus.partners, corpus.products
    )
    corpus.credits, corpus.censored_credits = _credits(
        seed, corpus.invoices, corpus.lines
    )
    return corpus


def write(corpus: Corpus, out_dir: Path) -> dict[str, str]:
    """Write every registered artifact and return a digest per file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    for name, rows in corpus.tables().items():
        header = ARTIFACTS[name]
        path = out_dir / name
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(header), lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()

    manifest = {
        "seed": corpus.seed,
        "window": {"start": WINDOW_START.isoformat(), "end": WINDOW_END.isoformat()},
        "rows": {name: len(rows) for name, rows in corpus.tables().items()},
        "censored_credits": corpus.censored_credits,
        "injected": corpus.defects,
        "digests": digests,
    }
    manifest_path = out_dir / MANIFEST
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    digests[MANIFEST] = "not self describing"
    return digests
