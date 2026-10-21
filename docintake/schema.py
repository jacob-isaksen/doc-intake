"""The shape every extracted document must have.

Pydantic checks the LLM's JSON against these models. If the model returns
something that doesn't fit (a missing field, a string where a number belongs),
validation fails and the document is marked "failed" instead of being saved.
"""
from datetime import date
from typing import Literal

from pydantic import BaseModel

DocType = Literal["invoice", "bill_of_lading", "work_order"]


class LineItem(BaseModel):
    description: str
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None
    weight_lb: float | None = None


class ExtractedDoc(BaseModel):
    doc_type: DocType
    doc_number: str
    doc_date: date | None = None
    counterparty: str
    equipment: str | None = None
    line_items: list[LineItem]
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    total_weight_lb: float | None = None
