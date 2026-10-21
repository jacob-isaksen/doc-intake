"""Business-rule checks that run after the schema check passes.

The schema only proves the JSON has the right shape. These rules check that
the numbers make sense, which is where an LLM (or the paperwork itself) can be
quietly wrong. Anything that trips a rule gets flagged for a human to review.
"""
from .config import COMPANY
from .schema import ExtractedDoc

TOLERANCE = 0.011  # a cent of rounding slack
MIN_YEAR, MAX_YEAR = 2020, 2030


def check(doc: ExtractedDoc) -> list[str]:
    flags: list[str] = []

    if doc.doc_date is None:
        flags.append("missing date")
    elif not MIN_YEAR <= doc.doc_date.year <= MAX_YEAR:
        flags.append(f"date out of range ({doc.doc_date})")

    if doc.counterparty.strip().casefold() == COMPANY.casefold():
        flags.append("counterparty is our own company")

    if not doc.line_items:
        flags.append("no line items")

    for i, item in enumerate(doc.line_items, start=1):
        if None not in (item.quantity, item.unit_price, item.amount):
            if abs(item.quantity * item.unit_price - item.amount) > TOLERANCE:
                flags.append(f"line {i}: quantity x price does not equal amount")

    if doc.doc_type == "bill_of_lading":
        weights = [i.weight_lb for i in doc.line_items if i.weight_lb is not None]
        if doc.total_weight_lb is not None and weights:
            if abs(sum(weights) - doc.total_weight_lb) > 0.5:
                flags.append("total weight does not equal sum of line weights")
        return flags

    if doc.total is None:
        flags.append("missing total")
        return flags

    amounts = [i.amount for i in doc.line_items if i.amount is not None]
    if len(amounts) != len(doc.line_items):
        flags.append("a line item is missing its amount")
        return flags

    lines_sum = sum(amounts)
    if doc.subtotal is not None and abs(doc.subtotal - lines_sum) > TOLERANCE:
        flags.append("subtotal does not equal sum of lines")

    base = doc.subtotal if doc.subtotal is not None else lines_sum
    if abs(base + (doc.tax or 0) - doc.total) > TOLERANCE:
        flags.append("total does not equal subtotal plus tax")

    return flags
