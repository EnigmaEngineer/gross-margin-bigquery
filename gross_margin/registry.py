"""The list of files a corpus is made of.

Two things read this. The generator writes exactly these and nothing else, and a
check asserts that the generator names every one of them. That check exists
because a file can enter a manifest without entering the thing that builds it,
and a suite running where the data already sits cannot tell.
"""

from __future__ import annotations

# name -> the header row, in the order the generator writes it. The header is here
# rather than in the writer so that a check can compare the two.
ARTIFACTS: dict[str, tuple[str, ...]] = {
    "vendors.csv": ("vendor_id", "vendor_name", "rebate_program"),
    "partners.csv": ("partner_id", "partner_name", "region", "segment"),
    "products.csv": ("product_id", "product_line", "vendor_id", "effective_from"),
    "plans.csv": ("partner_id", "plan_code", "rebate_rate_pct", "effective_from"),
    "invoices.csv": ("invoice_id", "invoice_date", "period", "partner_id", "channel"),
    "invoice_lines.csv": (
        "line_id",
        "invoice_id",
        "product_id",
        "qty",
        "unit_price_usd",
        "unit_cost_usd",
    ),
    "credit_memos.csv": (
        "credit_id",
        "vendor_doc_no",
        "invoice_id",
        "line_id",
        "credit_type",
        "applies_to",
        "amount_usd",
        "raised_date",
        "posted_date",
        "period",
    ),
}

# The corpus manifest. Not a table, so it is listed separately rather than given a
# fake header.
MANIFEST = "corpus.json"


def expected_files() -> tuple[str, ...]:
    return tuple(sorted(ARTIFACTS)) + (MANIFEST,)
