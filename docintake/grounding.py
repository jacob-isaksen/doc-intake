"""Grounding check: every value the model extracted must actually appear in the document.

A model can misread a digit ($26.94 -> $24.94) or invent a value (a date on a blank
line). Both leave the same fingerprint: the value is not in the source text. That is
cheap to test, so we test it. Anything we cannot find gets sent back to the model once
for a second look, and flagged for a human if it is still missing.
"""
import re
from datetime import date

from .schema import ExtractedDoc

NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
TOLERANCE = 0.005
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _numbers_in(text: str) -> list[float]:
    return [float(token.replace(",", "")) for token in NUMBER.findall(text)]


def _squash(text: str) -> str:
    """Case, spacing and . , punctuation don't matter when asking 'is this text present?'"""
    return " ".join(text.casefold().replace(".", "").replace(",", "").split())


def _date_spellings(d: date) -> list[str]:
    month = MONTHS[d.month - 1]
    return [
        d.isoformat(),
        f"{month} {d.day}, {d.year}",
        f"{month} {d.day:02d}, {d.year}",
        f"{d.month:02d}/{d.day:02d}/{d.year}",
        f"{d.month}/{d.day}/{d.year}",
    ]


def find_ungrounded(doc: ExtractedDoc, text: str) -> list[str]:
    """Return a human-readable list of extracted values that cannot be found in the text."""
    issues: list[str] = []
    numbers = _numbers_in(text)
    squashed = _squash(text)

    def number_present(value: float) -> bool:
        return value == 0 or any(abs(value - n) <= TOLERANCE for n in numbers)

    def check_number(label: str, value: float | None) -> None:
        if value is not None and not number_present(value):
            issues.append(f"{label} {value:g}")

    def check_text(label: str, value: str | None) -> None:
        if value and _squash(value) not in squashed:
            issues.append(f'{label} "{value}"')

    check_text("document number", doc.doc_number)
    check_text("counterparty", doc.counterparty)
    check_text("equipment", doc.equipment)

    if doc.doc_date is not None and not any(s in text for s in _date_spellings(doc.doc_date)):
        issues.append(f"date {doc.doc_date.isoformat()}")

    for n, item in enumerate(doc.line_items, start=1):
        check_text(f"line {n} description", item.description)
        check_number(f"line {n} quantity", item.quantity)
        check_number(f"line {n} unit price", item.unit_price)
        check_number(f"line {n} amount", item.amount)
        check_number(f"line {n} weight", item.weight_lb)

    for label, value in (("subtotal", doc.subtotal), ("tax", doc.tax), ("total", doc.total),
                         ("total weight", doc.total_weight_lb)):
        check_number(label, value)
    return issues
